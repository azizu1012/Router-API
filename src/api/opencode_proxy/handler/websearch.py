"""Web search injection for OpenCode proxy.

Detects search intent from user messages, executes DuckDuckGo
search, and injects the context block into the last user message.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger


def get_auth_key_prefix(account: Optional[Dict[str, Any]]) -> str:
    if not account:
        return ""
    ak = account.get("auth_key") or ""
    return ak[-8:] if len(ak) >= 8 else ak


VALID_ENGINES = {"auto", "google_grounding", "duckduckgo", "disabled"}

def resolve_search_engine(body: Dict[str, Any], account: Optional[Dict[str, Any]]) -> str:
    """Resolve effective search engine from request body override or account config.
    
    Body override takes precedence. Falls back to account setting, then 'auto'.
    """
    body_engine = (body.get("search_engine") or "").strip().lower()
    if body_engine in VALID_ENGINES:
        return body_engine
    if account:
        acct_engine = (account.get("search_engine") or "").strip().lower()
        if acct_engine in VALID_ENGINES:
            return acct_engine
    return "auto"

def should_enable_web_search(body: Dict[str, Any], account: Optional[Dict[str, Any]]) -> bool:
    """Check if web search should be enabled for this request.

    Respects explicit client-level disable flags.
    """
    engine = resolve_search_engine(body, account)
    if engine == "disabled":
        return False
    for flag in ["web_search", "search", "google_search", "grounding"]:
        if flag in body and body[flag] is False:
            return False
    return bool(
        body.get("web_search") is True
        or body.get("search") is True
        or body.get("google_search") is True
        or body.get("grounding") is True
    )


async def with_search_context(
    body: Dict[str, Any],
    messages: List[Dict[str, Any]],
    model_alias: str,
    account: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Detect search intent, execute search, and inject results into the last user message.

    Returns the modified message list (unchanged if no search needed).
    """
    if not messages:
        return messages
    if not should_enable_web_search(body, account):
        return messages

    prompt_text = _extract_prompt_text(messages)
    if not prompt_text.strip():
        return messages

    akp = get_auth_key_prefix(account)
    last_user_msg = _extract_last_user_message(messages)
    queries: List[str] = []

    if last_user_msg:
        try:
            from src.core.providers.search_manager import extract_search_queries
            queries = await extract_search_queries(last_user_msg, messages, auth_key_prefix=akp, account=account)
        except Exception as qerr:
            logger.warning("[OpenCode Search] extract_search_queries failed: %s", qerr)

    if not queries:
        return messages

    try:
        from .search import execute_opencode_search
        se = resolve_search_engine(body, account)
        search_context, citations = await execute_opencode_search(
            queries, model_alias_or_name=model_alias, search_engine=se, auth_key_prefix=akp, account=account,
        )
    except Exception as serr:
        logger.warning("[OpenCode Search] execute_opencode_search failed: %s", serr)
        return messages

    if not search_context:
        return messages

    citations_block = _format_citations(citations)
    context_block = _build_context_block(search_context, citations_block)

    return _inject_into_last_user(messages, context_block)


def _extract_prompt_text(messages: List[Dict[str, Any]]) -> str:
    chunks: List[str] = []
    for m in messages:
        if m.get("role") in ("system", "developer"):
            continue
        c = m.get("content", "")
        if isinstance(c, str) and c.strip():
            chunks.append(c)
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and p.get("type") == "text":
                    text = str(p.get("text", "")).strip()
                    if text:
                        chunks.append(text)
    return "\n".join(chunks)


def _extract_last_user_message(messages: List[Dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                texts = [str(p.get("text", "")) for p in c if isinstance(p, dict) and p.get("type") == "text"]
                return "\n".join(texts)
    return ""


def _format_citations(citations: list) -> str:
    if not citations:
        return ""
    seen = set()
    unique = []
    for c in citations:
        url = c.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(c)
    if not unique:
        return ""
    return "\n\n— Sources —\n" + "\n".join(
        f"• [{c.get('title', 'Source')}]({c.get('url', '#')})" for c in unique
    )


def _build_context_block(search_context: str, citations_block: str) -> str:
    current_time = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p")
    return (
        "\n\n---\n"
        f"[Web Search Context — {current_time}]\n"
        "CRITICAL: Use the search results above to write a comprehensive, detailed response. "
        "Include all relevant dates, numbers, names. Do NOT summarize briefly — be exhaustive.\n"
        f"{search_context}\n"
        f"{citations_block}\n"
        "[/Web Search Context]"
    )


def _inject_into_last_user(messages: List[Dict[str, Any]], context_block: str) -> List[Dict[str, Any]]:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            existing = messages[i].get("content", "")
            if isinstance(existing, str):
                messages[i] = {**messages[i], "content": existing + context_block}
            elif isinstance(existing, list):
                messages[i] = {**messages[i], "content": list(existing) + [{"type": "text", "text": context_block}]}
            return messages
    messages.insert(0, {"role": "system", "content": context_block.strip()})
    return messages

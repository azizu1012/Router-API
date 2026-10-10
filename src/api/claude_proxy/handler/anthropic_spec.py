"""Anthropic Messages API protocol helpers.

Single source of truth for translating between the Anthropic wire format and the
OpenAI-shaped structures that PoolManager produces. Used by both the streaming and
non-streaming Claude proxy paths so the two never drift apart.

Covered here:
- Request params: stop_sequences, top_p, top_k, tool_choice, metadata
- Response: stop_reason / stop_sequence mapping (incl. refusal + stop_sequence)
- Usage accounting: cache_control breakpoints -> cache_creation/cache_read tokens
- Hosted web search: running it, the declared limits, and the blocks the client
  sees. The search itself lived inline in each proxy path, and the two copies had
  already drifted — only the non-streaming one read `max_uses`, only one of them
  kept the citations, and only one of them filtered blocked domains.
"""

import hashlib
from typing import Any, Dict, List, Optional, Tuple

from src.core.config_n_logg.logger import logger_proxy as logger

# Anthropic sends `content_filter` from Gemini's safety blocks.
_REFUSAL_FINISH_REASONS = {"content_filter", "safety", "recitation", "blocklist", "prohibited_content"}

# Anthropic's minimum cacheable prompt prefix. Below this a cache_control marker
# is a no-op, so we must not report cache tokens either.
MIN_CACHEABLE_TOKENS = 1024


# ── Request params ──────────────────────────────────────────────────────────

def extract_sampling_params(body: Dict[str, Any]) -> Dict[str, Any]:
    """Pull Anthropic sampling params out of the request body.

    Gemini accepts stop_sequences / top_p / top_k but the facade never received
    them, so every one of these was silently dropped before.

    Returns a dict suitable for spreading into `acompletion` kwargs.
    """
    out: Dict[str, Any] = {}

    stops = body.get("stop_sequences")
    if isinstance(stops, list):
        cleaned = [str(s) for s in stops if isinstance(s, str) and s]
        if cleaned:
            out["stop_sequences"] = cleaned

    top_p = body.get("top_p")
    if isinstance(top_p, (int, float)) and not isinstance(top_p, bool):
        out["top_p"] = max(0.0, min(1.0, float(top_p)))

    top_k = body.get("top_k")
    if isinstance(top_k, int) and not isinstance(top_k, bool) and top_k > 0:
        out["top_k"] = top_k

    # tool_choice rides the same channel. It was previously read only to strip
    # the tools list on `none`; nothing ever told the provider which mode the
    # client asked for, so `any` and a forced tool were both no-ops.
    choice = extract_tool_choice(body)
    if choice:
        out.update(choice)

    return out


def extract_tool_choice(body: Dict[str, Any]) -> Dict[str, Any]:
    """Translate Anthropic `tool_choice` into the canonical OpenAI spelling.

    OpenAI is the internal shape, so this converts rather than passing through:
    Anthropic says `any` where OpenAI says `required`, and names a tool with
    `{"type": "tool", "name": ...}` where OpenAI nests it under `function`.
    Forwarding the Anthropic spelling is a 400 on every other provider.

    Anthropic values:
      {"type": "auto"}                       -> "auto"
      {"type": "any"}                        -> "required"
      {"type": "none"}                       -> drop tools entirely
      {"type": "tool", "name": "X"}          -> forced X
    """
    tc = body.get("tool_choice")
    if not isinstance(tc, dict):
        return {}

    kind = tc.get("type")
    disable_parallel = bool(tc.get("disable_parallel_tool_use"))
    out: Dict[str, Any] = {}

    if kind == "auto":
        out["tool_choice"] = "auto"
    elif kind == "any":
        out["tool_choice"] = "required"
    elif kind == "none":
        out["tool_choice"] = "none"
    elif kind == "tool":
        name = tc.get("name")
        if isinstance(name, str) and name:
            out["tool_choice"] = {"type": "function", "function": {"name": name}}
    else:
        return {}

    # Anthropic spells it inside tool_choice; OpenAI has a separate flag, and
    # Gemini has neither. Carrying it as a sibling lets each target translate
    # into whatever it does have.
    if disable_parallel:
        out["parallel_tool_calls"] = False
    return out


def apply_tool_choice_to_tools(tool_choice: Dict[str, Any], tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`tool_choice: none` means the model must not see tools at all."""
    if tool_choice.get("tool_choice") == "none":
        return []
    return tools


# ── Response: stop_reason ───────────────────────────────────────────────────

def map_stop_reason(
    finish_reason: Optional[str],
    has_tool_calls: bool = False,
    stop_sequences: Optional[List[str]] = None,
    emitted_text: str = "",
) -> Tuple[str, Optional[str]]:
    """Map a provider finish_reason to the Anthropic `stop_reason` enum.

    Anthropic's full enum is:
      end_turn | max_tokens | stop_sequence | tool_use | pause_turn | refusal | model_context_window_exceeded

    Returns (stop_reason, stop_sequence).
    """
    fr = str(finish_reason or "").strip().lower()

    if fr in _REFUSAL_FINISH_REASONS:
        return "refusal", None

    # Pending tool calls outrank every other reason: the client only executes them
    # when we say `tool_use`, so reporting anything else would strand the calls and
    # deadlock the conversation.
    if has_tool_calls:
        return "tool_use", None

    if "max" in fr or "length" in fr:
        return "max_tokens", None

    # Anthropic reports stop_sequence (not end_turn) when a custom stop fired.
    matched = _match_stop_sequence(stop_sequences, emitted_text)
    if matched is not None:
        return "stop_sequence", matched

    return "end_turn", None


def _match_stop_sequence(stop_sequences: Optional[List[str]], text: str) -> Optional[str]:
    if not stop_sequences or not text:
        return None
    for seq in stop_sequences:
        if seq and seq in text:
            return seq
    return None


# ── Response: usage ─────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token estimate. Matches the len/4 heuristic used elsewhere in the repo."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def count_cache_breakpoints(body: Dict[str, Any]) -> int:
    """Count how many `cache_control` markers the client sent.

    Claude Code places these on the system prompt, on tool definitions, and on the
    last few message blocks. Each marker is a place the client expects to be able
    to reuse a cached prefix.
    """
    count = 0

    system = body.get("system")
    if isinstance(system, list):
        count += sum(1 for b in system if isinstance(b, dict) and b.get("cache_control"))

    for tool in body.get("tools") or []:
        if isinstance(tool, dict) and tool.get("cache_control"):
            count += 1

    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            count += sum(1 for b in content if isinstance(b, dict) and b.get("cache_control"))

    return count


def compute_usage(
    body: Dict[str, Any],
    total_input_tokens: int,
    output_tokens: int,
) -> Dict[str, Any]:
    """Build an Anthropic-shaped usage block.

    Gemini has no prompt cache, so we cannot report real cache hits. What we *can*
    do — and what Claude Code needs in order to render /context at all — is report
    the four required fields consistently. When the client sent cache_control
    breakpoints we attribute the stable prefix to cache_read and the remainder to
    fresh input, which is the shape a real Anthropic response has.

    Callers add `server_tool_use`, a nested object rather than a counter, so the
    value type is not `int` alone.
    """
    total = max(0, int(total_input_tokens))
    usage = {
        "input_tokens": total,
        "output_tokens": max(0, int(output_tokens)),
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }

    # Anthropic only caches prefixes above a minimum size, so a short request with
    # cache_control markers is never actually cached. Reporting cache hits there
    # would make /context claim a discount the client cannot rely on.
    if count_cache_breakpoints(body) > 0 and total >= MIN_CACHEABLE_TOKENS:
        cached = int(total * 0.75)
        usage["cache_read_input_tokens"] = cached
        usage["input_tokens"] = total - cached

    return usage


def thinking_signature(thinking_text: str) -> str:
    """Deterministic signature for a thinking block.

    Anthropic requires a non-empty signature on every thinking block. We have no
    real signature to echo back, so derive a stable one from the content.
    """
    return "gmni_" + hashlib.sha256((thinking_text or "").encode()).hexdigest()[:60]


# ── server tools ────────────────────────────────────────────────────────────
# Anthropic calls a tool the API runs itself a *server tool*, and reports it as
# a `server_tool_use` block paired with a result block. The caller never sends a
# tool_result back for these — that is the whole difference from a client
# `tool_use`. Docs: "The API executes the tool internally. You see the call and
# its result in the response, but you don't handle execution."

_SERVER_TOOL_PREFIX = "srvtoolu_"
_DEFAULT_MAX_USES = 5

_SEARCH_NAMES = ("web_search", "WebSearch")
_FETCH_NAMES = ("web_fetch", "WebFetch")


async def run_server_tool(
    name: str,
    args: Dict[str, Any],
    *,
    body: Dict[str, Any],
    account: Optional[Dict[str, Any]],
    auth_key_prefix: str,
    limits: Dict[str, Any],
    used: int,
) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    """Run one search the model asked for. Returns (result, citations, error_code).

    Both proxy paths call this, because the alternative was two copies of the
    same logic and they had drifted: the streaming copy enforced none of the
    client's declared limits and kept no citations, so a streamed search lost
    its sources while an identical non-streamed one kept them.
    """
    if name in _SEARCH_NAMES:
        if not _search_budget_left(limits, used):
            # Anthropic hands back an error result rather than searching past
            # the cap, so the model can still answer from what it has.
            return "max_uses_exceeded", [], "max_uses_exceeded"
        query = str(args.get("query") or "")
        logger.info("[ClaudeProxy] server search query=%r", query[:160])
        try:
            from src.api.opencode_proxy.handler.websearch import (
                resolve_search_engine,
            )
            from src.core.providers.search_manager import execute_hybrid_search

            engine = resolve_search_engine(body, account)
            context, citations = await execute_hybrid_search(
                [query], search_engine=engine,
                auth_key_prefix=auth_key_prefix, account=account)
        except Exception as e:
            logger.warning("[ClaudeProxy] server search failed: %s", e)
            return f"Search error: {e}", [], "unavailable"
        citations = filter_citations(citations, limits)
        if not context or not citations:
            return "No search results found.", [], None
        lines = [context]
        links = []
        for c in citations:
            if c.get("url"):
                links.append(f"- [{c.get('title') or 'Source'}]({c['url']})")
        if links:
            lines.append("\n**Sources / Citations:**\n" + "\n".join(links))
        return "\n".join(lines), citations, None

    if name in _FETCH_NAMES:
        url = str(args.get("url") or "")
        logger.info("[ClaudeProxy] server fetch url=%r", url[:200])
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            headers = {"User-Agent": "Mozilla/5.0 (Router API; +http://127.0.0.1:58100)"}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout, headers=headers) as r:
                    if r.status != 200:
                        return f"HTTP error {r.status}", [], "unavailable"
                    body_text = (await r.text())[:8000]
        except Exception as e:
            logger.warning("[ClaudeProxy] server fetch failed: %s", e)
            return f"WebFetch error: {e}", [], "unavailable"
        return body_text, [{"url": url, "title": url, "snippet": body_text[:500]}], None

    return "", [], None


def _search_budget_left(limits: Dict[str, Any], used: int) -> bool:
    """Whether another search is within the client's `max_uses`.

    A client that omitted `max_uses` gets the API default of 5, which is also
    where the recursive tool loop gives up, so the cap is a real bound either
    way rather than an open-ended loop.
    """
    cap = limits.get("max_uses")
    if cap is None:
        cap = _DEFAULT_MAX_USES
    return used < cap


def server_search_limits(body: Dict[str, Any]) -> Dict[str, Any]:
    """The web-search constraints the client declared on its tool definition.

    `WebSearchTool20250305Param` carries `max_uses`, `allowed_domains` and
    `blocked_domains`; the router runs the search itself, so these are the only
    place the declared limits can be enforced.
    """
    allowed: Optional[List[str]] = None
    blocked: Optional[List[str]] = None
    max_uses: Optional[int] = None
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        if not str(tool.get("type") or "").startswith("web_search"):
            continue
        if tool.get("allowed_domains"):
            allowed = [str(d).lower() for d in tool["allowed_domains"]]
        if tool.get("blocked_domains"):
            blocked = [str(d).lower() for d in tool["blocked_domains"]]
        if tool.get("max_uses") is not None:
            try:
                max_uses = int(tool["max_uses"])
            except (TypeError, ValueError):
                max_uses = None
        break
    return {"max_uses": max_uses, "allowed_domains": allowed,
            "blocked_domains": blocked}


def _domain_of(url: str) -> str:
    url = (url or "").lower()
    for sep in ("://",):
        if sep in url:
            url = url.split(sep, 1)[1]
    return url.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0]


def filter_citations(citations: List[Dict[str, Any]],
                     limits: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Drop citations from domains the client excluded.

    A domain restriction the router silently ignored would put results in the
    answer that the client asked the provider never to surface.
    """
    allowed = limits.get("allowed_domains")
    blocked = limits.get("blocked_domains")
    if not allowed and not blocked:
        return list(citations)
    kept = []
    for c in citations:
        if not isinstance(c, dict) or not c.get("url"):
            continue
        host = _domain_of(c["url"])
        if allowed and not any(host == d or host.endswith("." + d) for d in allowed):
            continue
        if blocked and any(host == d or host.endswith("." + d) for d in blocked):
            continue
        kept.append(c)
    return kept


def _server_tool_record(name: str, tc_id: str, args: Any, result: str,
                        citations: Optional[List[Dict[str, Any]]] = None,
                        error_code: Optional[str] = None,
                        ) -> Dict[str, Any]:
    """Record one search the router ran, for the server-tool blocks."""
    query = ""
    if isinstance(args, dict):
        query = str(args.get("query") or args.get("url") or "")
    text = result or ""
    return {
        "id": tc_id if str(tc_id).startswith(_SERVER_TOOL_PREFIX)
        else _SERVER_TOOL_PREFIX + str(tc_id),
        "name": "web_search" if name.lower().endswith("search") else "web_fetch",
        "query": query,
        "result": text,
        "citations": list(citations or []),
        "failed": bool(error_code) or _search_failed(text),
        "error_code": error_code,
    }


def _search_failed(text: str) -> bool:
    """A search that ran and matched nothing is not an error.

    Anthropic distinguishes the two: an empty `content` list means the search
    worked and found nothing; only a genuine failure produces an error object.
    """
    return (not (text or "").strip()
            or (text or "").startswith(("Search error", "WebFetch error",
                                        "Fetch error", "HTTP error")))


def _server_tool_use_block(rec: Dict[str, Any]) -> Dict[str, Any]:
    key = "query" if rec["name"] == "web_search" else "url"
    return {"type": "server_tool_use", "id": rec["id"], "name": rec["name"],
            "input": {key: rec["query"]}}


def _web_search_result_block(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The result block paired with a `server_tool_use` by `tool_use_id`.

    Each result carries the real url and title of the page, and
    `encrypted_content` holding the excerpt — which is what a later turn has to
    send back so the search stays in context.
    """
    if rec.get("failed"):
        return {"type": "web_search_tool_result", "tool_use_id": rec["id"],
                "content": {"type": "web_search_tool_result_error",
                            "error_code": rec.get("error_code") or "unavailable"}}
    citations = [c for c in (rec.get("citations") or [])
                 if isinstance(c, dict) and c.get("url")]
    if not citations:
        # Searched and matched nothing: an empty list, not an error.
        return {"type": "web_search_tool_result", "tool_use_id": rec["id"],
                "content": []}
    content = []
    for c in citations:
        # The search engines report a url and title per hit, not a per-result
        # excerpt, so the excerpt handed back is the text the model read.
        excerpt = c.get("snippet") or c.get("content") or rec.get("result") or ""
        entry = {"type": "web_search_result",
                 "url": c["url"],
                 "title": c.get("title") or c["url"],
                 "encrypted_content": excerpt}
        if c.get("page_age"):
            entry["page_age"] = c["page_age"]
        content.append(entry)
    return {"type": "web_search_tool_result", "tool_use_id": rec["id"],
            "content": content}


def _search_citations(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`web_search_result_location` citations for the answering text block.

    Citations are always on for Anthropic's web search, and they live on the text
    that used them rather than in a block of their own.
    """
    out: List[Dict[str, Any]] = []
    for rec in records:
        for c in (rec.get("citations") or []):
            if not isinstance(c, dict) or not c.get("url"):
                continue
            out.append({
                "type": "web_search_result_location",
                "url": c["url"],
                "title": c.get("title") or c["url"],
                # The router has no server-side encryption key to offer here,
                # so this stands as an opaque handle the client echoes back.
                "encrypted_index": rec["id"],
                "cited_text": (c.get("snippet") or c.get("content")
                               or rec.get("result") or "")[:150],
            })
    return out


def estimate_input_tokens(body: Dict[str, Any]) -> int:
    """Estimate the *client-visible* input tokens for a request body.

    Counts system + message text + tool schemas. This is what the client sees in
    its own context window, so it must NOT be derived from backend key accounting.
    """
    parts: List[str] = []

    system = body.get("system")
    if isinstance(system, str):
        parts.append(system)
    elif isinstance(system, list):
        parts.extend(str(b.get("text", "")) for b in system if isinstance(b, dict))

    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    parts.append(str(block.get("text", "")))
                elif btype == "tool_use":
                    parts.append(str(block.get("input", "")))
                elif btype == "tool_result":
                    c = block.get("content", "")
                    if isinstance(c, str):
                        parts.append(c)
                    elif isinstance(c, list):
                        parts.extend(str(x.get("text", "")) for x in c if isinstance(x, dict))

    for tool in body.get("tools") or []:
        if isinstance(tool, dict):
            parts.append(str(tool.get("name", "")))
            parts.append(str(tool.get("description", "")))

    return max(1, estimate_tokens("\n".join(p for p in parts if p)))


def build_error_event(err: Exception, error_type: str = "api_error") -> Dict[str, Any]:
    """Build an Anthropic-shaped error body for the SSE `error` event."""
    return {
        "type": "error",
        "error": {
            "type": error_type,
            "message": str(err)[:500] or "Internal error",
        },
    }


def log_stream_failure(model_alias: str, err: Exception) -> None:
    logger.error("[Claude Spec] stream failure model=%s: %s", model_alias, err)
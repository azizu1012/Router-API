"""Hosted web-search server-tool helpers for the Claude protocol."""

import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

from src.core.config_n_logg.logger import logger_proxy as logger



_SERVER_TOOL_PREFIX = "srvtoolu_"

_DEFAULT_MAX_USES = 5

_MAX_SMART_BUDGET_CAP = 3

# Hosted chat needs enough independent sources to synthesize a reliable answer,
# but sending every search excerpt back on every recursive turn can multiply the
# context. These are presentation limits; the ranking/evidence pipeline still
# evaluates its full internal result set.
_HOSTED_SEARCH_MAX_RESULTS = 5
_HOSTED_SEARCH_MAX_EVIDENCE_CHARS = 900
_HOSTED_SEARCH_MAX_CITATION_CHARS = 1200

# A provider can return an empty synthesis turn after the router has already
# executed a hosted search. The raw search context is an internal tool payload,
# so it must never be used as the assistant's visible answer.
SEARCH_SYNTHESIS_FALLBACK = (
    "Mình đã thực hiện tìm kiếm nhưng model chưa tạo được phần tổng hợp. "
    "Vui lòng thử lại."
)


def is_raw_search_context(text: Any) -> bool:
    """Return whether model text is the router's internal search payload."""
    value = str(text or "")
    return "[Web Search Results" in value or "[End of Web Search Results]" in value


def _bounded_excerpt(text: Any, max_chars: int = _HOSTED_SEARCH_MAX_CITATION_CHARS) -> str:
    """Bound one citation without repeating the full search context per source."""
    value = str(text or "").strip()
    limit = max(200, int(max_chars))
    if len(value) <= limit:
        return value
    paragraphs = [part.strip() for part in value.split("\n\n") if part.strip()]
    if paragraphs:
        kept: List[str] = []
        used = 0
        for paragraph in paragraphs:
            extra = len(paragraph) + (2 if kept else 0)
            if used + extra > limit:
                break
            kept.append(paragraph)
            used += extra
        if kept:
            return "\n\n".join(kept).rstrip() + " …"
    return value[:limit].rstrip() + " …"

_ROUTER_HOSTED_TOOLS = "ROUTER_CLAUDE_HOSTED_TOOLS"


def router_hosted_tools_enabled() -> bool:
    """Whether the Router should intercept Claude's built-in web tools.

    Enabled by default so Gemini-backed Claude aliases (including Lite) can
    execute WebSearch/WebFetch through the Router. Set
    ``ROUTER_CLAUDE_HOSTED_TOOLS=false`` to leave native tools untouched.
    """
    return os.getenv(_ROUTER_HOSTED_TOOLS, "true").strip().lower() in {
        "1", "true", "yes", "on"
    }

_SEARCH_NAMES = ("web_search", "WebSearch")

_FETCH_NAMES = ("web_fetch", "WebFetch")

def _fetch_query_from_url(url: str) -> str:
    """Turn the url a model asked to fetch into a query the search engines can use.

    WebFetch used to be an HTTP GET against whatever url the model named, so it
    never needed a query. It is answered by the search manager now (see
    `run_server_tool`), and a raw url is a bad query: nearly every token in
    `https://example.com/blog/why-caching-fails?utm_source=rss` is scheme,
    separator or tracking parameter, and the one word that carries meaning is
    the shortest.

    What survives is what a human would have typed — the host without `www.`, the
    path read with its separators as spaces — and nothing from the query string
    or fragment. A bare host is then the whole query. If the split leaves nothing
    at all the raw url is passed through: a search keyed on the url is poor, a
    search keyed on an empty string is nothing.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw if "://" in raw else "//" + raw, scheme="http")
        # `urlsplit` raises on a malformed IPv6 literal; unquote and friends do
        # not, so this is the only call worth guarding.
        host = unquote(parts.netloc).lower().rsplit("@", 1)[-1]
        if host.startswith("www."):
            host = host[len("www."):]
        host = host.split(":", 1)[0]  # the port is not the page's identity
        segments = [seg.replace("-", " ").replace("_", " ")
                    for seg in unquote(parts.path).split("/")]
        query = " ".join(part for part in [host, *segments] if part.strip())
        query = " ".join(query.split())
    except Exception:
        query = ""
    return query or raw

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

    WebSearch and WebFetch are the same request here. WebFetch used to open an
    aiohttp session against the url the model named and feed the raw page back,
    so any turn that could call a tool could pull an arbitrary url into its own
    context — and Claude Code does exactly that against a custom model/provider,
    where nothing upstream of the router would have fetched it for us. Both are
    answered by the search manager now, with the url reduced to a query by
    `_fetch_query_from_url`; the router no longer fetches a url a model chose.

    Both proxy paths call this, because the alternative was two copies of the
    same logic and they had drifted: the streaming copy enforced none of the
    client's declared limits and kept no citations, so a streamed search lost
    its sources while an identical non-streamed one kept them.

    `max_uses` is read off the *web_search* tool only — Anthropic's
    `web_fetch_20250910` declares its own, and `server_search_limits` parses
    neither — so a fetch does not consume the search cap. What bounds it is the
    proxy's five-turn recursion, not this.
    """
    if name in _SEARCH_NAMES:
        if not _search_budget_left(limits, used):
            # Anthropic hands back an error result rather than searching past
            # the cap, so the model can still answer from what it has.
            return "max_uses_exceeded", [], "max_uses_exceeded"
        query = str(args.get("query") or "").strip()
        if not query:
            return "Search error: no query provided.", [], "invalid_tool_input"
        logger.info("[ClaudeProxy] server search query=%r", query[:160])
        error_prefix, empty_result = "Search error", "No search results found."
    elif name in _FETCH_NAMES:
        url = str(args.get("url") or "")
        if not url.strip():
            # The url is the tool's only input, so there is nothing to look up.
            return "WebFetch error: no url given.", [], "invalid_tool_input"
        query = _fetch_query_from_url(url)
        logger.info("[ClaudeProxy] server fetch url=%r query=%r",
                    url[:200], query[:160])
        error_prefix, empty_result = "WebFetch error", "No fetch results found."
    else:
        return "", [], None

    try:
        from src.api.opencode_proxy.handler.websearch import (
            resolve_search_engine,
        )
        from src.tools.search.orchestration.manager import execute_hybrid_search

        engine = resolve_search_engine(body, account)
        context, citations = await execute_hybrid_search(
            [query], search_engine=engine,
            auth_key_prefix=auth_key_prefix, account=account,
            result_limit=_HOSTED_SEARCH_MAX_RESULTS,
            evidence_chars=_HOSTED_SEARCH_MAX_EVIDENCE_CHARS,
        )
    except Exception as e:
        logger.warning("[ClaudeProxy] server %s failed: %s", name, e)
        return f"{error_prefix}: {e}", [], "unavailable"
    citations = filter_citations(citations, limits)
    if not context or not citations:
        # Ran and matched nothing. Anthropic spells that an empty content list,
        # not an error, so the code stays None either way.
        return empty_result, [], None
    lines = [context]
    links = []
    for c in citations:
        if c.get("url"):
            links.append(f"- [{c.get('title') or 'Source'}]({c['url']})")
    if links:
        lines.append("\n**Sources / Citations:**\n" + "\n".join(links))
    return "\n".join(lines), citations, None

def _search_budget_left(limits: Dict[str, Any], used: int, cap_override: Optional[int] = None) -> bool:
    """Whether another search is within the client's `max_uses`.

    A client that omitted `max_uses` gets the API default of 5, which is also
    where the recursive tool loop gives up, so the cap is a real bound either
    way rather than an open-ended loop.
    """
    cap = limits.get("max_uses")
    if cap is None:
        cap = _DEFAULT_MAX_USES
    if cap_override is not None:
        cap = min(cap, cap_override)
    return used < cap

def is_redundant_query(new_query: str, past_queries: List[str]) -> bool:
    """Detect if a query has already been executed or is trivially redundant."""
    norm_new = " ".join((new_query or "").strip().lower().split())
    if not norm_new:
        return True
    for past in past_queries or []:
        norm_past = " ".join((past or "").strip().lower().split())
        if norm_new == norm_past:
            return True
    return False

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
    for index, c in enumerate(citations):
        # The search engines report a url and title per hit, not a per-result
        # excerpt. New citations carry their own bounded snippet. For older or
        # mocked callers, use the full result only once instead of multiplying
        # it by every citation in the result list.
        excerpt = c.get("snippet") or c.get("content") or ""
        if not excerpt and index == 0:
            excerpt = rec.get("result") or ""
        excerpt = _bounded_excerpt(excerpt)
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


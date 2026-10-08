"""Run the router's own WebSearch tool when a model calls it.

The router injects a `WebSearch` function tool when a client asks for search, on
the assumption that something on the server will run it. On `/v1/messages` that
is true — the Anthropic proxy intercepts the call, searches, feeds the result
back and recurses. On the OpenAI chat dialect nothing intercepted it, so the
model's call was handed straight to the client:

    finish_reason: 'tool_calls'
    content:       ''
    tool_calls:    [{"name": "WebSearch", ...}]

An internal tool the client has no implementation for, on a response that
carries nothing else. The README documents this path as a server-side tool loop,
so the client never has to implement search — leaking the call breaks that
promise and breaks the turn at the same time.

`pool_manager` has no `web_search` parameter, so Google grounding is not an
alternative here: the injected tool is the only search mechanism on this path.
"""

import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from src.core.config_n_logg.logger import logger_proxy as logger

SEARCH_TOOL_NAMES = ("WebSearch", "web_search")
FETCH_TOOL_NAMES = ("WebFetch", "web_fetch")

# A search that keeps asking for another search is a loop, not a deep
# investigation. The Anthropic path has the same bound; both exist so a model
# cannot spin here forever.
MAX_SEARCH_ROUNDS = 3


def _tool_name(tc: Any) -> str:
    if isinstance(tc, dict):
        fn = tc.get("function") or {}
        # The facade flattens to {"name", "arguments"}; OpenAI nests under
        # "function". Both shapes reach here.
        return fn.get("name", "") or tc.get("name", "") or ""
    fn = getattr(tc, "function", None)
    return getattr(fn, "name", "") or getattr(tc, "name", "") or ""


def _tool_id(tc: Any) -> str:
    if isinstance(tc, dict):
        return tc.get("id") or tc.get("tool_call_id") or f"call_{uuid.uuid4().hex[:16]}"
    return (getattr(tc, "id", "") or getattr(tc, "tool_call_id", "")
            or f"call_{uuid.uuid4().hex[:16]}")


def _tool_args(tc: Any) -> Dict[str, Any]:
    if isinstance(tc, dict):
        fn = tc.get("function") or {}
        args = fn.get("arguments") if "arguments" in fn else tc.get("arguments")
    else:
        fn = getattr(tc, "function", None)
        args = (getattr(fn, "arguments", None) if fn is not None
                else getattr(tc, "arguments", None))
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args.strip():
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def find_intercepted(tool_calls: Optional[List[Any]]) -> Optional[Any]:
    """The first call the router is meant to run itself, if any."""
    for tc in tool_calls or []:
        name = _tool_name(tc)
        if name in SEARCH_TOOL_NAMES or name in FETCH_TOOL_NAMES:
            return tc
    return None


async def run_intercepted(
    tc: Any,
    body: Dict[str, Any],
    account: Optional[Dict[str, Any]],
    auth_key_prefix: str,
) -> str:
    """Execute one intercepted tool and return the text to feed back."""
    name = _tool_name(tc)
    args = _tool_args(tc)

    if name in SEARCH_TOOL_NAMES:
        query = str(args.get("query") or args.get("q") or "")
        logger.info("[SearchIntercept] executing query=%r", query[:160])
        if not query.strip():
            return "Search error: no query given."
        try:
            from src.api.opencode_proxy.handler.websearch import resolve_search_engine
            from src.core.providers.search_manager import execute_hybrid_search

            engine = resolve_search_engine(body, account)
            context, citations = await execute_hybrid_search(
                [query], search_engine=engine,
                auth_key_prefix=auth_key_prefix, account=account,
            )
            if not context:
                return "No search results found."
            parts = [context]
            links = []
            for c in citations or []:
                url = c.get("url")
                if url and url not in context:
                    title = c.get("title") or "Source"
                    links.append(f"- [{title}]({url})")
            if links:
                parts.append("\n**Sources / Citations:**\n" + "\n".join(links))
            return "\n".join(parts)
        except Exception as e:  # a search failure must not fail the turn
            logger.warning("[SearchIntercept] query failed: %s", e)
            return f"Search error: {e}"

    url = str(args.get("url") or "")
    logger.info("[SearchIntercept] fetching url=%r", url[:200])
    if not url.strip():
        return "Fetch error: no url given."
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=10),
                headers={"User-Agent":
                         "Mozilla/5.0 (Router API; +http://127.0.0.1:58100)"}
            ) as resp:
                if resp.status == 200:
                    return (await resp.text())[:8000]
                return f"HTTP error {resp.status}"
    except Exception as e:
        logger.warning("[SearchIntercept] fetch failed: %s", e)
        return f"WebFetch error: {e}"


def _message_of(resp: Any) -> Any:
    """The assistant message on a provider response.

    The chat facade assembles a SimpleNamespace shaped like an OpenAI *chunk*:
    the text lives at `choices[0].message`, not on a top-level `message`. A
    stubbed caller may hand back a completion instead, which has one.
    """
    msg = getattr(resp, "message", None)
    if msg is None and isinstance(resp, dict):
        msg = resp.get("message")
    if msg is not None:
        return msg
    choices = getattr(resp, "choices", None)
    if choices is None and isinstance(resp, dict):
        choices = resp.get("choices")
    if choices:
        first = choices[0]
        return (first.get("message") if isinstance(first, dict)
                else getattr(first, "message", None))
    return None


def _tool_calls_of(result: Any) -> List[Any]:
    """Tool calls on whatever `call_once` returned.

    `pool_manager.call_nonstream` hands back a dict whose "response" is the
    object; a stubbed or stub-shaped caller may return the object directly.
    """
    resp = result.get("response") if isinstance(result, dict) else result
    msg = _message_of(resp)
    calls = getattr(msg, "tool_calls", None)
    if calls is None and isinstance(msg, dict):
        calls = msg.get("tool_calls")
    return list(calls or [])


async def drive_search_loop(
    call_once,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    body: Dict[str, Any],
    account: Optional[Dict[str, Any]],
    auth_key_prefix: str,
    max_rounds: int = MAX_SEARCH_ROUNDS,
) -> Tuple[Any, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Run `call_once` until the model stops asking for the router's own tools.

    Returns the final result plus the message and tool lists that produced it,
    so the caller can build its response without re-deriving them.
    """
    result = None
    for _ in range(max_rounds):
        result = await call_once(messages, tools)
        tool_calls = _tool_calls_of(result)
        target = find_intercepted(tool_calls)
        if target is None:
            return result, messages, tools

        result = await run_intercepted(target, body, account, auth_key_prefix)
        messages = list(messages) + [
            {"role": "assistant", "content": "",
             "tool_calls": [_as_assistant_tool_call(tc)
                            for tc in tool_calls]},
            {"role": "tool", "tool_call_id": _tool_id(target),
             "name": _tool_name(target), "content": result},
        ]
        logger.info("[SearchIntercept] feeding %d chars back, round %d",
                    len(result), _ + 1)
    return result, messages, tools


def _as_assistant_tool_call(tc: Any) -> Dict[str, Any]:
    """Normalise either shape to the OpenAI tool_call the model expects back."""
    args = _tool_args(tc)
    args_str = args if isinstance(args, str) else json.dumps(args)
    return {"id": _tool_id(tc), "type": "function",
            "function": {"name": _tool_name(tc), "arguments": args_str}}

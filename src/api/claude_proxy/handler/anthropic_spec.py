"""Anthropic Messages API protocol helpers.

Single source of truth for translating between the Anthropic wire format and the
OpenAI-shaped structures that PoolManager produces. Used by both the streaming and
non-streaming Claude proxy paths so the two never drift apart.

Covered here:
- Request params: stop_sequences, top_p, top_k, tool_choice, metadata
- Response: stop_reason / stop_sequence mapping (incl. refusal + stop_sequence)
- Usage accounting: cache_control breakpoints -> cache_creation/cache_read tokens
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

    return out


def extract_tool_choice(body: Dict[str, Any]) -> Dict[str, Any]:
    """Translate Anthropic `tool_choice` into Gemini/OpenAI `tool_config` shape.

    Anthropic values:
      {"type": "auto"}                       -> AUTO
      {"type": "any"}                        -> ANY
      {"type": "tool", "name": "X"}          -> forced X
      {"type": "none"}                       -> drop tools entirely

    Returns dict with either {"tool_choice": "auto"|"any"|"none"|{"name": ...}} or {}.
    """
    tc = body.get("tool_choice")
    if not isinstance(tc, dict):
        return {}

    kind = tc.get("type")
    disable_parallel = bool(tc.get("disable_parallel_tool_use"))

    if kind == "auto":
        return {"tool_choice": "auto", "disable_parallel_tool_use": disable_parallel}
    if kind == "any":
        return {"tool_choice": "any", "disable_parallel_tool_use": disable_parallel}
    if kind == "none":
        return {"tool_choice": "none"}
    if kind == "tool":
        name = tc.get("name")
        if isinstance(name, str) and name:
            return {
                "tool_choice": {"type": "function", "function": {"name": name}},
                "disable_parallel_tool_use": disable_parallel,
            }
    return {}


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
) -> Dict[str, int]:
    """Build an Anthropic-shaped usage block.

    Gemini has no prompt cache, so we cannot report real cache hits. What we *can*
    do — and what Claude Code needs in order to render /context at all — is report
    the four required fields consistently. When the client sent cache_control
    breakpoints we attribute the stable prefix to cache_read and the remainder to
    fresh input, which is the shape a real Anthropic response has.
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


def _server_tool_record(name: str, tc_id: str, args: Any,
                        result: str) -> Dict[str, Any]:
    """Record one search the router ran, for the server-tool blocks."""
    query = ""
    if isinstance(args, dict):
        query = str(args.get("query") or args.get("url") or "")
    return {
        "id": tc_id if str(tc_id).startswith(_SERVER_TOOL_PREFIX)
        else _SERVER_TOOL_PREFIX + str(tc_id),
        "name": "web_search" if name.lower().endswith("search") else "web_fetch",
        "query": query,
        "result": result or "",
        "failed": not (result or "").strip()
        or (result or "").startswith(("Search error", "WebFetch error",
                                      "Fetch error", "HTTP error")),
    }


def _server_tool_use_block(rec: Dict[str, Any]) -> Dict[str, Any]:
    key = "query" if rec["name"] == "web_search" else "url"
    return {"type": "server_tool_use", "id": rec["id"], "name": rec["name"],
            "input": {key: rec["query"]}}


def _web_search_result_block(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The result block paired with a `server_tool_use` by `tool_use_id`."""
    if rec.get("failed"):
        return {"type": "web_search_tool_result", "tool_use_id": rec["id"],
                "content": {"type": "web_search_tool_result_error",
                            "error_code": "unavailable"}}
    return {"type": "web_search_tool_result", "tool_use_id": rec["id"],
            "content": [{"type": "web_search_result",
                          "title": rec.get("query") or "Result",
                          "url": rec.get("query") or "",
                          "encrypted_content": rec.get("result") or ""}]}


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
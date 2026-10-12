"""OpenAI Responses wire formatting helpers."""

import json
from typing import Any
def _error_type(status_code: int) -> str:
    """The OpenAI error type that goes with an HTTP status."""
    return {400: "invalid_request_error",
            401: "authentication_error",
            403: "permission_error",
            404: "invalid_request_error",
            429: "rate_limit_error",
            }.get(status_code, "api_error")


def _spoof_model_name_in_response(result: dict, original_name: str) -> dict:
    """Replace model name in response with original alias name.

    Client sent "claude-3-5-sonnet" (alias) → we routed to "gemini-flash" (target)
    → response must say "claude-3-5-sonnet" to match client expectation.
    """
    if not result or not isinstance(result, dict):
        return result

    # Update model in response
    if "model" in result:
        result["model"] = original_name

    # Update in choices (for chat completions)
    if "choices" in result:
        for choice in result.get("choices", []):
            if isinstance(choice, dict) and "message" in choice:
                msg = choice["message"]
                if isinstance(msg, dict) and "model" in msg:
                    msg["model"] = original_name

    return result


def _response_text_value(value: Any) -> str:
    """Normalize OpenAI content, including the list form used by some clients."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return "" if value is None else str(value)


def _extract_response_text(result: dict) -> str:
    """Extract text content from an OpenAI-format response dict."""
    choices = result.get("choices", [])
    if not choices:
        return ""
    msg = choices[0].get("message", {}) or {}
    return _response_text_value(msg.get("content"))


def _extract_finish_reason(result: dict) -> str:
    """The upstream reason, mapped onto the OpenAI enum.

    Upstream does not speak OpenAI's vocabulary — Gemini reports things like
    `malformed_function_call` — and the field is a Literal in the SDK, so an
    unmapped value makes the response unparseable rather than merely odd.
    """
    from src.core.providers.finish_reason import normalize_finish_reason

    choices = result.get("choices", [])
    if not choices:
        return "stop"
    has_calls = any(
        isinstance(c, dict) and (c.get("message") or {}).get("tool_calls")
        for c in choices
    )
    return normalize_finish_reason(choices[0].get("finish_reason"),
                                   has_tool_calls=has_calls)


def _extract_response_tool_calls(result: dict) -> list[dict]:
    """The function calls a chat-format response asked for, flattened.

    Reads both nested shapes: `message.tool_calls[].function` is what the proxy
    emits, but a handler may hand back the flattened `function.name` /
    `function.arguments` form, and silently returning nothing for either is how
    a tool turn used to come back empty.
    """
    choices = result.get("choices") or []
    if not choices:
        return []
    msg = choices[0].get("message") or {}

    out: list[dict] = []
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name") or ""
        if not name:
            continue
        args = fn.get("arguments")
        if args is None:
            args = tc.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args) if args else ""
        out.append({"id": tc.get("id"), "name": name, "arguments": args})
    return out


def _responses_event(kind: str, seq: int, **fields) -> bytes:
    """One Responses SSE frame.

    Official frames carry an `event:` line naming the type *and* a `data:` line
    whose JSON repeats it. The Python SDK reads the type out of the JSON, but
    other clients dispatch on the event name, so both are emitted.
    """
    payload = {"type": kind, "sequence_number": seq, **fields}
    body = json.dumps(payload, ensure_ascii=False)
    return f"event: {kind}\ndata: {body}\n\n".encode("utf-8")


def _responses_usage(usage: dict | None) -> dict | None:
    """Chat usage -> Responses usage. The two dialects name the same numbers
    differently: prompt/completion_tokens vs input/output_tokens.

    Both `*_tokens_details` objects are required members of ResponseUsage, so
    they are always emitted; the numbers inside them default to 0.

    No usage at all yields None, not a row of zeros. `Response.usage` is
    optional, and reporting 0/0 for a request that really spent tokens is a
    number a client will believe and bill against; null says "unknown".
    """
    if not usage:
        return None
    inp = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    out = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    total = int(usage.get("total_tokens", 0) or 0) or (inp + out)
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
    reasoning = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0)
    return {
        "input_tokens": inp,
        "input_tokens_details": {"cached_tokens": cached},
        "output_tokens": out,
        "output_tokens_details": {"reasoning_tokens": reasoning},
        "total_tokens": total,
    }


def _responses_envelope(rid: str, model: str, status: str, output: list,
                        created_at: int, usage: dict | None = None) -> dict:
    """The `response` object embedded in created/in_progress/completed.

    `parallel_tool_calls`, `tool_choice` and `tools` are required members of the
    Response object; omitting them makes the official SDK reject the frame.
    """
    return {
        "id": rid,
        "object": "response",
        "created_at": created_at,
        "model": model,
        "status": status,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": usage,
    }


def _output_text_part(text: str, citations: list | None = None) -> dict:
    return {"type": "output_text", "text": text,
            "annotations": _url_citations(text, citations or []),
            "logprobs": []}


def _output_message(item_id: str, status: str, text: str,
                    citations: list | None = None) -> dict:
    return {"id": item_id, "type": "message", "status": status,
            "role": "assistant",
            "content": [_output_text_part(text, citations)]}


def _web_search_action(record: dict) -> dict:
    """The action object inside a `web_search_call` item.

    `ActionSearch` declares exactly `type`, `queries`, `query` and `sources`;
    `sources` entries are `{type: "url", url}`. Anything outside that set fails
    validation under strict=True, so the engine name the router used internally
    has no home here.
    """
    if record.get("type") == "open_page":
        return {"type": "open_page", "url": record.get("url", "")}
    action: dict = {"type": "search"}
    queries = record.get("queries")
    if isinstance(queries, list) and queries:
        action["queries"] = [str(q) for q in queries]
    if record.get("query"):
        action["query"] = record["query"]
    sources = [{"type": "url", "url": c["url"]}
               for c in (record.get("citations") or [])
               if isinstance(c, dict) and c.get("url")]
    if sources:
        action["sources"] = sources
    return action


def _web_search_call_item(call_id: str, record: dict) -> dict:
    """A `web_search_call` output item.

    `status` is `failed` when the search did not run, which is how a client
    tells an empty search apart from one that was attempted. A search that ran
    and matched nothing is `completed`, not `failed`.
    """
    failed = bool(record.get("error")) or not record.get("query")
    return {
        "id": call_id,
        "type": "web_search_call",
        "status": "failed" if failed else "completed",
        "action": _web_search_action(record),
    }


def _url_citations(text: str, citations: list) -> list:
    """Citation annotations for a message that used search results.

    OpenAI attaches `url_citation` annotations to the text part, each spanning a
    character range of it. Without them a client has no way to show where an
    answer came from, which is the whole point of the hosted tool.
    """
    if not text or not citations:
        return []
    out = []
    for c in citations:
        if not isinstance(c, dict) or not c.get("url"):
            continue
        out.append({
            "type": "url_citation",
            "url": c["url"],
            "title": c.get("title") or c["url"],
            # The whole message is attributed to every source: the router does
            # not know which span of the answer a given page supports.
            "start_index": 0,
            "end_index": len(text),
        })
    return out







"""OpenAI (chat completions) to Anthropic (messages) wire translation.

A custom endpoint is somebody else's paid API, so the router speaks whatever
the owner speaks and translates on the way in and on the way back. OpenAI is
the internal canonical format, so Anthropic-compatible endpoints are the
asymmetric case: everything upstream of here has already been normalised to
OpenAI, and this module is the only place that knows how to leave that shape.

The two dialects disagree about four things, and each one is silent if missed:

- system prompt is a top-level field in Anthropic, a message in OpenAI
- tool results are ``role: tool`` messages in OpenAI, ``tool_result`` content
  blocks in Anthropic
- ``max_tokens`` is required by Anthropic and optional in OpenAI
- stream framing is a typed event stream, not ``choices[].delta``
"""

from typing import Any, Dict, List, Optional

API_FORMAT_OPENAI = "openai"
API_FORMAT_ANTHROPIC = "anthropic"
SUPPORTED_API_FORMATS = (API_FORMAT_OPENAI, API_FORMAT_ANTHROPIC)

# Anthropic rejects a request without this; OpenAI treats it as optional.
ANTHROPIC_MIN_MAX_TOKENS = 1
ANTHROPIC_DEFAULT_MAX_TOKENS = 4096


def normalize_api_format(value: Any) -> str:
    """Return a supported format name, defaulting to OpenAI.

    An endpoint predating this column reads as openai, which is what every
    endpoint used to be. Defaulting the other way would silently break them.
    """
    name = str(value or "").strip().lower()
    return name if name in SUPPORTED_API_FORMATS else API_FORMAT_OPENAI


def is_anthropic_format(value: Any) -> bool:
    return normalize_api_format(value) == API_FORMAT_ANTHROPIC


def _text_of(content: Any) -> str:
    """Flatten OpenAI content, which is a string or a list of typed parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict) and part.get("type") in ("text", "input_text"):
                out.append(str(part.get("text") or ""))
        return "".join(out)
    if content is None:
        return ""
    return str(content)


def _tool_result_block(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert an OpenAI tool-result message into an Anthropic content block."""
    if msg.get("role") != "tool" and not str(msg.get("role") or "").startswith("tool"):
        return None
    call_id = msg.get("tool_call_id") or msg.get("id") or ""
    body = msg.get("content")
    if isinstance(body, str):
        parsed: Any = body
    else:
        parsed = _text_of(body)
    return {
        "type": "tool_result",
        "tool_use_id": str(call_id or ""),
        "content": parsed if isinstance(parsed, str) else str(parsed),
    }


def _tool_call_block(tool_call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    fn = (tool_call or {}).get("function") or {}
    name = fn.get("name") or (tool_call or {}).get("name")
    if not name:
        return None
    raw_args = fn.get("arguments", tool_call.get("arguments"))
    if isinstance(raw_args, str):
        try:
            import json as _json

            args = _json.loads(raw_args) if raw_args.strip() else {}
        except Exception:
            args = {"_raw": raw_args}
    elif isinstance(raw_args, dict):
        args = raw_args
    else:
        args = {}
    return {
        "type": "tool_use",
        "id": (tool_call or {}).get("id") or "",
        "name": name,
        "input": args if isinstance(args, dict) else {},
    }


def _tool_schema(tool: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """OpenAI wraps a function in {"type": "function", "function": {...}};
    Anthropic names it directly with an input_schema."""
    if not isinstance(tool, dict):
        return None
    fn = tool.get("function")
    if not isinstance(fn, dict):
        return None
    name = fn.get("name")
    if not name:
        return None
    return {
        "name": name,
        "description": fn.get("description") or "",
        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
    }


def openai_to_anthropic_body(
    model: str,
    messages: List[Dict[str, Any]],
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    stream: bool = False,
    tools: Optional[List[Dict[str, Any]]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build an Anthropic /v1/messages body from the canonical OpenAI shape."""
    system_chunks: List[str] = []
    converted: List[Dict[str, Any]] = []

    for msg in messages or []:
        role = str(msg.get("role") or "user")
        block = _tool_result_block(msg)
        if block is not None:
            # Anthropic puts a tool result inside the next user turn rather
            # than in a turn of its own.
            if converted and converted[-1]["role"] == "user":
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
            continue

        if role == "system" or role == "developer":
            text = _text_of(msg.get("content"))
            if text:
                system_chunks.append(text)
            continue

        if role == "assistant":
            parts: List[Dict[str, Any]] = []
            text = _text_of(msg.get("content"))
            if text:
                parts.append({"type": "text", "text": text})
            for tc in msg.get("tool_calls") or []:
                block = _tool_call_block(tc)
                if block:
                    parts.append(block)
            converted.append({"role": "assistant", "content": parts or [{"type": "text", "text": ""}]})
            continue

        converted.append({"role": "user", "content": _text_of(msg.get("content"))})

    # Two user turns in a row is invalid in Anthropic; merge rather than fail.
    merged: List[Dict[str, Any]] = []
    for turn in converted:
        if merged and merged[-1]["role"] == turn["role"] == "user":
            prev = merged[-1]["content"]
            cur = turn["content"]
            if isinstance(prev, list) and isinstance(cur, list):
                merged[-1]["content"] = prev + cur
            elif isinstance(prev, list):
                merged[-1]["content"] = prev + [{"type": "text", "text": _text_of(cur)}]
            else:
                merged[-1]["content"] = [
                    {"type": "text", "text": _text_of(prev)},
                    {"type": "text", "text": _text_of(cur)},
                ]
            continue
        merged.append(turn)

    body: Dict[str, Any] = {
        "model": model,
        "max_tokens": max(max_tokens or 0, ANTHROPIC_MIN_MAX_TOKENS)
        or ANTHROPIC_DEFAULT_MAX_TOKENS,
        "messages": merged or [{"role": "user", "content": ""}],
        "stream": bool(stream),
    }
    if system_chunks:
        body["system"] = "\n\n".join(system_chunks)
    if temperature is not None:
        body["temperature"] = temperature
    if top_p is not None:
        body["top_p"] = top_p

    anthropic_tools = [t for t in (_tool_schema(x) for x in (tools or [])) if t]
    if anthropic_tools:
        body["tools"] = anthropic_tools

    # The OpenAI name for a stop sequence is "stop"; Anthropic renamed it, and
    # an unknown field is a 400 rather than something quietly ignored.
    passthrough = dict(extra_body or {})
    if "stop" in passthrough and "stop_sequences" not in body:
        stop = passthrough.pop("stop")
        body["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop or [])

    for key, value in passthrough.items():
        if key not in ("messages", "model", "stream", "tools", "system"):
            body[key] = value
    return body


def anthropic_to_openai_message(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a non-streamed Anthropic response into the OpenAI shape."""
    blocks = ((data.get("content") or []) if isinstance(data, dict) else [])
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(str(block.get("text") or ""))
        elif btype == "tool_use":
            import json as _json

            tool_calls.append({
                "id": block.get("id") or "",
                "type": "function",
                "function": {
                    "name": block.get("name") or "",
                    "arguments": _json.dumps(block.get("input") or {}),
                },
            })

    stop_reason = (data or {}).get("stop_reason")
    finish = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
    }.get(str(stop_reason or ""), "stop")

    message: Dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts),
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": (data or {}).get("id", ""),
        "object": "chat.completion",
        "model": (data or {}).get("model", ""),
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish,
        }],
        "usage": anthropic_to_openai_usage((data or {}).get("usage")),
    }


def anthropic_to_openai_usage(usage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Anthropic reports cache tokens as a separate input; OpenAI folds them in."""
    usage = usage or {}
    prompt = int(usage.get("input_tokens") or 0)
    completion = int(usage.get("output_tokens") or 0)
    prompt += int(usage.get("cache_creation_input_tokens") or 0)
    prompt += int(usage.get("cache_read_input_tokens") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def anthropic_event_to_openai_delta(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Translate one Anthropic stream event into an OpenAI delta chunk.

    Returns None for events that carry no delta, so the caller can skip them
    rather than emit a chunk with an empty delta that a strict client would
    reject.
    """
    etype = (event or {}).get("type")
    if etype == "content_block_start":
        block = (event.get("content_block") or {})
        if block.get("type") == "tool_use":
            return {
                "choices": [{
                    "index": 0,
                    "delta": {"tool_calls": [{
                        "index": event.get("index", 0),
                        "id": block.get("id") or "",
                        "type": "function",
                        "function": {
                            "name": block.get("name") or "",
                            "arguments": "",
                        },
                    }]},
                    "finish_reason": None,
                }]
            }
        return None

    if etype == "content_block_delta":
        delta = event.get("delta") or {}
        dtype = delta.get("type")
        if dtype == "text_delta":
            return {
                "choices": [{
                    "index": 0,
                    "delta": {"content": delta.get("text") or ""},
                    "finish_reason": None,
                }]
            }
        if dtype == "thinking_delta":
            return {
                "choices": [{
                    "index": 0,
                    "delta": {"reasoning_content": delta.get("thinking") or ""},
                    "finish_reason": None,
                }]
            }
        if dtype == "input_json_delta":
            return {
                "choices": [{
                    "index": 0,
                    "delta": {"tool_calls": [{
                        "index": event.get("index", 0),
                        "function": {"arguments": delta.get("partial_json") or ""},
                    }]},
                    "finish_reason": None,
                }]
            }
        return None

    if etype == "message_delta":
        stop = (event.get("delta") or {}).get("stop_reason")
        finish = {
            "end_turn": "stop", "stop_sequence": "stop",
            "max_tokens": "length", "tool_use": "tool_calls",
        }.get(str(stop or ""), "stop")
        chunk: Dict[str, Any] = {
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
        }
        usage = event.get("usage")
        if usage:
            chunk["usage"] = anthropic_to_openai_usage(usage)
        return chunk

    return None
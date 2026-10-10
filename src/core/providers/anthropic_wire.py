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


# Anthropic accepts exactly these four. `image/jpg` is the common non-standard
# label in the wild and it is rejected with a 400, so it is normalised here
# rather than forwarded verbatim (hermes-agent#55432).
ANTHROPIC_IMAGE_TYPES = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
}
ANTHROPIC_DEFAULT_IMAGE_TYPE = "image/png"


def _image_block_from_url(url: Any) -> Optional[Dict[str, Any]]:
    """OpenAI carries an image as a URL that may be a data URL.

    Anthropic splits that in two: an inline payload wants a media_type beside
    the bytes, a remote one wants the url. Collapsing both into the plain text
    that came before silently threw the picture away.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    if url.startswith("data:"):
        header, _, payload = url.partition(",")
        if not payload:
            return None
        media = header[len("data:"):].split(";", 1)[0].strip().lower()
        if not media:
            media = ANTHROPIC_DEFAULT_IMAGE_TYPE
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": ANTHROPIC_IMAGE_TYPES.get(media, media),
                "data": payload,
            },
        }
    return {"type": "image", "source": {"type": "url", "url": url}}


def _to_anthropic_blocks(content: Any) -> List[Dict[str, Any]]:
    """Translate OpenAI message content into Anthropic content blocks.

    Anything already shaped like an Anthropic block is kept, so a client that
    speaks Anthropic reaches an Anthropic endpoint untouched. An unrecognised
    part is dropped rather than stringified: its text was never there, and
    sending a placeholder would answer the wrong question with confidence.
    """
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content)}]

    blocks: List[Dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            if part:
                blocks.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")

        if ptype in ("text", "input_text"):
            text = part.get("text") or ""
            if text:
                block: Dict[str, Any] = {"type": "text", "text": str(text)}
                # The cache marker rides on the block, so rebuilding the block
                # without it would throw away the reason the block exists.
                if part.get("_cache_control"):
                    block["_cache_control"] = True
                blocks.append(block)
            continue

        if ptype == "refusal":
            text = part.get("refusal") or ""
            if text:
                blocks.append({"type": "text", "text": str(text)})
            continue

        if ptype == "image_url":
            raw = part.get("image_url") or {}
            url = raw.get("url") if isinstance(raw, dict) else raw
            block = _image_block_from_url(url)
            if block:
                blocks.append(block)
            continue

        if ptype in ("input_image", "image"):
            raw = part.get("image_url") or part.get("image") or part.get("source")
            url = raw.get("url") if isinstance(raw, dict) else raw
            if isinstance(part.get("source"), dict) and ptype == "image":
                blocks.append(part)  # already Anthropic
                continue
            block = _image_block_from_url(url)
            if block:
                blocks.append(block)
            continue

        if ptype == "file":
            blocks.append({"type": "document", "source": {
                "type": "base64",
                "media_type": part.get("media_type") or "application/pdf",
                "data": part.get("file_data") or part.get("data") or "",
            }})
            continue

        # Anthropic-native blocks travel through unchanged.
        if ptype in ("image", "document", "tool_use", "tool_result", "thinking"):
            blocks.append(part)
            continue

    return blocks


def _text_of(content: Any) -> str:
    """Text only, for fields the spec types as a string.

    Never use this on message content: an image has no text to flatten to, and
    using it here is what made pictures disappear.
    """
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
    # The spec allows tool_result content to be blocks, not only a string, so
    # a tool that returns a picture keeps it.
    blocks = _to_anthropic_blocks(body)
    if blocks and any(b.get("type") != "text" for b in blocks):
        content: Any = blocks
    else:
        content = _text_of(body)
    return {
        "type": "tool_result",
        "tool_use_id": str(call_id or ""),
        "content": content,
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


def _tool_choice(value: Any) -> Optional[Dict[str, Any]]:
    """OpenAI and Anthropic both gate tool use, and spell it differently.

    OpenAI: "auto" | "none" | "required" | {"type": "function", "function": {"name": ...}}
    Anthropic: {"type": "auto" | "any" | "none" | "tool", "name": ...}

    Forwarding OpenAI's spelling verbatim is a 400: "required" is not an
    Anthropic value, and the named-function shape has no meaning there.
    """
    if value is None:
        return None
    if isinstance(value, str):
        spelling = {"none": "none", "auto": "auto", "required": "any"}.get(value, "auto")
        return {"type": spelling}
    if not isinstance(value, dict):
        return None

    # Responses-style typed choice, which OpenAI also accepts.
    vtype = value.get("type")
    if vtype in ("auto", "any", "none"):
        return {"type": vtype}
    if vtype in ("required", "any"):
        return {"type": "any"}

    fn = value.get("function")
    if isinstance(fn, dict) and fn.get("name"):
        return {"type": "tool", "name": fn["name"]}
    if value.get("name"):
        return {"type": "tool", "name": value["name"]}
    return None


def _cache_marker() -> Dict[str, Any]:
    return {"type": "ephemeral"}


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
    system_chunks: List[Any] = []
    converted: List[Dict[str, Any]] = []

    for msg in messages or []:
        role = str(msg.get("role") or "user")
        block = _tool_result_block(msg)
        if block is not None:
            # Anthropic puts a tool result inside the next user turn rather
            # than in a turn of its own, and it has to lead that turn. Appending
            # it puts it behind any image the user sent, which is a 400.
            if converted and converted[-1]["role"] == "user":
                content = converted[-1]["content"]
                if not isinstance(content, list):
                    content = _to_anthropic_blocks(content)
                at = 0
                while at < len(content) and content[at].get("type") == "tool_result":
                    at += 1
                converted[-1]["content"] = content[:at] + [block] + content[at:]
            else:
                converted.append({"role": "user", "content": [block]})
            continue

        if role == "system" or role == "developer":
            system_blocks = _to_anthropic_blocks(msg.get("content"))
            if system_blocks:
                system_chunks.append(system_blocks)
            continue

        if role == "assistant":
            parts: List[Dict[str, Any]] = _to_anthropic_blocks(msg.get("content"))
            for tc in msg.get("tool_calls") or []:
                block = _tool_call_block(tc)
                if block:
                    parts.append(block)
            converted.append({"role": "assistant", "content": parts or [{"type": "text", "text": ""}]})
            continue

        user_blocks = _to_anthropic_blocks(msg.get("content"))
        if user_blocks:
            converted.append({"role": "user", "content": user_blocks})

    # Two user turns in a row is invalid in Anthropic; merge rather than fail.
    # tool_result blocks have to stay at the front of the turn: the spec puts
    # them before anything else, and a picture sent ahead of one is a 400.
    merged: List[Dict[str, Any]] = []
    for turn in converted:
        if merged and merged[-1]["role"] == turn["role"] == "user":
            prev = merged[-1]["content"]
            cur = turn["content"]
            if not isinstance(prev, list):
                prev = _to_anthropic_blocks(prev)
            if not isinstance(cur, list):
                cur = _to_anthropic_blocks(cur)
            results = [b for b in prev + cur
                       if isinstance(b, dict) and b.get("type") == "tool_result"]
            rest = [b for b in prev + cur if b not in results]
            merged[-1]["content"] = results + rest
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
        # Blocks carry a cache_control marker; plain text does not. Anthropic's
        # system accepts either, so the block form is used only when it says
        # something the string form cannot.
        needs_blocks = any(
            isinstance(c, list) and any("_cache_control" in b for b in c)
            for c in system_chunks
        )
        if needs_blocks:
            merged_system: List[Dict[str, Any]] = []
            for chunk in system_chunks:
                if isinstance(chunk, list):
                    for block in chunk:
                        clean = {k: v for k, v in block.items() if not k.startswith("_")}
                        if block.get("_cache_control") and "cache_control" not in clean:
                            clean["cache_control"] = _cache_marker()
                        merged_system.append(clean)
                elif chunk:
                    merged_system.append({"type": "text", "text": chunk})
            body["system"] = merged_system
        else:
            # No markers anywhere, so the compact string form is enough and
            # keeps the payload smaller for an endpoint that does not cache.
            body["system"] = "\n\n".join(
                "".join(b.get("text", "") for b in c if isinstance(b, dict))
                for c in system_chunks if isinstance(c, list)
            )
    if temperature is not None:
        body["temperature"] = temperature
    if top_p is not None:
        body["top_p"] = top_p

    anthropic_tools = [t for t in (_tool_schema(x) for x in (tools or [])) if t]
    if anthropic_tools:
        body["tools"] = anthropic_tools

    # An OpenAI client that set tool_choice gets it in extra_body. (The router
    # does not currently forward it from the chat paths at all -- that is a
    # separate gap -- but an endpoint reached through extra_body gets the
    # correct spelling rather than a value Anthropic will reject.)
    passthrough = dict(extra_body or {})
    # thinking is forwarded exactly as the client wrote it, never synthesised.
    # Anthropic is mid-migration on this field: {"type":"enabled","budget_tokens":
    # N} is deprecated on 4.6 and rejected with a 400 on 4.7+, while
    # {"type":"adaptive"} is rejected on 4.5 and earlier. Which one is valid
    # depends on the model behind an endpoint the router cannot see, so the
    # only safe move is not to guess.
    thinking = passthrough.pop("thinking", None)
    if isinstance(thinking, dict) and thinking.get("type"):
        body["thinking"] = thinking

    raw_choice = passthrough.pop("tool_choice", None)
    if raw_choice is not None:
        mapped = _tool_choice(raw_choice)
        if mapped is not None:
            body["tool_choice"] = mapped

    # The OpenAI name for a stop sequence is "stop"; Anthropic renamed it, and
    # an unknown field is a 400 rather than something quietly ignored.
    if "stop" in passthrough and "stop_sequences" not in body:
        stop = passthrough.pop("stop")
        body["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop or [])

    for key, value in passthrough.items():
        if key not in ("messages", "model", "stream", "tools", "system", "tool_choice"):
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
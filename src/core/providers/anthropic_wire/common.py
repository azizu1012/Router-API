"""Shared primitives for the Anthropic wire translator."""

from typing import Any, Dict, List, Optional

API_FORMAT_OPENAI = "openai"
API_FORMAT_ANTHROPIC = "anthropic"
SUPPORTED_API_FORMATS = (API_FORMAT_OPENAI, API_FORMAT_ANTHROPIC)

# Anthropic rejects a request without this; OpenAI treats it as optional.
ANTHROPIC_MIN_MAX_TOKENS = 1
ANTHROPIC_DEFAULT_MAX_TOKENS = 4096


def normalize_api_format(value: Any) -> str:
    """Return a supported format name, defaulting to OpenAI."""
    name = str(value or "").strip().lower()
    return name if name in SUPPORTED_API_FORMATS else API_FORMAT_OPENAI


def is_anthropic_format(value: Any) -> bool:
    return normalize_api_format(value) == API_FORMAT_ANTHROPIC


# Anthropic accepts exactly these four. ``image/jpg`` is normalised because
# Anthropic rejects that common non-standard label.
ANTHROPIC_IMAGE_TYPES = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
}
ANTHROPIC_DEFAULT_IMAGE_TYPE = "image/png"


def _image_block_from_url(url: Any) -> Optional[Dict[str, Any]]:
    """Translate a remote or data URL into an Anthropic image block."""
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
    """Translate OpenAI message content into Anthropic content blocks."""
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
                blocks.append(part)
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
    """Extract text for fields whose protocol type is a string."""
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
    """Convert an OpenAI tool-result message into an Anthropic block."""
    if msg.get("role") != "tool" and not str(msg.get("role") or "").startswith("tool"):
        return None
    call_id = msg.get("tool_call_id") or msg.get("id") or ""
    body = msg.get("content")
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
            import json
            args = json.loads(raw_args) if raw_args.strip() else {}
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
    """Convert an OpenAI function tool into an Anthropic input schema."""
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
    """Map OpenAI tool choice spellings to Anthropic's protocol."""
    if value is None:
        return None
    if isinstance(value, str):
        spelling = {"none": "none", "auto": "auto", "required": "any"}.get(value, "auto")
        return {"type": spelling}
    if not isinstance(value, dict):
        return None

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


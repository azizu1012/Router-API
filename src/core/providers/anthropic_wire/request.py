"""OpenAI-to-Anthropic request translation."""

from typing import Any, Dict, List, Optional

from .common import (
    ANTHROPIC_DEFAULT_MAX_TOKENS,
    ANTHROPIC_MIN_MAX_TOKENS,
    _cache_marker,
    _to_anthropic_blocks,
    _tool_call_block,
    _tool_choice,
    _tool_result_block,
    _tool_schema,
)


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
    """Build an Anthropic ``/v1/messages`` body from OpenAI messages."""
    system_chunks: List[Any] = []
    converted: List[Dict[str, Any]] = []

    for msg in messages or []:
        role = str(msg.get("role") or "user")
        block = _tool_result_block(msg)
        if block is not None:
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

    # Anthropic requires alternating user and assistant turns.
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
        "max_tokens": max(max_tokens or 0, ANTHROPIC_MIN_MAX_TOKENS) or ANTHROPIC_DEFAULT_MAX_TOKENS,
        "messages": merged or [{"role": "user", "content": ""}],
        "stream": bool(stream),
    }
    if system_chunks:
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

    passthrough = dict(extra_body or {})
    thinking = passthrough.pop("thinking", None)
    if isinstance(thinking, dict) and thinking.get("type"):
        body["thinking"] = thinking

    raw_choice = passthrough.pop("tool_choice", None)
    if raw_choice is not None:
        mapped = _tool_choice(raw_choice)
        if mapped is not None:
            body["tool_choice"] = mapped

    if "stop" in passthrough and "stop_sequences" not in body:
        stop = passthrough.pop("stop")
        body["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop or [])

    for key, value in passthrough.items():
        if key not in ("messages", "model", "stream", "tools", "system", "tool_choice"):
            body[key] = value
    return body


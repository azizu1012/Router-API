"""Anthropic-to-OpenAI response and stream-event translation."""

import json
from typing import Any, Dict, List, Optional


def anthropic_to_openai_message(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a non-streamed Anthropic response into OpenAI shape."""
    blocks = ((data.get("content") or []) if isinstance(data, dict) else [])
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(str(block.get("text") or ""))
        elif btype == "thinking":
            # Compaction summaries from Anthropic-compatible endpoints can be
            # returned as thinking-only. Preserve it as assistant text in the
            # OpenAI bridge; the Claude-facing adapter decides when to expose
            # it as summary text.
            text_parts.append(str(block.get("thinking") or ""))
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id") or "",
                "type": "function",
                "function": {
                    "name": block.get("name") or "",
                    "arguments": json.dumps(block.get("input") or {}),
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
    """Fold Anthropic cache token counts into OpenAI prompt tokens."""
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
    """Translate one typed Anthropic stream event into an OpenAI chunk."""
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
                        "function": {"name": block.get("name") or "", "arguments": ""},
                    }]},
                    "finish_reason": None,
                }]
            }
        return None

    if etype == "content_block_delta":
        delta = event.get("delta") or {}
        dtype = delta.get("type")
        if dtype == "text_delta":
            return {"choices": [{"index": 0, "delta": {"content": delta.get("text") or ""}, "finish_reason": None}]}
        if dtype == "thinking_delta":
            return {"choices": [{"index": 0, "delta": {"reasoning_content": delta.get("thinking") or ""}, "finish_reason": None}]}
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
        chunk: Dict[str, Any] = {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
        usage = event.get("usage")
        if usage:
            chunk["usage"] = anthropic_to_openai_usage(usage)
        return chunk

    return None


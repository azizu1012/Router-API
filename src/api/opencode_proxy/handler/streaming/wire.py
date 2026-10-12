"""Provider-delta extraction and OpenAI SSE framing."""

import json
import time
import uuid
from typing import Any, Dict, List, Optional


def openai_sse(
    model_name: str,
    content: Optional[str] = None,
    tool_calls: Optional[List[dict]] = None,
    finish_reason: Optional[str] = None,
    chunk_id: Optional[str] = None,
    reasoning_content: Optional[str] = None,
    thought_signature: Optional[str] = None,
) -> bytes:
    if not chunk_id:
        chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
    data: Dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
    }
    if content is not None:
        data["choices"][0]["delta"]["content"] = content
    if reasoning_content is not None:
        data["choices"][0]["delta"]["reasoning_content"] = reasoning_content
    if tool_calls:
        data["choices"][0]["delta"]["tool_calls"] = [
            {
                "index": idx,
                "id": tc.get("id") or f"call_{uuid.uuid4().hex}",
                "type": tc.get("type", "function"),
                "function": {
                    "name": tc.get("name", ""),
                    "arguments": tc.get("arguments", ""),
                },
            }
            for idx, tc in enumerate(tool_calls)
        ]
    if thought_signature is not None:
        data["choices"][0]["delta"]["thought_signature"] = thought_signature
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def get_reasoning(delta_obj: Any) -> Optional[str]:
    if not delta_obj:
        return None
    for attr in ("reasoning_content", "thought", "reasoning"):
        value = getattr(delta_obj, attr, None)
        if value:
            return value
    if hasattr(delta_obj, "get"):
        for key in ("reasoning_content", "thought", "reasoning"):
            try:
                value = delta_obj.get(key)
                if value:
                    return value
            except Exception:
                pass
    for attr in ("model_extra", "extra_fields", "additional_kwargs"):
        extra = getattr(delta_obj, attr, None)
        if extra and isinstance(extra, dict):
            for key in ("reasoning_content", "thought", "reasoning"):
                if extra.get(key):
                    return extra[key]
    return None


def get_content(delta_obj: Any) -> Optional[str]:
    if not delta_obj:
        return None
    value = getattr(delta_obj, "content", None)
    if value:
        return value
    if hasattr(delta_obj, "get"):
        try:
            value = delta_obj.get("content")
            if value:
                return value
        except Exception:
            pass
    for attr in ("model_extra", "extra_fields", "additional_kwargs"):
        extra = getattr(delta_obj, attr, None)
        if extra and isinstance(extra, dict) and extra.get("content"):
            return extra["content"]
    return None


def get_tool_calls(delta_obj: Any) -> Optional[List[dict]]:
    if not delta_obj:
        return None
    tool_calls = getattr(delta_obj, "tool_calls", None)
    if not tool_calls:
        return None
    result = []
    for tool_call in tool_calls:
        if isinstance(tool_call, dict):
            fn = tool_call.get("function", {})
            fn_name = fn.get("name", "") if isinstance(fn, dict) else ""
            fn_args = fn.get("arguments", "") if isinstance(fn, dict) else ""
            call_id = tool_call.get("id")
            call_type = tool_call.get("type", "function")
        else:
            function = getattr(tool_call, "function", None)
            call_id = getattr(tool_call, "id", None)
            call_type = getattr(tool_call, "type", "function")
            fn_name = getattr(function, "name", "") if function else ""
            fn_args = getattr(function, "arguments", "") if function else ""
        result.append({
            "id": call_id,
            "type": call_type,
            "name": fn_name,
            "arguments": fn_args,
        })
    return result


def get_thought_signature(delta_obj: Any) -> Optional[str]:
    if not delta_obj:
        return None
    value = getattr(delta_obj, "thought_signature", None)
    if value:
        return value
    if hasattr(delta_obj, "get"):
        try:
            value = delta_obj.get("thought_signature")
            if value:
                return value
        except Exception:
            pass
    return None


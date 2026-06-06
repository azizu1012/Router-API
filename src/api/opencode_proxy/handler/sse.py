import json
import uuid
from typing import Any, List, Optional


def openai_chunks(chunk: Any, model_name: str) -> List[bytes]:
    if not chunk.choices:
        return []

    delta = chunk.choices[0].delta
    content = getattr(delta, "content", None)
    tool_calls = getattr(delta, "tool_calls", None)
    finish_reason = getattr(chunk.choices[0], "finish_reason", None)

    result = {}
    result["id"] = f"chatcmpl-{uuid.uuid4().hex}"
    result["object"] = "chat.completion.chunk"
    result["model"] = model_name
    result["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish_reason}]

    if content is not None:
        result["choices"][0]["delta"]["content"] = content

    if tool_calls:
        result["choices"][0]["delta"]["tool_calls"] = _build_tool_calls(tool_calls)

    return [f"data: {json.dumps(result, ensure_ascii=False)}\n\n".encode("utf-8")]


def _build_tool_calls(tool_calls) -> List[dict]:
    result = []
    for tc in tool_calls:
        fn = getattr(tc, "function", None)
        if fn:
            result.append({
                "index": getattr(tc, "index", 0),
                "id": getattr(tc, "id", f"call_{uuid.uuid4().hex}"),
                "type": "function",
                "function": {
                    "name": getattr(fn, "name", ""),
                    "arguments": getattr(fn, "arguments", "") or "",
                }
            })
    return result


def error_sse(error_response: dict) -> List[bytes]:
    return [f"data: {json.dumps(error_response, ensure_ascii=False)}\n\n".encode("utf-8"), b"data: [DONE]\n\n"]
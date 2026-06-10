import json
import time
from typing import Any, Dict, List, Optional


def make_sse(chunk: Any, choice: Any, delta_dict: Dict[str, Any], finish_reason: Optional[str], model: Optional[str] = None) -> bytes:
    created = getattr(chunk, "created", None) or int(time.time())
    data = {
        "id": chunk.id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model or chunk.model,
        "choices": [{"index": choice.index, "delta": delta_dict, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _build_tool_calls(raw_tool_calls: Any) -> Optional[List[Dict[str, Any]]]:
    tc_list = []
    for tc in raw_tool_calls:
        entry: Dict[str, Any] = {"index": tc.index}
        tc_id = getattr(tc, "id", None)
        if tc_id:
            entry["id"] = tc_id
        tc_type = getattr(tc, "type", None)
        if tc_type:
            entry["type"] = tc_type
        fn = getattr(tc, "function", None)
        if fn is not None:
            fn_dict: Dict[str, Any] = {}
            fn_name = getattr(fn, "name", None)
            if fn_name:
                fn_dict["name"] = fn_name
            fn_args = getattr(fn, "arguments", None)
            if fn_args:
                fn_dict["arguments"] = fn_args
            if fn_dict:
                entry["function"] = fn_dict
        tc_list.append(entry)
    return tc_list if tc_list else None


def openai_chunks(chunk: Any, model: Optional[str] = None) -> List[bytes]:
    if not chunk or not chunk.choices:
        return []
    choice = chunk.choices[0]
    delta = choice.delta
    finish_reason = getattr(choice, "finish_reason", None)

    role = getattr(delta, "role", None)
    content = getattr(delta, "content", None)
    reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "thought", None) or getattr(delta, "reasoning", None)
    tool_calls = _build_tool_calls(getattr(delta, "tool_calls", None))

    has_role = bool(role)
    has_content = content is not None
    has_reasoning = reasoning is not None
    has_tool_calls = tool_calls is not None
    has_finish = finish_reason is not None

    if not has_role and not has_content and not has_reasoning and not has_tool_calls and not has_finish:
        return []

    results: List[bytes] = []

    if has_role and has_content:
        results.append(make_sse(chunk, choice, {"role": role}, None, model))
        if has_reasoning:
            results.append(make_sse(chunk, choice, {"reasoning_content": reasoning}, None, model))
        results.append(make_sse(chunk, choice, {"content": content}, None, model))
    elif has_role:
        results.append(make_sse(chunk, choice, {"role": role}, None, model))
        if has_reasoning:
            results.append(make_sse(chunk, choice, {"reasoning_content": reasoning}, None, model))
    else:
        if has_reasoning:
            results.append(make_sse(chunk, choice, {"reasoning_content": reasoning}, None, model))
        if has_content:
            results.append(make_sse(chunk, choice, {"content": content}, None, model))

    if has_tool_calls:
        results.append(make_sse(chunk, choice, {"tool_calls": tool_calls}, None, model))

    if has_finish:
        results.append(make_sse(chunk, choice, {}, finish_reason, model))

    return results


def error_sse(error_resp: Dict[str, Any]) -> List[bytes]:
    chunks: List[bytes] = []
    first = {
        "id": error_resp["id"], "object": "chat.completion.chunk",
        "created": error_resp["created"], "model": error_resp["model"],
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    chunks.append(f"data: {json.dumps(first, ensure_ascii=False)}\n\n".encode("utf-8"))
    text = error_resp["choices"][0]["message"]["content"]
    for offset in range(0, len(text), 900):
        chunk = {
            "id": error_resp["id"], "object": "chat.completion.chunk",
            "created": error_resp["created"], "model": error_resp["model"],
            "choices": [{"index": 0, "delta": {"content": text[offset:offset + 900]}, "finish_reason": None}],
        }
        chunks.append(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
    done = {
        "id": error_resp["id"], "object": "chat.completion.chunk",
        "created": error_resp["created"], "model": error_resp["model"],
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    chunks.append(f"data: {json.dumps(done, ensure_ascii=False)}\n\n".encode("utf-8"))
    chunks.append(b"data: [DONE]\n\n")
    return chunks

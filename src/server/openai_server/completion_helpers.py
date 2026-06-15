import json
import time
import uuid
from typing import Any, AsyncIterator, Dict



def completion_response(body: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    now = int(time.time())
    text = result["text"]
    model = body.get("model") or result["model_alias"]
    thought = result.get("thought")

    if not thought and text:
        from src.api.claude_proxy.utils import XMLThinkingExtractor
        _ex = XMLThinkingExtractor()
        _evs = _ex.feed(text) + _ex.flush()
        _extracted = []
        _clean = []
        for _et, _ev in _evs:
            if _et == "thinking":
                _extracted.append(_ev)
            elif _et == "text":
                _clean.append(_ev)
        if _extracted:
            thought = "".join(_extracted)
            text = "".join(_clean) if _clean else ""

    if thought:
        text = f"<think>\n{thought}\n</think>\n\n{text}"
    msg: Dict[str, Any] = {"role": "assistant", "content": text}
    if thought:
        msg["reasoning_content"] = thought
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": now,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": msg,
                "finish_reason": result.get("finish_reason") or "stop",
            }
        ],
        "usage": {
            "prompt_tokens": result.get("input_tokens", 0),
            "completion_tokens": result.get("output_tokens", 0),
            "total_tokens": (result.get("input_tokens", 0) or 0) + (result.get("output_tokens", 0) or 0),
        },
    }


async def stream_response(body: Dict[str, Any], result: Dict[str, Any]) -> AsyncIterator[bytes]:
    cid = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    model = body.get("model") or result["model_alias"]

    thought = result.get("thought")
    if thought:
        think_start = {
            "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"content": "<think>\n"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(think_start, ensure_ascii=False)}\n\n".encode("utf-8")

        for offset in range(0, len(thought), 900):
            chunk_text = thought[offset:offset + 900]
            thought_chunk = {
                "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": {"content": chunk_text, "reasoning_content": chunk_text}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(thought_chunk, ensure_ascii=False)}\n\n".encode("utf-8")

        think_end = {
            "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"content": "\n</think>\n\n"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(think_end, ensure_ascii=False)}\n\n".encode("utf-8")

    first = {
        "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n".encode("utf-8")

    text = result["text"]
    for offset in range(0, len(text), 900):
        chunk = {
            "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"content": text[offset:offset + 900]}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")

    done = {
        "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": result.get("finish_reason") or "stop"}],
    }
    yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"

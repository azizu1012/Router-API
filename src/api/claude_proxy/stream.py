import asyncio
import hashlib
import json
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from src.core.providers.gemini_facade import token_counter
from src.core.usage_logger import log_usage
from src.logical_HQ_translator import (
    _sse,
    _get_simulated_cache_usage,
    is_claude_code_body,
    is_sub_agent_body,
    StreamingTextNormalizer,
    XMLThinkingExtractor,
)


async def _process_anthropic_stream(
    gen_iterator: AsyncIterator[Any], first_chunk: Any,
    model: str, input_tokens: int, key_prefix: str = "",
    auth_key_prefix: str = "",
    body: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[bytes]:
    """
    Xử lý luồng phản hồi từ một API Anthropic (hoặc tương thích) và chuyển đổi nó thành
    định dạng SSE (Server-Sent Events) cho client Claude Code.

    Hàm này chịu trách nhiệm cho các tác vụ sau:
    1. Khởi tạo thông báo `message_start` với thông tin ban đầu về tin nhắn.
    2. Xử lý cảnh báo ngữ cảnh lớn: Nếu số lượng token đầu vào vượt quá ngưỡng cảnh báo,
       một cảnh báo sẽ được chèn vào luồng phản hồi để nhắc nhở người dùng về giới hạn TPM.
    3. Lặp qua các chunk được tạo ra từ `gen_iterator` (iterator chung từ `PoolManager`)
       và xử lý từng chunk:
       a. Phân tích các sự kiện từ `StreamingTextNormalizer` và `XMLThinkingExtractor`
          để tách biệt văn bản, suy nghĩ và các cuộc gọi công cụ.
       b. Tạo các sự kiện SSE (`content_block_start`, `content_block_delta`,
          `content_block_stop`) cho nội dung văn bản và suy nghĩ.
       c. Đệm và xử lý các cuộc gọi công cụ (tool calls) được mô hình tạo ra,
          bao gồm các cuộc gọi `Task` và các cuộc gọi công cụ thông thường.
    4. Xử lý logic `thought_signature` để tạo chữ ký cho các khối suy nghĩ.
    5. Sau khi luồng hoàn tất, tạo các sự kiện `message_delta` và `message_stop` cuối cùng,
       bao gồm thông tin sử dụng token và lý do dừng.
    6. Ghi lại thông tin sử dụng vào hệ thống log.

    Args:
        gen_iterator (AsyncIterator[Any]): Iterator bất đồng bộ tạo ra các chunk phản hồi thô từ mô hình backend.
        first_chunk (Any): Chunk đầu tiên của phản hồi (có thể đã được xử lý trước).
        model (str): Tên của mô hình được sử dụng.
        input_tokens (int): Số lượng token đầu vào của yêu cầu.
        key_prefix (str, optional): Tiền tố khóa API. Mặc định là "".
        auth_key_prefix (str, optional): Tiền tố khóa xác thực. Mặc định là "".
        body (Optional[Dict[str, Any]], optional): Body của yêu cầu API gốc. Mặc định là None.

    Yields:
        AsyncIterator[bytes]: Một iterator bất đồng bộ của các khối phản hồi đã được định dạng SSE.
    """
    msg_id = "msg_" + uuid.uuid4().hex

    include_thoughts = body.get("include_thoughts", True) if body else True
    adjusted_input_tokens = input_tokens
    cache_usage = _get_simulated_cache_usage(body or {}, adjusted_input_tokens)
    cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
    cr = cache_usage.get("cache_read_input_tokens", 0) or 0
    client_input_tokens = max(1, adjusted_input_tokens - cc - cr)
    yield _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {
                "input_tokens": client_input_tokens,
                "output_tokens": 0,
                **cache_usage
            },
        },
    })

    next_block_idx = 0
    text_block_idx = None
    text_started = False
    text_stopped = False
    thought_block_idx = None
    thought_started = False
    thought_stopped = False
    tool_buffers: Dict[int, Dict[str, Any]] = {}
    tool_to_cbidx: Dict[int, int] = {}
    text_chunks: List[str] = []
    thought_parts: List[str] = []
    output_tokens = 0

    async def _yield_signature(idx: Optional[int]):
        if idx is None or not thought_parts:
            return
        full_thought = "".join(thought_parts)
        sig = "gmni_" + hashlib.sha256(full_thought.encode()).hexdigest()[:60]
        yield _sse("content_block_delta", {
            "type": "content_block_delta", "index": idx,
            "delta": {"type": "signature_delta", "signature": sig}
        })

    is_sub = is_sub_agent_body(body or {})
    warning_threshold = 178000 if is_claude_code_body(body or {}) else 170000
    if input_tokens > warning_threshold and not is_sub:
        text_block_idx = next_block_idx
        next_block_idx += 1
        yield _sse("content_block_start", {
            "type": "content_block_start", "index": text_block_idx,
            "content_block": {"type": "text", "text": ""}
        })
        text_started = True
        warning_message = (
            "\r\n\033[1;33m⚠️  [ROUTER-API WARNING] Context is extremely large (%.1fk tokens). "
            "Please run '/compact' in your terminal immediately to avoid 250k TPM rate limits! ⚠️\033[0m\r\n"
            "\033[1;31m⚠️  [CẢNH BÁO] Context hiện tại cực kỳ lớn (%.1fk tokens). "
            "Vui lòng chạy lệnh '/compact' ngay lập tức để tránh bị lỗi giới hạn 250k TPM! ⚠️\033[0m\r\n\r\n"
        ) % (input_tokens / 1000.0, input_tokens / 1000.0)
        yield _sse("content_block_delta", {
            "type": "content_block_delta", "index": text_block_idx,
            "delta": {"type": "text_delta", "text": warning_message}
        })
        text_chunks.append(warning_message)

    async def _iter_safe():
        if first_chunk is not None:
            yield ("chunk", first_chunk)
        while True:
            try:
                while True:
                    try:
                        c = await asyncio.wait_for(asyncio.shield(gen_iterator.__anext__()), timeout=4.0)
                        yield ("chunk", c)
                        break
                    except asyncio.TimeoutError:
                        yield ("ping", None)
            except StopAsyncIteration:
                break

    normalizer = StreamingTextNormalizer()
    extractor = XMLThinkingExtractor()

    async def _process_extractor_events(events) -> AsyncIterator[bytes]:
        nonlocal next_block_idx, text_block_idx, text_started, text_stopped
        nonlocal thought_block_idx, thought_started, thought_stopped
        for ev_type, ev_val in events:
            if ev_type in ("start_thinking", "end_thinking", "thinking") and not include_thoughts:
                continue
            if ev_type == "start_thinking":
                if thought_started and not thought_stopped:
                    async for sig_bytes in _yield_signature(thought_block_idx):
                        yield sig_bytes
                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": thought_block_idx})
                    thought_stopped = True
                thought_block_idx = next_block_idx
                next_block_idx += 1
                yield _sse("content_block_start", {
                    "type": "content_block_start", "index": thought_block_idx,
                    "content_block": {"type": "thinking", "thinking": "", "signature": ""}
                })
                thought_started = True
                thought_stopped = False
            elif ev_type == "thinking" and ev_val:
                if not thought_started or thought_stopped:
                    thought_block_idx = next_block_idx
                    next_block_idx += 1
                    yield _sse("content_block_start", {
                        "type": "content_block_start", "index": thought_block_idx,
                        "content_block": {"type": "thinking", "thinking": "", "signature": ""}
                    })
                    thought_started = True
                    thought_stopped = False
                thought_parts.append(ev_val)
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": thought_block_idx,
                    "delta": {"type": "thinking_delta", "thinking": ev_val}
                })
            elif ev_type == "end_thinking":
                if thought_started and not thought_stopped:
                    async for sig_bytes in _yield_signature(thought_block_idx):
                        yield sig_bytes
                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": thought_block_idx})
                    thought_stopped = True
            elif ev_type == "text" and ev_val:
                if thought_started and not thought_stopped:
                    async for sig_bytes in _yield_signature(thought_block_idx):
                        yield sig_bytes
                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": thought_block_idx})
                    thought_stopped = True
                if not text_started or text_stopped:
                    text_block_idx = next_block_idx
                    next_block_idx += 1
                    yield _sse("content_block_start", {
                        "type": "content_block_start", "index": text_block_idx,
                        "content_block": {"type": "text", "text": ""}
                    })
                    text_started = True
                    text_stopped = False
                norm_content = normalizer.feed(ev_val)
                if norm_content:
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta", "index": text_block_idx,
                        "delta": {"type": "text_delta", "text": norm_content}
                    })
                    text_chunks.append(norm_content)

    async for item_type, val in _iter_safe():
        if item_type == "ping":
            yield _sse("ping", {"type": "ping", "retry": 0, "reason": "keepalive"})
            continue
        chunk = val
        if chunk is None:
            continue
        delta = chunk.choices[0].delta if chunk.choices else None

        if delta:
            reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "thought", None) or getattr(delta, "reasoning", None)
            if reasoning and include_thoughts:
                if not thought_started or thought_stopped:
                    thought_block_idx = next_block_idx
                    next_block_idx += 1
                    yield _sse("content_block_start", {
                        "type": "content_block_start", "index": thought_block_idx,
                        "content_block": {"type": "thinking", "thinking": "", "signature": ""}
                    })
                    thought_started = True
                    thought_stopped = False
                thought_parts.append(reasoning)
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": thought_block_idx,
                    "delta": {"type": "thinking_delta", "thinking": reasoning}
                })

            content_val = getattr(delta, "content", None)
            if content_val:
                async for chunk_bytes in _process_extractor_events(extractor.feed(content_val)):
                    yield chunk_bytes

            if getattr(delta, "tool_calls", None):
                if thought_started and not thought_stopped:
                    async for sig_bytes in _yield_signature(thought_block_idx):
                        yield sig_bytes
                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": thought_block_idx})
                    thought_stopped = True
                
                async for chunk_bytes in _process_extractor_events(extractor.flush()):
                    yield chunk_bytes
                
                flushed = normalizer.flush()
                if flushed:
                    if not text_started or text_stopped:
                        text_block_idx = next_block_idx
                        next_block_idx += 1
                        yield _sse("content_block_start", {
                            "type": "content_block_start", "index": text_block_idx,
                            "content_block": {"type": "text", "text": ""}
                        })
                        text_started = True
                        text_stopped = False
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta", "index": text_block_idx,
                        "delta": {"type": "text_delta", "text": flushed}
                    })
                    text_chunks.append(flushed)
                if text_started and not text_stopped:
                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": text_block_idx})
                    text_stopped = True

                for tc in delta.tool_calls:
                    if isinstance(tc, dict):
                        tc_idx = tc.get("index", 0)
                        fn = tc.get("function", {})
                        fn_name = fn.get("name", "") if isinstance(fn, dict) else ""
                        if tc_idx not in tool_buffers:
                            t_id = tc.get("id") or (f"toolu_{fn_name}_{uuid.uuid4().hex[:12]}" if fn_name else f"toolu_{uuid.uuid4().hex}")
                            tool_buffers[tc_idx] = {"id": t_id, "name": fn_name, "args": "", "started": False}
                        else:
                            if fn_name:
                                tool_buffers[tc_idx]["name"] = fn_name
                        args_value = fn.get("arguments") if isinstance(fn, dict) else None
                    else:
                        tc_idx = tc.index
                        fn_name = getattr(tc.function, "name", "") if getattr(tc, "function", None) else ""
                        if tc_idx not in tool_buffers:
                            t_id = getattr(tc, "id", f"toolu_{fn_name}_{uuid.uuid4().hex[:12]}" if fn_name else f"toolu_{uuid.uuid4().hex}")
                            tool_buffers[tc_idx] = {"id": t_id, "name": fn_name, "args": "", "started": False}
                        else:
                            if fn_name:
                                tool_buffers[tc_idx]["name"] = fn_name
                        args_value = getattr(tc.function, "arguments", None)

                    name = tool_buffers[tc_idx]["name"]
                    if name and not tool_buffers[tc_idx]["started"]:
                        tool_buffers[tc_idx]["started"] = True
                        if name != "Task":
                            tool_to_cbidx[tc_idx] = next_block_idx
                            next_block_idx += 1
                            yield _sse("content_block_start", {
                                "type": "content_block_start", "index": tool_to_cbidx[tc_idx],
                                "content_block": {"type": "tool_use", "id": tool_buffers[tc_idx]["id"],
                                                  "name": name, "input": {}}
                            })
                    if args_value:
                        if isinstance(args_value, dict):
                            args_str = json.dumps(args_value)
                        elif not isinstance(args_value, str):
                            args_str = str(args_value)
                        else:
                            args_str = args_value

                        tool_buffers[tc_idx]["args"] += args_str
                        if tc_idx in tool_to_cbidx:
                            yield _sse("content_block_delta", {
                                "type": "content_block_delta", "index": tool_to_cbidx[tc_idx],
                                "delta": {"type": "input_json_delta", "partial_json": args_str}
                            })

        finish = chunk.choices[0].finish_reason if chunk and chunk.choices else None
        if finish:
            if thought_started and not thought_stopped:
                async for sig_bytes in _yield_signature(thought_block_idx):
                    yield sig_bytes
                yield _sse("content_block_stop", {"type": "content_block_stop", "index": thought_block_idx})
                thought_stopped = True
            
            async for chunk_bytes in _process_extractor_events(extractor.flush()):
                yield chunk_bytes
            
            flushed = normalizer.flush()
            if flushed:
                if not text_started or text_stopped:
                    text_block_idx = next_block_idx
                    next_block_idx += 1
                    yield _sse("content_block_start", {
                        "type": "content_block_start", "index": text_block_idx,
                        "content_block": {"type": "text", "text": ""}
                    })
                    text_started = True
                    text_stopped = False
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": text_block_idx,
                    "delta": {"type": "text_delta", "text": flushed}
                })
                text_chunks.append(flushed)
            if text_started and not text_stopped:
                yield _sse("content_block_stop", {"type": "content_block_stop", "index": text_block_idx})
                text_stopped = True

            for tc_idx in sorted(tool_to_cbidx.keys()):
                yield _sse("content_block_stop", {"type": "content_block_stop", "index": tool_to_cbidx[tc_idx]})

            for tc_idx, buf in tool_buffers.items():
                if tc_idx not in tool_to_cbidx and buf["name"] != "Task":
                    fallback_idx = next_block_idx
                    next_block_idx += 1
                    yield _sse("content_block_start", {
                        "type": "content_block_start", "index": fallback_idx,
                        "content_block": {"type": "tool_use", "id": buf["id"],
                                          "name": buf["name"], "input": {}}
                    })
                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": fallback_idx})

            for tc_idx, buf in tool_buffers.items():
                if buf["name"] == "Task":
                    try:
                        parsed_args = json.loads(buf["args"]) if buf["args"] else {}
                        prompt_str = parsed_args.get("prompt", "") or buf["args"]
                    except Exception:
                        prompt_str = buf["args"]

                    agent_idx = next_block_idx
                    next_block_idx += 1

                    yield _sse("content_block_start", {
                        "type": "content_block_start", "index": agent_idx,
                        "content_block": {
                            "type": "agent_use",
                            "id": buf["id"],
                            "agent_type": "general-purpose",
                            "prompt": prompt_str
                        }
                    })
                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": agent_idx})

            full_text = "".join(text_chunks)
            try:
                output_tokens = await token_counter(model=model, messages=[{"role": "assistant", "content": full_text}])
            except Exception:
                output_tokens = max(1, len(full_text) // 4) if full_text else 0
            output_tokens += len(tool_buffers) * 50

            stop_reason = "tool_use" if (isinstance(finish, str) and finish.lower() == "tool_calls") or len(tool_buffers) > 0 else "end_turn"
            yield _sse("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            })

    cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
    cr = cache_usage.get("cache_read_input_tokens", 0) or 0
    await log_usage(model, key_prefix, input_tokens, output_tokens, auth_key_prefix, cc, cr)
    yield _sse("message_stop", {"type": "message_stop"})

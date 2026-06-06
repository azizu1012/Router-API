import asyncio
import json
import uuid
from typing import Any, AsyncIterator, Dict, List

import litellm

from src.core.usage_logger import log_usage
from src.api.claude_proxy.utils import _sse, _get_simulated_cache_usage, is_claude_code_body, is_sub_agent_body


async def _process_anthropic_stream(
    gen_iterator: AsyncIterator[Any], first_chunk: Any,
    model: str, input_tokens: int, key_prefix: str = "",
    auth_key_prefix: str = "",
    body: Dict[str, Any] = None,
) -> AsyncIterator[bytes]:
    msg_id = "msg_" + uuid.uuid4().hex

    adjusted_input_tokens = input_tokens
    cache_usage = _get_simulated_cache_usage(body or {}, adjusted_input_tokens)
    yield _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {
                "input_tokens": adjusted_input_tokens,
                "output_tokens": 0,
                **cache_usage
            },
        },
    })

    next_block_idx = 0
    text_block_idx = None
    text_started = False
    text_stopped = False
    tool_buffers: Dict[int, Dict[str, Any]] = {}
    tool_to_cbidx: Dict[int, int] = {}
    text_chunks: List[str] = []
    output_tokens = 0

    is_sub = is_sub_agent_body(body)
    warning_threshold = 178000 if is_claude_code_body(body) else 170000
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
            yield first_chunk
        while True:
            try:
                c = await gen_iterator.__anext__()
                yield c
            except StopAsyncIteration:
                break

    async for chunk in _iter_safe():
        delta = chunk.choices[0].delta if chunk.choices else None

        if delta:
            if getattr(delta, "content", None):
                if not text_started:
                    text_block_idx = next_block_idx
                    next_block_idx += 1
                    yield _sse("content_block_start", {
                        "type": "content_block_start", "index": text_block_idx,
                        "content_block": {"type": "text", "text": ""}
                    })
                    text_started = True
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": text_block_idx,
                    "delta": {"type": "text_delta", "text": delta.content}
                })
                text_chunks.append(delta.content)

            if getattr(delta, "tool_calls", None):
                if text_started and not text_stopped:
                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": text_block_idx})
                    text_stopped = True

                for tc in delta.tool_calls:
                    tc_idx = tc.index
                    if tc_idx not in tool_buffers:
                        t_id = getattr(tc, "id", f"toolu_{uuid.uuid4().hex}")
                        t_name = getattr(tc.function, "name", "") if getattr(tc, "function", None) else ""
                        tool_buffers[tc_idx] = {"id": t_id, "name": t_name, "args": "", "started": False}

                    if getattr(tc.function, "name", None):
                        tool_buffers[tc_idx]["name"] = tc.function.name

                    args_value = getattr(tc.function, "arguments", None)
                    if args_value:
                        if isinstance(args_value, dict):
                            args_str = json.dumps(args_value)
                        elif not isinstance(args_value, str):
                            args_str = str(args_value)
                        else:
                            args_str = args_value

                        if not tool_buffers[tc_idx]["started"]:
                            tool_buffers[tc_idx]["started"] = True
                            tool_to_cbidx[tc_idx] = next_block_idx
                            next_block_idx += 1
                            yield _sse("content_block_start", {
                                "type": "content_block_start", "index": tool_to_cbidx[tc_idx],
                                "content_block": {"type": "tool_use", "id": tool_buffers[tc_idx]["id"],
                                                  "name": tool_buffers[tc_idx]["name"], "input": {}}
                            })

                        tool_buffers[tc_idx]["args"] += args_str
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta", "index": tool_to_cbidx[tc_idx],
                            "delta": {"type": "input_json_delta", "partial_json": args_str}
                        })

        finish = chunk.choices[0].finish_reason if chunk.choices else None
        if finish:
            if text_started and not text_stopped:
                yield _sse("content_block_stop", {"type": "content_block_stop", "index": text_block_idx})

            for tc_idx in sorted(tool_to_cbidx.keys()):
                yield _sse("content_block_stop", {"type": "content_block_stop", "index": tool_to_cbidx[tc_idx]})

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
                output_tokens = await asyncio.to_thread(
                    litellm.token_counter,
                    model=model,
                    messages=[{"role": "assistant", "content": full_text}]
                )
            except Exception:
                output_tokens = max(1, len(full_text) // 4) if full_text else 0
            output_tokens += len(tool_buffers) * 50

            stop_reason = "tool_use" if isinstance(finish, str) and finish.lower() == "tool_calls" else "end_turn"
            yield _sse("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            })

    cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
    cr = cache_usage.get("cache_read_input_tokens", 0) or 0
    await log_usage(model, key_prefix, input_tokens, output_tokens, auth_key_prefix, cc, cr)
    yield _sse("message_stop", {"type": "message_stop"})

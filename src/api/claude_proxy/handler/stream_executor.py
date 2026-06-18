import asyncio
import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional

from src.core.providers.litellm_wrapper import acompletion, token_counter
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.usage_logger import log_usage
from src.logical_HQ_translator import (
    _get_simulated_cache_usage,
    is_sub_agent_body,
    is_claude_code_body,
    _sse,
    _tool_call_names,
    normalize_text,
)
from src.api.claude_proxy.stream import _process_anthropic_stream
from .nonstream_executor import _resolve_gemini_with_tools_stream

async def _execute_stream(proxy_instance: Any, kwargs: Dict[str, Any], api_key: str, model_id: str, model_alias: str, input_tokens: int, pool: Any, body: Dict[str, Any], auth_key_prefix: str = "", account: Optional[Dict[str, Any]] = None) -> Any:
    tools = kwargs.get("tools") or []
    has_websearch = any(
        tool.get("function", {}).get("name") == "WebSearch"
        for tool in tools
    )
    has_webfetch = any(
        tool.get("function", {}).get("name") == "WebFetch"
        for tool in tools
    )

    if has_websearch or has_webfetch:
        kwargs_ns = {k: v for k, v in kwargs.items() if k != "stream"}

        async def _nonstream_wrapper():
            kp = api_key[-8:] if api_key else ""
            fetch_task = None
            try:
                msg_id = "msg_" + uuid.uuid4().hex
                adjusted_input_tokens = input_tokens
                cache_usage = _get_simulated_cache_usage(body, adjusted_input_tokens)
                t0_wait = asyncio.get_event_loop().time()

                yield _sse("message_start", {
                    "type": "message_start",
                    "message": {
                        "id": msg_id, "type": "message", "role": "assistant", "model": body.get("model") or model_alias,
                        "content": [], "stop_reason": None, "stop_sequence": None,
                        "usage": {
                            "input_tokens": adjusted_input_tokens,
                            "output_tokens": 0,
                            **cache_usage
                        },
                    },
                })

                body.get("include_thoughts", True)
                block_idx = 0
                thinking_active = False
                text_active = False
                thinking_buf: List[str] = []
                text_buf: List[str] = []
                stream_tool_calls: List[Dict[str, Any]] = []
                stream_fr = "stop"
                stream_thought: str = ""
                stream_accumulated_text: str = ""
                t_last_chunk = asyncio.get_event_loop().time()
                t0_wait = t_last_chunk

                async def _iter_events():
                    it = _resolve_gemini_with_tools_stream(
                        kwargs_ns, body, proxy_instance, auth_key_prefix=auth_key_prefix, account=account
                    ).__aiter__()
                    while True:
                        try:
                            while True:
                                try:
                                    evt = await asyncio.wait_for(asyncio.shield(it.__anext__()), timeout=4.0)
                                    yield ("event", evt)
                                    break
                                except asyncio.TimeoutError:
                                    yield ("ping", None)
                        except StopAsyncIteration:
                            break

                async for item_type, val in _iter_events():
                    if item_type == "ping":
                        yield _sse("ping", {"type": "ping", "retry": 0, "reason": "keepalive"})
                        continue
                    if val is None:
                        continue
                    
                    evt_type, *evt_vals = val
                    now = asyncio.get_event_loop().time()
                    if evt_type == "reasoning":
                        val = str(evt_vals[0] or "")
                        if not thinking_active:
                            yield _sse("content_block_start", {
                                "type": "content_block_start", "index": block_idx,
                                "content_block": {"type": "thinking", "thinking": "", "signature": ""}
                            })
                            thinking_active = True
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta", "index": block_idx,
                            "delta": {"type": "thinking_delta", "thinking": val}
                        })
                        thinking_buf.append(val)
                        t_last_chunk = now
                    elif evt_type == "text":
                        val = str(evt_vals[0] or "")
                        if thinking_active:
                            full_thought = "".join(thinking_buf)
                            sig = "gmni_" + hashlib.sha256(full_thought.encode()).hexdigest()[:60]
                            yield _sse("content_block_delta", {
                                "type": "content_block_delta", "index": block_idx,
                                "delta": {"type": "signature_delta", "signature": sig}
                            })
                            yield _sse("content_block_stop", {"type": "content_block_stop", "index": block_idx})
                            block_idx += 1
                            thinking_active = False
                        if not text_active:
                            yield _sse("content_block_start", {
                                "type": "content_block_start", "index": block_idx,
                                "content_block": {"type": "text", "text": ""}
                            })
                            text_active = True
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta", "index": block_idx,
                            "delta": {"type": "text_delta", "text": normalize_text(val)}
                        })
                        text_buf.append(val)
                        t_last_chunk = now
                    elif evt_type == "result":
                        stream_accumulated_text = str(evt_vals[0] or "")
                        raw_tc = evt_vals[1] if len(evt_vals) > 1 else []
                        stream_tool_calls = raw_tc if isinstance(raw_tc, list) else []
                        stream_fr = str(evt_vals[2] or "stop") if len(evt_vals) > 2 else "stop"
                        stream_thought = str(evt_vals[3] or "") if len(evt_vals) > 3 else ""

                text = stream_accumulated_text or "".join(text_buf)
                stream_thought or "".join(thinking_buf)
                tool_calls = stream_tool_calls
                finish_reason = stream_fr

                elapsed_total = asyncio.get_event_loop().time() - t0_wait
                logger.info(
                    "[ToolResolve Stream] model=%s elapsed=%.2fs text_len=%d emitted_tools=%d tool_names=%s websearch_capable=true",
                    model_alias, elapsed_total, len(text), len(tool_calls), _tool_call_names(tool_calls)
                )

                # Close any still-open blocks
                if thinking_active:
                    full_thought = "".join(thinking_buf)
                    sig = "gmni_" + hashlib.sha256(full_thought.encode()).hexdigest()[:60]
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta", "index": block_idx,
                        "delta": {"type": "signature_delta", "signature": sig}
                    })
                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": block_idx})
                    block_idx += 1
                    thinking_active = False
                if text_active:
                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": block_idx})
                    block_idx += 1
                    text_active = False

                is_sub = is_sub_agent_body(body)
                warning_threshold = 178000 if is_claude_code_body(body) else 170000
                if input_tokens > warning_threshold and not is_sub:
                    warning_message = (
                        "\n⚠️  [ROUTER-API WARNING] Context is extremely large (%.1fk tokens). "
                        "Please run '/compact' in your terminal immediately to avoid 250k TPM rate limits! ⚠️\n"
                        "⚠️  [CẢNH BÁO] Context hiện tại cực kỳ lớn (%.1fk tokens). "
                        "Vui lòng chạy lệnh '/compact' ngay lập tức để tránh bị lỗi giới hạn 250k TPM! ⚠️\n\n"
                    ) % (input_tokens / 1000.0, input_tokens / 1000.0)
                    text = warning_message + text
                    if text_active:
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta", "index": block_idx,
                            "delta": {"type": "text_delta", "text": warning_message}
                        })
                    else:
                        yield _sse("content_block_start", {
                            "type": "content_block_start", "index": block_idx,
                            "content_block": {"type": "text", "text": ""}
                        })
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta", "index": block_idx,
                            "delta": {"type": "text_delta", "text": warning_message}
                        })
                        yield _sse("content_block_stop", {"type": "content_block_stop", "index": block_idx})
                        block_idx += 1

                for tc in tool_calls:
                    try:
                        args = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
                    except Exception:
                        args = {}
                    if tc["name"] == "Task":
                        yield _sse("content_block_start", {
                            "type": "content_block_start", "index": block_idx,
                            "content_block": {"type": "agent_use", "id": tc["id"], "agent_type": "general-purpose", "prompt": args.get("prompt", "") or str(args)}
                        })
                        yield _sse("content_block_stop", {"type": "content_block_stop", "index": block_idx})
                    else:
                        yield _sse("content_block_start", {
                            "type": "content_block_start", "index": block_idx,
                            "content_block": {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": {}}
                        })
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta", "index": block_idx,
                            "delta": {"type": "input_json_delta", "partial_json": tc["arguments"]}
                        })
                        yield _sse("content_block_stop", {"type": "content_block_stop", "index": block_idx})
                    block_idx += 1
                try:
                    out_tokens = await token_counter(model=kwargs.get("model", "gemini/gemini-1.5-pro"), messages=[{"role": "assistant", "content": text}])
                except Exception:
                    out_tokens = max(1, len(text) // 4) if text else 1
                out_tokens += len(tool_calls) * 50
                stop_reason = "tool_use" if tool_calls else ("length" if "max" in str(finish_reason).lower() else "end_turn")
                yield _sse("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": out_tokens},
                })
                cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
                cr = cache_usage.get("cache_read_input_tokens", 0) or 0
                await log_usage(model_id, kp, input_tokens, out_tokens, auth_key_prefix, cc, cr)
                router.record_success(api_key, model_id)
                if pool:
                    pool.record_success()
                yield _sse("message_stop", {"type": "message_stop"})
            except asyncio.CancelledError:
                logger.warning("[NonStream Cancelled] Client disconnected/cancelled request prematurely for model=%s, key=...%s", model_alias, kp)
                if fetch_task and not fetch_task.done():
                    fetch_task.cancel()
                raise
            except Exception as e:
                logger.error("[NonStream Exception] Unexpected error for model=%s, key=...%s: %s", model_alias, kp, e, exc_info=True)
                if fetch_task and not fetch_task.done():
                    fetch_task.cancel()
                raise
            finally:
                router.release_key(api_key)

        return _nonstream_wrapper()

    kwargs["stream"] = True

    async def _stream_wrapper():
        kp = api_key[-8:] if api_key else ""
        fetch_task = None
        try:
            async def _fetch_stream():
                g = await acompletion(**kwargs)
                fc = await g.__anext__()
                return g, fc

            fetch_task = asyncio.create_task(_fetch_stream())

            t0_wait = asyncio.get_event_loop().time()
            ping_count = 0
            while not fetch_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(fetch_task), timeout=3.0)
                    break
                except asyncio.TimeoutError:
                    elapsed = asyncio.get_event_loop().time() - t0_wait
                    ping_count += 1
                    if ping_count % 5 == 1:
                        logger.info("[Stream Keepalive] Still waiting for %s response (elapsed=%.1fs), sending ping", model_alias, elapsed)
                    yield _sse("ping", {"type": "ping", "retry": 0, "reason": "keepalive"})

            gen, first_chunk = await fetch_task
            ttfb = asyncio.get_event_loop().time() - t0_wait
            logger.info("[Stream] model=%s ttfb=%.2fs", model_alias, ttfb)

            async for chunk in _process_anthropic_stream(gen, first_chunk, body.get("model") or model_alias, input_tokens, kp, auth_key_prefix, body):
                yield chunk
            router.record_success(api_key, model_id)
            if pool:
                pool.record_success()
        except asyncio.CancelledError:
            logger.warning("[Stream Cancelled] Client disconnected/cancelled stream prematurely for model=%s, key=...%s", model_alias, kp)
            if fetch_task and not fetch_task.done():
                fetch_task.cancel()
            raise
        except Exception as e:
            logger.error("[Stream Exception] Unexpected stream-level error for model=%s, key=...%s: %s", model_alias, kp, e, exc_info=True)
            if fetch_task and not fetch_task.done():
                fetch_task.cancel()
            raise
        finally:
            router.release_key(api_key)

    return _stream_wrapper()



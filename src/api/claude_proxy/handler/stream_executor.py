import asyncio
import json
import uuid
from typing import Any, Dict, List, AsyncIterator, Optional

from src.core.providers.litellm_wrapper import acompletion, token_counter
from fastapi import HTTPException
from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.limits import apply_error_penalty
from src.core.usage_logger import log_usage
from src.api.claude_proxy.utils import (
    _resolve_model,
    _get_simulated_cache_usage,
    is_sub_agent_body,
    is_claude_code_body,
    _sse,
    _tool_call_names,
    _emergency_truncate_to_limit,
    _dict_to_sse_events,
    _retry_delay,
    normalize_text,
)
from src.api.claude_proxy.stream import _process_anthropic_stream
from .helpers import get_system_status_summary, _classify_error_reason, _reinforce_messages_for_retry
from .nonstream_executor import _resolve_gemini_with_tools

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

                async def _fetch_websearch():
                    return await _resolve_gemini_with_tools(kwargs_ns, body, proxy_instance, auth_key_prefix=auth_key_prefix, account=account)

                fetch_task = asyncio.create_task(_fetch_websearch())
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
                            logger.info("[Stream Keepalive] Still waiting for %s response (WebSearch capable) (elapsed=%.1fs), sending ping", model_alias, elapsed)
                        yield _sse("ping", {"type": "ping", "retry": 0, "reason": "keepalive"})

                text, tool_calls, finish_reason, _ = await fetch_task
                elapsed = asyncio.get_event_loop().time() - t0_wait
                logger.info(
                    "[ToolResolve Stream] model=%s elapsed=%.2fs text_len=%d emitted_tools=%d tool_names=%s websearch_capable=true",
                    model_alias, elapsed, len(text), len(tool_calls), _tool_call_names(tool_calls)
                )

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

                block_idx = 0
                if text:
                    yield _sse("content_block_start", {
                        "type": "content_block_start", "index": block_idx,
                        "content_block": {"type": "text", "text": ""}
                    })
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta", "index": block_idx,
                        "delta": {"type": "text_delta", "text": normalize_text(text)}
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

async def _stream_with_pool(
    proxy_instance: Any, body: Dict[str, Any], openai_messages: List[Dict[str, Any]], openai_tools: List[Dict[str, Any]],
    pool: Any, model_alias: str, auth_key_prefix: str = "", account: Optional[Dict[str, Any]] = None
) -> AsyncIterator[bytes]:
    committed = False
    pool.start()
    while not pool.exhausted:
        yield _sse("ping", {"type": "ping", "retry": pool.total_attempts, "reason": "initial"})
        actual_alias = pool.current_model
        api_key_val = None
        saved_key = None
        try:
            est_input = len(str(openai_messages)) // 4
            max_output = min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS)
            estimated_tokens = est_input + max_output
            model_alias_val, model_id_val, api_key_val, litellm_model_val, reservation = await _resolve_model(body, actual_alias, account=account, estimated_tokens=estimated_tokens, retry_attempt=pool.total_attempts, pool_mode=True)
            logger.info(
                "[Pool Reserve Stream] Reserved key ...%s for model_alias=%s (resolved model_id=%s) | Attempt: %d (remaining=%ds) | Estimated tokens: %d",
                api_key_val[-8:] if api_key_val else "N/A", actual_alias, model_id_val, pool.total_attempts + 1, int(pool.remaining_time()), estimated_tokens
            )
            try:
                try:
                    input_tokens = await token_counter(model=litellm_model_val, messages=openai_messages)
                except Exception:
                    input_tokens = max(1, len(str(openai_messages)) // 4)

                is_lite = "lite" in str(litellm_model_val).lower()
                limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
                openai_messages[:] = _emergency_truncate_to_limit(openai_messages, limit)
                try:
                    input_tokens = await token_counter(model=litellm_model_val, messages=openai_messages)
                except Exception:
                    input_tokens = max(1, len(str(openai_messages)) // 4)

                max_output = min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS)
                has_quota = await router.acquire_quota(input_tokens + max_output, actual_alias)
                if not has_quota:
                    apply_error_penalty(api_key_val, "rate_limit_rpm_tpm", model_id_val)
                    router.freeze_key(api_key_val, 15, model_id_val, "rate_limit")
                    if pool.record_failure(actual_alias, "rate_limit"):
                        if not pool.swap():
                            if pool.exhausted:
                                break
                            wait = min(15.0, pool.remaining_time())
                            for _ in range(int(wait)):
                                yield _sse("ping", {"type": "ping", "retry": 0, "reason": "backoff"})
                                await asyncio.sleep(1)
                            pool.reset_cycle()
                            continue
                    yield _sse("ping", {"type": "ping", "retry": pool.total_attempts, "reason": "rate_limit"})
                    await asyncio.sleep(_retry_delay(pool.total_attempts))
                    continue

                attempt_val = pool.total_attempts
                reinforced_messages = _reinforce_messages_for_retry(openai_messages, attempt_val)
                kwargs = proxy_instance._prepare_litellm_kwargs(
                    litellm_model_val=litellm_model_val,
                    reinforced_messages=reinforced_messages,
                    api_key_val=api_key_val,
                    max_output=max_output,
                    body=body,
                    openai_tools=openai_tools,
                    reservation=reservation,
                    is_stream=True
                )

                gen = await _execute_stream(proxy_instance, kwargs, api_key_val, model_id_val, model_alias_val, input_tokens, pool, body, auth_key_prefix, account=account)
                saved_key = api_key_val
                api_key_val = None
                async for chunk in gen:
                    if not chunk.startswith(b"event: ping") and not chunk.startswith(b"event: message_start"):
                        committed = True
                    yield chunk
                return

            finally:
                if api_key_val:
                    router.release_key(api_key_val)

        except HTTPException as e:
            if committed:
                logger.error("[Stream Pool Exception] HTTP exception after stream committed: %s", e)
                yield _sse("error", {"type": "error", "error": {"type": "api_error", "message": f"Stream error: {e}"}})
                return
            if e.status_code == 503:
                raise
            _err_key = api_key_val or saved_key
            if _err_key:
                router.freeze_key(_err_key, 15, model_id_val, "rate_limit")
                apply_error_penalty(_err_key, "rate_limit", model_id_val)
            router.record_failure("rate_limit")
            if pool.record_failure(actual_alias, "rate_limit"):
                if not pool.swap():
                    if pool.exhausted:
                        break
                    wait = min(15.0, pool.remaining_time())
                    for _ in range(int(wait)):
                        yield _sse("ping", {"type": "ping", "retry": 0, "reason": "backoff"})
                        await asyncio.sleep(1)
                    pool.reset_cycle()
                    continue
            yield _sse("ping", {"type": "ping", "retry": pool.total_attempts, "reason": "rate_limit"})
            await asyncio.sleep(_retry_delay(pool.total_attempts))
        except asyncio.CancelledError:
            logger.warning("[Pool Cancelled] Stream cancelled by client during pool retry loop for alias=%s (attempt %d)", actual_alias, pool.total_attempts)
            raise
        except Exception as e:
            if committed:
                logger.error("[Stream Pool Exception] Exception after stream committed: %s", e)
                yield _sse("error", {"type": "error", "error": {"type": "api_error", "message": f"Stream error: {e}"}})
                return
            error_text = str(e).lower()

            if "400" in error_text and "failed_precondition" not in error_text and ("invalid_argument" in error_text or "bad_request" in error_text):
                logger.error("[Bad Request] Schema/Prompt Error: %s", error_text[:200])
                if api_key_val:
                    router.freeze_key(api_key_val, 2, model_id_val, "bad_request_spam_prevent")
                yield _sse("error", {"type": "error", "error": {"type": "invalid_request_error", "message": f"LLM rejected payload (HTTP 400): {error_text[:200]}"}})
                return

            if "400" in error_text and "failed_precondition" in error_text:
                logger.error("[Failed Precondition] Billing issue with key ...%s: %s", (api_key_val or "N/A")[-4:], error_text[:200])
                if api_key_val:
                    router.freeze_key(api_key_val, 300, model_id_val, "billing_error")
                    apply_error_penalty(api_key_val, "billing_error", model_id_val)
                router.record_failure("billing_error")
                if pool.record_failure(actual_alias, "billing_error"):
                    if not pool.swap():
                        if pool.exhausted:
                            break
                        wait = min(15.0, pool.remaining_time())
                        for _ in range(int(wait)):
                            yield _sse("ping", {"type": "ping", "retry": 0, "reason": "backoff"})
                            await asyncio.sleep(1)
                        pool.reset_cycle()
                        continue
                yield _sse("ping", {"type": "ping", "retry": pool.total_attempts, "reason": "billing_error"})
                await asyncio.sleep(_retry_delay(pool.total_attempts))
                continue

            if "499" in error_text or "cancelled" in error_text:
                logger.warning("[Cancel] Request cancelled by client, stopping stream for key ...%s", (api_key_val or "N/A")[-4:])
                return

            reason = _classify_error_reason(error_text, api_key_val, model_id_val)
            if reason == "rate_limit":
                router.record_429()

            _err_key = api_key_val or saved_key or "N/A"
            if reason == "unknown_error":
                logger.error("[Pool Failure Detail] Unexpected error on key ...%s: %s",
                             _err_key[-4:] if len(_err_key) >= 4 else _err_key, e, exc_info=True)

            if _err_key:
                router.freeze_key(_err_key, 0, model_id_val, reason)
                if reason not in ("bad_request_spam_prevent", "invalid_key"):
                    apply_error_penalty(_err_key, reason, model_id_val)
            router.record_failure(reason)
            _err_key2 = api_key_val or saved_key or "N/A"
            failure_state = pool.failure_state_after_next(actual_alias, reason)
            logger.warning("[Pool Failure] Key ...%s failed on model %s | Reason: %s | Action: %s (model failures: %d/%d, pool total attempts: %d, remaining=%ds)",
                          _err_key2[-4:] if len(_err_key2) >= 4 else _err_key2, actual_alias, reason, failure_state["action"],
                          failure_state["failures_after"], failure_state["threshold"],
                          pool.total_attempts + 1, int(pool.remaining_time()))
            if pool.record_failure(actual_alias, reason):
                if not pool.swap():
                    if pool.exhausted:
                        break
                    wait = min(15.0, pool.remaining_time())
                    for _ in range(int(wait)):
                        yield _sse("ping", {"type": "ping", "retry": 0, "reason": "backoff"})
                        await asyncio.sleep(1)
                    pool.reset_cycle()
                    continue
            yield _sse("ping", {"type": "ping", "retry": pool.total_attempts, "reason": reason})
            await asyncio.sleep(_retry_delay(pool.total_attempts))

    logger.warning("[Pool Exhausted] Request timed out after %.1fs.", pool.elapsed)
    summary_text = get_system_status_summary(model_alias)
    fake_result = {
        "id": "msg_err_" + uuid.uuid4().hex[:8],
        "type": "message",
        "role": "assistant",
        "model": body.get("model") or model_alias,
        "content": [{"type": "text", "text": summary_text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": len(summary_text) // 4,
            "output_tokens": len(summary_text) // 4,
        },
    }
    for chunk in _dict_to_sse_events(fake_result):
        yield chunk
    return

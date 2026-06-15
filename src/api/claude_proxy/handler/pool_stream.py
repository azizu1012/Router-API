import asyncio
import uuid
from typing import Any, Dict, List, AsyncIterator, Optional

from fastapi import HTTPException
from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.limits import apply_error_penalty
from src.core.providers import _custom_endpoint_manager as endpoint_manager
from src.logical_HQ_translator import (
    _resolve_model,
    _sse,
    _emergency_truncate_to_limit,
    _dict_to_sse_events,
    _retry_delay,
)
from .helpers import get_system_status_summary, _classify_error_reason, _reinforce_messages_for_retry
from .stream_executor import _execute_stream


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
        model_id_val = ""
        reservation = {}
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
                from src.logical_HQ_translator import save_resolved_model_for_cwd
                save_resolved_model_for_cwd(body.get("system", ""), model_alias_val, model_id_val)
            except Exception as ex_sync:
                logger.error("[Statusline Sync Error] Failed to call sync helper in stream: %s", ex_sync)
            try:
                try:
                    from src.core.providers.litellm_wrapper import token_counter
                    input_tokens = await token_counter(model=litellm_model_val, messages=openai_messages)
                except Exception:
                    input_tokens = max(1, len(str(openai_messages)) // 4)

                is_lite = "lite" in str(litellm_model_val).lower()
                limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
                openai_messages[:] = _emergency_truncate_to_limit(openai_messages, limit)
                try:
                    from src.core.providers.litellm_wrapper import token_counter
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
            is_custom = reservation.get("provider") == "custom" if "reservation" in locals() else False
            if is_custom:
                logger.warning("[CustomEndpoint Stream] Failed on custom endpoint %s: %s, falling back to Gemini pool", model_id_val, e)
                ep_name = reservation.get("name", actual_alias)
                endpoint_manager.mark_endpoint_failure(ep_name)
                if pool.record_failure(actual_alias, "custom_endpoint_error"):
                    if not pool.swap():
                        if pool.exhausted:
                            break
                        wait = min(15.0, pool.remaining_time())
                        for _ in range(int(wait)):
                            yield _sse("ping", {"type": "ping", "retry": 0, "reason": "backoff"})
                            await asyncio.sleep(1)
                        pool.reset_cycle()
                        continue
                yield _sse("ping", {"type": "ping", "retry": pool.total_attempts, "reason": "custom_endpoint_error"})
                await asyncio.sleep(_retry_delay(pool.total_attempts))
                continue

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

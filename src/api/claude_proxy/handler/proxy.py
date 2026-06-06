import asyncio
from typing import Any, Dict, List, Optional

import litellm
from fastapi import HTTPException

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.limits import apply_error_penalty
from src.api.claude_proxy.utils import (
    _resolve_model,
    _retry_delay,
    should_compact,
    _compact_conversation,
    _emergency_truncate_to_limit,
)

from .helpers import _classify_error_reason, _reinforce_messages_for_retry
from .nonstream_executor import _execute_nonstream
from .stream_executor import _execute_stream

from .proxy_nonstream import ClaudeProxyNonstreamMixin
from .proxy_stream import ClaudeProxyStreamMixin

class ClaudeProxy(ClaudeProxyNonstreamMixin, ClaudeProxyStreamMixin):

    def _prepare_litellm_kwargs(
        self, litellm_model_val: str, reinforced_messages: List[Dict[str, Any]],
        api_key_val: Optional[str], max_output: int, body: Dict[str, Any],
        openai_tools: List[Dict[str, Any]], reservation: Dict[str, Any], is_stream: bool
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": litellm_model_val,
            "messages": reinforced_messages,
            "api_key": api_key_val,
            "max_tokens": max_output,
            "temperature": float(body.get("temperature", 0.7)),
            "stream": is_stream,
            "request_timeout": config.REQUEST_TIMEOUT_SECONDS,
        }
        if openai_tools:
            kwargs["tools"] = openai_tools
            
        if reservation.get("provider") == "custom":
            kwargs["api_base"] = reservation["api_base"]
        return kwargs

    async def _call_lm_with_retry(
        self, body: Dict[str, Any], openai_messages: List[Dict[str, Any]], openai_tools: List[Dict[str, Any]],
        pool: Any, model_alias: str, is_stream: bool = False,
        auth_key_prefix: str = "",
        account: Optional[Dict[str, Any]] = None,
    ) -> Any:
        while not pool.exhausted:
            actual_alias = pool.current_model
            model_alias_val = None
            api_key_val = None
            litellm_model_val = None

            try:
                est_input = len(str(openai_messages)) // 4
                max_output = min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS)
                estimated_tokens = est_input + max_output
                model_alias_val, model_id_val, api_key_val, litellm_model_val, reservation = await _resolve_model(body, actual_alias, account=account, estimated_tokens=estimated_tokens, retry_attempt=pool.total_attempts, pool_mode=True)
                logger.info(
                    "[Pool Reserve] Reserved key ...%s for model_alias=%s (resolved model_id=%s) | Attempt: %d/%d | Estimated tokens: %d",
                    api_key_val[-8:] if api_key_val else "N/A", actual_alias, model_id_val, pool.total_attempts + 1, pool.max_attempts, estimated_tokens
                )
            except HTTPException as e:
                if e.status_code in (429, 503):
                    logger.info("[Pool Retry] _resolve_model returned %d for %s (cooldown=%s), retrying (attempt %d/%d)",
                                e.status_code, actual_alias,
                                "global cooldown" if e.status_code == 503 else "all keys frozen",
                                pool.total_attempts + 1, pool.max_attempts)
                    if pool.record_failure(actual_alias, "rate_limit"):
                        pool.swap()
                    await asyncio.sleep(_retry_delay(pool.total_attempts))
                    continue
                raise

            try:
                try:
                    try:
                        input_tokens = await asyncio.to_thread(litellm.token_counter, model=litellm_model_val, messages=openai_messages)
                    except Exception:
                        input_tokens = max(1, len(str(openai_messages)) // 4)

                    attempt_val = pool.total_attempts
                    if should_compact(openai_messages, input_tokens, retry_attempt=attempt_val):
                        openai_messages[:] = await _compact_conversation(body, openai_messages, openai_tools, input_tokens, retry_attempt=attempt_val)
                        try:
                            input_tokens = await asyncio.to_thread(litellm.token_counter, model=litellm_model_val, messages=openai_messages)
                        except Exception:
                            input_tokens = max(1, len(str(openai_messages)) // 4)

                    is_lite = "lite" in str(litellm_model_val).lower()
                    limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
                    if attempt_val >= 10:
                        _div = max(3, attempt_val - 7)
                        limit = max(20000, limit // _div)
                    openai_messages[:] = _emergency_truncate_to_limit(openai_messages, limit)
                    try:
                        input_tokens = await asyncio.to_thread(litellm.token_counter, model=litellm_model_val, messages=openai_messages)
                    except Exception:
                        input_tokens = max(1, len(str(openai_messages)) // 4)

                    max_output = min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS)
                    has_quota = await router.acquire_quota(input_tokens + max_output, actual_alias)
                    if not has_quota:
                        apply_error_penalty(api_key_val, "rate_limit_rpm_tpm", model_id_val)
                        router.freeze_key(api_key_val, 15, model_id_val, "rate_limit")
                        if pool.record_failure(actual_alias, "rate_limit"):
                            pool.swap()
                        await asyncio.sleep(_retry_delay(pool.total_attempts))
                        continue

                    reinforced_messages = _reinforce_messages_for_retry(openai_messages, attempt_val)
                    kwargs = self._prepare_litellm_kwargs(
                        litellm_model_val=litellm_model_val,
                        reinforced_messages=reinforced_messages,
                        api_key_val=api_key_val,
                        max_output=max_output,
                        body=body,
                        openai_tools=openai_tools,
                        reservation=reservation,
                        is_stream=is_stream
                    )

                    if is_stream:
                        gen = await _execute_stream(self, kwargs, api_key_val, model_id_val, actual_alias, input_tokens, pool, body, auth_key_prefix, account=account)
                        api_key_val = None
                        return gen
                    else:
                        return await _execute_nonstream(self, kwargs, api_key_val, model_id_val, actual_alias, input_tokens, pool, body, auth_key_prefix, account=account)
                finally:
                    if api_key_val:
                        router.release_key(api_key_val)

            except HTTPException:
                raise
            except Exception as e:
                error_text = str(e).lower()

                if "400" in error_text and "failed_precondition" not in error_text and ("invalid_argument" in error_text or "bad_request" in error_text):
                    logger.error("[Bad Request] Schema/Prompt Error: %s", error_text[:200])
                    if api_key_val:
                        router.freeze_key(api_key_val, 2, model_id_val, "bad_request_spam_prevent")
                    raise HTTPException(status_code=400, detail={
                        "type": "error",
                        "error": {"type": "invalid_request_error", "message": f"LLM rejected payload (HTTP 400): {error_text[:200]}"},
                    })

                if "400" in error_text and "failed_precondition" in error_text:
                    logger.error("[Failed Precondition] Billing issue with key ...%s: %s", (api_key_val or "N/A")[-4:], error_text[:200])
                    if api_key_val:
                        router.freeze_key(api_key_val, 300, model_id_val, "billing_error")
                        apply_error_penalty(api_key_val, "billing_error", model_id_val)
                    router.record_failure("billing_error")
                    if pool.record_failure(actual_alias, "billing_error"):
                        pool.swap()
                    await asyncio.sleep(_retry_delay(pool.total_attempts))
                    continue

                if "499" in error_text or "cancelled" in error_text:
                    raise HTTPException(status_code=503, detail={
                        "type": "error", "error": {"type": "api_error", "message": "Request cancelled by client"}
                    })

                duration = config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS
                reason = _classify_error_reason(error_text, api_key_val, model_id_val)
                if reason == "rate_limit":
                    router.record_429()

                if reason == "unknown_error":
                    logger.error("[Pool Failure Detail] Unexpected error on key ...%s: %s", (api_key_val or "N/A")[-4:], e, exc_info=True)

                if api_key_val:
                    router.freeze_key(api_key_val, duration, model_id_val, reason)
                    if reason not in ("bad_request_spam_prevent", "invalid_key"):
                        apply_error_penalty(api_key_val, reason, model_id_val)
                router.record_failure(reason)
                failure_state = pool.failure_state_after_next(actual_alias, reason)
                logger.warning("[Pool Failure] Key ...%s failed on model %s | Reason: %s | Action: %s (model failures: %d/%d, pool total attempts: %d/%d)",
                              (api_key_val or "N/A")[-4:], actual_alias, reason, failure_state["action"],
                              failure_state["failures_after"], failure_state["threshold"],
                              pool.total_attempts + 1, pool.max_attempts)
                if pool.record_failure(actual_alias, reason):
                    pool.swap()
                await asyncio.sleep(_retry_delay(pool.total_attempts))

        logger.warning("[Pool Exhausted] All model swap attempts failed after %d total attempts.", pool.max_attempts)
        raise HTTPException(status_code=503, detail={
            "type": "error", "error": {"type": "api_error", "message": "Pool exhausted."}
        })

claude_proxy = ClaudeProxy()

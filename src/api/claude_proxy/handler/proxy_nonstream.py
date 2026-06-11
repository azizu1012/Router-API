import asyncio
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException
from src.core.providers.litellm_wrapper import token_counter

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.limits import apply_error_penalty
from src.api.claude_proxy.utils import (
    _resolve_model,
    _retry_delay,
    _convert_messages,
    _intercept_sub_agent,
    _emergency_truncate_to_limit,
)

from .helpers import get_system_status_summary, _reinforce_messages_for_retry
from src.core.providers.gemini.error import classify
from .compaction import _pre_compact_and_truncate
from .nonstream_executor import _execute_nonstream

class ClaudeProxyNonstreamMixin:

    async def create_message(self, body: Dict[str, Any], auth_key_prefix: str = "", account: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        openai_messages, openai_tools = _convert_messages(body)

        override_alias = _intercept_sub_agent(body)
        model_alias = override_alias or router.resolve_model_alias(body.get("model", ""))
        if not model_alias:
            model_alias = config.DEFAULT_MODEL_ALIAS

        await _pre_compact_and_truncate(body, openai_messages, openai_tools, model_alias)

        req_id = uuid.uuid4().hex[:8]
        first_user = ""
        for m in openai_messages:
            if m.get("role") == "user":
                c = m.get("content", "")
                first_user = str(c)[:200].replace('\n', ' ') if isinstance(c, str) else str(c.get("text", ""))[:200].replace('\n', ' ')
                break
        logger.info("[%s] [Request] create_message (Non-Stream) | model=%s, alias=%s, override=%s, messages=%d, tools=%d | first_user=%.200s",
                    req_id, body.get("model"), model_alias,
                    override_alias or "-", len(openai_messages), len(openai_tools),
                    first_user)

        pool = router.resolve_pool(model_alias)
        if pool:
            try:
                result = await self._call_lm_with_retry(body, openai_messages, openai_tools, pool, model_alias, auth_key_prefix=auth_key_prefix, account=account)
                if isinstance(result, dict):
                    result["model"] = body.get("model") or model_alias
                    return result
                return result
            except HTTPException as exc:
                if exc.status_code == 503:
                    summary_text = get_system_status_summary(model_alias)
                    return {
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
                raise

        for attempt in range(config.MAX_RETRIES):
            model_alias_val = None
            api_key_val = None
            litellm_model_val = None
            try:
                est_input = len(str(openai_messages)) // 4
                max_output = min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS)
                estimated_tokens = est_input + max_output
                model_alias_val, model_id_val, api_key_val, litellm_model_val, reservation = await _resolve_model(body, model_alias, account=account, estimated_tokens=estimated_tokens, retry_attempt=attempt)
                logger.info(
                    "[%s] [Reserve Non-Pool] Reserved key ...%s for model_alias=%s (resolved model_id=%s) | Attempt: %d/%d | Estimated tokens: %d",
                    req_id, api_key_val[-8:] if api_key_val else "N/A", model_alias, model_id_val, attempt + 1, config.MAX_RETRIES, estimated_tokens
                )
                try:
                    try:
                        input_tokens = await token_counter(model=litellm_model_val, messages=openai_messages)
                    except Exception:
                        input_tokens = max(1, len(str(openai_messages)) // 4)

                    is_lite = "lite" in str(litellm_model_val).lower()
                    limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
                    if attempt >= 10:
                        _div = max(3, attempt - 7)
                        limit = max(20000, limit // _div)
                    openai_messages[:] = _emergency_truncate_to_limit(openai_messages, limit)
                    try:
                        input_tokens = await token_counter(model=litellm_model_val, messages=openai_messages)
                    except Exception:
                        input_tokens = max(1, len(str(openai_messages)) // 4)

                    estimated_output = min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS)
                    has_quota = await router.acquire_quota(input_tokens + estimated_output, model_alias_val)
                    if not has_quota:
                        apply_error_penalty(api_key_val, "rate_limit_rpm_tpm", model_id_val)
                        router.freeze_key(api_key_val, 15, model_id_val, "rate_limit")
                        if attempt == config.MAX_RETRIES - 1:
                            summary_text = get_system_status_summary(model_alias)
                            return {
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
                        await asyncio.sleep(_retry_delay(attempt))
                        continue

                    reinforced_messages = _reinforce_messages_for_retry(openai_messages, attempt)
                    kwargs: Dict[str, Any] = {
                        "model": litellm_model_val,
                        "messages": reinforced_messages,
                        "api_key": api_key_val,
                        "max_tokens": max(1, min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS)),
                        "temperature": float(body.get("temperature", 0.7)),
                        "stream": False,
                        "request_timeout": config.REQUEST_TIMEOUT_SECONDS,
                    }
                    if reservation.get("provider") == "custom":
                        kwargs["api_base"] = reservation["api_base"]

                    from .proxy import _clean_kwargs_for_model
                    kwargs = _clean_kwargs_for_model(kwargs, litellm_model_val)

                    result = await _execute_nonstream(self, kwargs, api_key_val, model_id_val, model_alias_val, input_tokens, None, body, auth_key_prefix, account=account)
                    router.record_success(api_key_val, model_id_val)
                    return result
                finally:
                    if api_key_val:
                        router.release_key(api_key_val)

            except HTTPException as e:
                if e.status_code == 503 and attempt < config.MAX_RETRIES - 1:
                    logger.info("[Retry] 503 from resolve_model (attempt %d/%d), retrying", attempt + 1, config.MAX_RETRIES)
                    if api_key_val:
                        router.freeze_key(api_key_val, 15, model_id_val, "rate_limit")
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                summary_text = get_system_status_summary(model_alias)
                return {
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
                    if attempt == config.MAX_RETRIES - 1:
                        summary_text = get_system_status_summary(model_alias)
                        return {
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
                    await asyncio.sleep(_retry_delay(attempt))
                    continue

                if "499" in error_text or "cancelled" in error_text:
                    raise HTTPException(status_code=503, detail={
                        "type": "error", "error": {"type": "api_error", "message": "Request cancelled by client"}
                    })

                reason = classify(e)
                if reason == "rate_limit":
                    router.record_429()

                if reason == "unknown":
                    logger.error("[Retry Failure Detail] Unexpected error on key ...%s: %s", (api_key_val or "N/A")[-4:], e, exc_info=True)

                if api_key_val:
                    router.freeze_key(api_key_val, 0, model_id_val, reason)
                    if reason not in ("bad_request_spam_prevent", "invalid_key"):
                        apply_error_penalty(api_key_val, reason, model_id_val)
                router.record_failure(reason)
                logger.warning("[Retry] Key ...%s failed (attempt %d/%d). Reason: %s",
                              (api_key_val or "N/A")[-4:], attempt + 1, config.MAX_RETRIES, reason)

                if attempt == config.MAX_RETRIES - 1:
                    summary_text = get_system_status_summary(model_alias)
                    return {
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

                await asyncio.sleep(_retry_delay(attempt))

        summary_text = get_system_status_summary(model_alias)
        return {
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

import asyncio
import uuid
from typing import Any, Dict, List, Optional, AsyncIterator

import litellm
from fastapi import HTTPException

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.limits import apply_error_penalty
from src.api.claude_proxy.utils import (
    _resolve_model,
    _retry_delay,
    _convert_messages,
    _intercept_sub_agent,
    should_compact,
    _compact_conversation,
    _emergency_truncate_to_limit,
    _dict_to_sse_events,
    _sse,
)

from .helpers import get_system_status_summary, _classify_error_reason, _reinforce_messages_for_retry
from .compaction import _pre_compact_and_truncate
from .stream_executor import _execute_stream, _stream_with_pool

class ClaudeProxyStreamMixin:

    async def stream_message(self, body: Dict[str, Any], auth_key_prefix: str = "", account: Optional[Dict[str, Any]] = None) -> AsyncIterator[bytes]:
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
        logger.info("[%s] [Request] stream_message (Streaming) | model=%s, alias=%s, override=%s, messages=%d, tools=%d | first_user=%.200s",
                    req_id, body.get("model"), model_alias,
                    override_alias or "-", len(openai_messages), len(openai_tools),
                    first_user)

        yield _sse("ping", {"type": "ping", "retry": 0, "reason": "initial"})

        if override_alias:
            logger.info("[Sub-Agent] Using non-stream path for sub-agent (override=%s)", override_alias)
            task = asyncio.create_task(self.create_message(body, auth_key_prefix))
            try:
                while not task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
                    except asyncio.TimeoutError:
                        yield _sse("ping", {"type": "ping", "retry": 0, "reason": "keepalive"})
                result = await task
                for chunk in _dict_to_sse_events(result):
                    yield chunk
            except HTTPException as e:
                detail = e.detail if isinstance(e.detail, dict) else {"error": {"message": str(e.detail)}}
                yield _sse("error", {"type": "error", "error": {"type": detail.get("error", {}).get("type", "api_error"), "message": detail.get("error", {}).get("message", str(e.detail))}})
            except Exception as e:
                logger.error("[Sub-Agent] Non-stream error: %s", e)
                yield _sse("error", {"type": "error", "error": {"type": "api_error", "message": f"Sub-agent error: {e}"}})
            finally:
                if not task.done():
                    task.cancel()
            return

        pool = router.resolve_pool(model_alias)
        if pool:
            try:
                async for chunk in _stream_with_pool(self, body, openai_messages, openai_tools, pool, model_alias, auth_key_prefix, account=account):
                    yield chunk
            except asyncio.CancelledError:
                logger.warning("[%s] [Stream] Client cancelled/disconnected stream session (pool path) for model_alias=%s", req_id, model_alias)
                raise
            except Exception as stream_err:
                logger.error("[%s] [Stream Pool Exception] %s", req_id, stream_err, exc_info=True)
                err_str = str(stream_err).lower()
                err_reason = _classify_error_reason(err_str) if err_str else "pool_exhausted"
                summary_text = get_system_status_summary(model_alias, err_reason)
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

        for attempt in range(config.MAX_RETRIES):
            model_alias_val = None
            api_key_val = None
            try:
                est_input = len(str(openai_messages)) // 4
                max_output = min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS)
                estimated_tokens = est_input + max_output
                model_alias_val, model_id_val, api_key_val, litellm_model_val, reservation = await _resolve_model(body, model_alias, account=account, estimated_tokens=estimated_tokens, retry_attempt=attempt)
                logger.info(
                    "[%s] [Reserve Non-Pool Stream] Reserved key ...%s for model_alias=%s (resolved model_id=%s) | Attempt: %d/%d | Estimated tokens: %d",
                    req_id, api_key_val[-8:] if api_key_val else "N/A", model_alias, model_id_val, attempt + 1, config.MAX_RETRIES, estimated_tokens
                )
                try:
                    try:
                        input_tokens = await asyncio.to_thread(litellm.token_counter, model=litellm_model_val, messages=openai_messages)
                    except Exception:
                        input_tokens = max(1, len(str(openai_messages)) // 4)

                    if should_compact(openai_messages, input_tokens, retry_attempt=attempt):
                        openai_messages[:] = await _compact_conversation(body, openai_messages, openai_tools, input_tokens, retry_attempt=attempt)
                        try:
                            input_tokens = await asyncio.to_thread(litellm.token_counter, model=litellm_model_val, messages=openai_messages)
                        except Exception:
                            input_tokens = max(1, len(str(openai_messages)) // 4)

                    is_lite = "lite" in str(litellm_model_val).lower()
                    limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
                    if attempt >= 10:
                        _div = max(3, attempt - 7)
                        limit = max(20000, limit // _div)
                    openai_messages[:] = _emergency_truncate_to_limit(openai_messages, limit)
                    try:
                        input_tokens = await asyncio.to_thread(litellm.token_counter, model=litellm_model_val, messages=openai_messages)
                    except Exception:
                        input_tokens = max(1, len(str(openai_messages)) // 4)

                    max_output = min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS)
                    has_quota = await router.acquire_quota(input_tokens + max_output, model_alias_val)
                    if not has_quota:
                        apply_error_penalty(api_key_val, "rate_limit_rpm_tpm", model_id_val)
                        router.freeze_key(api_key_val, 15, model_id_val, "rate_limit")
                        if attempt == config.MAX_RETRIES - 1:
                            summary_text = get_system_status_summary(model_alias, "rate_limit")
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
                        yield _sse("ping", {"type": "ping", "retry": attempt + 1, "reason": "rate_limit"})
                        await asyncio.sleep(_retry_delay(attempt))
                        continue

                    reinforced_messages = _reinforce_messages_for_retry(openai_messages, attempt)
                    kwargs: Dict[str, Any] = {
                        "model": litellm_model_val,
                        "messages": reinforced_messages,
                        "api_key": api_key_val,
                        "max_tokens": max_output,
                        "temperature": float(body.get("temperature", 0.7)),
                        "request_timeout": config.REQUEST_TIMEOUT_SECONDS,
                    }
                    if openai_tools:
                        kwargs["tools"] = openai_tools
                    if reservation.get("provider") == "custom":
                        kwargs["api_base"] = reservation["api_base"]

                    gen = await _execute_stream(self, kwargs, api_key_val, model_id_val, model_alias_val, input_tokens, None, body, auth_key_prefix, account=account)
                    api_key_val = None
                    try:
                        async for chunk in gen:
                            yield chunk
                    except asyncio.CancelledError:
                        logger.warning("[%s] [Stream] Client cancelled/disconnected stream session (non-pool path) for model_alias=%s", req_id, model_alias)
                        raise
                    except Exception as stream_err:
                        logger.error("[%s] [Stream] Error yielding stream: %s", req_id, stream_err, exc_info=True)
                        yield _sse("error", {"type": "error", "error": {"type": "api_error", "message": "Stream error"}})
                    return
                finally:
                    if api_key_val:
                        router.release_key(api_key_val)

            except HTTPException:
                if api_key_val:
                    router.freeze_key(api_key_val, 15, model_id_val, "rate_limit")
                    apply_error_penalty(api_key_val, "rate_limit", model_id_val)
                router.record_failure("rate_limit")
                if attempt == config.MAX_RETRIES - 1:
                    summary_text = get_system_status_summary(model_alias, "rate_limit")
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
                yield _sse("ping", {"type": "ping", "retry": attempt + 1, "reason": "rate_limit"})
                await asyncio.sleep(_retry_delay(attempt))
            except asyncio.CancelledError:
                logger.warning("[%s] [Stream] Stream request cancelled/disconnected during attempt %d/%d for model=%s", req_id, attempt + 1, config.MAX_RETRIES, model_alias)
                raise
            except Exception as e:
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
                    if attempt == config.MAX_RETRIES - 1:
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
                    yield _sse("ping", {"type": "ping", "retry": attempt + 1, "reason": "billing_error"})
                    await asyncio.sleep(_retry_delay(attempt))
                    continue

                reason = "unknown_error"

                if "403" in error_text and "permission_denied" in error_text:
                    reason = "permission_denied"
                elif "quota" in error_text and ("day" in error_text or "daily" in error_text):
                    reason = "rate_limit_rpd"
                elif "429" in error_text or "rate_limit" in error_text:
                    reason = "rate_limit"
                    router.record_429()
                elif ("401" in error_text and ("unauthorized" in error_text or "invalid" in error_text or "api_key" in error_text or "api key" in error_text)) or "api key not valid" in error_text or "api_key_invalid" in error_text:
                    reason = "invalid_key"
                elif "503" in error_text or "unavailable" in error_text or "overloaded" in error_text:
                    reason = "unavailable"
                elif "500" in error_text or "internal" in error_text:
                    reason = "server_error"
                elif "504" in error_text or "deadline" in error_text or "timeout" in error_text:
                    reason = "timeout"
                elif "499" in error_text or "cancelled" in error_text:
                    yield _sse("error", {"type": "error", "error": {"type": "api_error", "message": "Request cancelled by client"}})
                    return

                if reason == "unknown_error":
                    logger.error("[Stream Retry Failure Detail] Unexpected error on key ...%s: %s", (api_key_val or "N/A")[-4:], e, exc_info=True)

                if api_key_val:
                    router.freeze_key(api_key_val, 0, model_id_val, reason)
                    if reason not in ("bad_request_spam_prevent", "invalid_key"):
                        apply_error_penalty(api_key_val, reason, model_id_val)
                router.record_failure(reason)
                logger.warning("[Stream Retry] Key ...%s failed (attempt %d/%d). Reason: %s",
                              (api_key_val or "N/A")[-4:], attempt + 1, config.MAX_RETRIES, reason)

                if attempt == config.MAX_RETRIES - 1:
                    summary_text = get_system_status_summary(model_alias, reason)
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

                yield _sse("ping", {"type": "ping", "retry": attempt + 1, "reason": reason})
                await asyncio.sleep(_retry_delay(attempt))

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

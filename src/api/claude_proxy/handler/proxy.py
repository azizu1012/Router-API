import asyncio
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from src.core.providers.litellm_wrapper import token_counter

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.providers import _custom_endpoint_manager as endpoint_manager
from src.core.limits import apply_error_penalty
from src.logical_HQ_translator import (
    _resolve_model,
    _retry_delay,
    _emergency_truncate_to_limit,
)

from .helpers import _reinforce_messages_for_retry
from src.core.providers.gemini.error import classify
from .nonstream_executor import _execute_nonstream
from .stream_executor import _execute_stream
from .proxy_nonstream import ClaudeProxyNonstreamMixin
from .proxy_stream import ClaudeProxyStreamMixin


def _is_gemini_v3(litellm_model: str) -> bool:
    """Check if the model is Gemini 3+ (temperature/top_p deprecated)."""
    m = litellm_model.lower()
    return "gemini-3" in m and "gemini-2" not in m


def _clean_kwargs_for_model(kwargs: Dict[str, Any], litellm_model: str) -> Dict[str, Any]:
    """Remove deprecated params for Gemini 3+; litellm defaults to 1.0."""
    if _is_gemini_v3(litellm_model):
        kwargs.pop("temperature", None)
        kwargs.pop("top_p", None)
        kwargs.pop("top_k", None)
    return kwargs


def _model_supports_thinking(model_id: str) -> bool:
    m = model_id.lower()
    if "lite" in m:
        return False
    return any(x in m for x in ["gemini-2", "gemini-2.5", "gemini-3", "gemini-3.5"])


def _build_litellm_thinking(body: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    """Build litellm kwargs for thinking from body params.

    litellm natively translates ``reasoning_effort`` and ``thinking``
    to Gemini's ``thinkingConfig`` — no ``extra_body`` needed.
    Returns ``{}`` when no thinking params given — each model uses API default.
    """
    # Sub-agents never get thinking (even if body has it) — flash-lite can't handle it
    try:
        from src.logical_HQ_translator import is_sub_agent_body
        if is_sub_agent_body(body):
            logger.info("[Thinking Config] model_id=%s — sub-agent, thinking disabled", model_id)
            return {}
    except Exception:
        pass

    m = model_id.lower()
    is_v3 = _is_gemini_v3(m)

    # 1. Anthropic-style thinking — pass directly, litellm handles it
    thinking = body.get("thinking")
    
    # Auto-enable thinking for main agent if not specified and model supports it
    if thinking is None:
        try:
            supports = _model_supports_thinking(model_id)
            logger.info("[Thinking Sync] Checking auto-enable: is_sub_agent=no, supports_thinking=%s", supports)
            if supports:
                thinking = {
                    "type": "enabled",
                    "budget_tokens": 32768 if "pro" in m else 24576
                }
        except Exception as ex:
            logger.error("[Thinking Sync Error] %s", ex, exc_info=True)

    logger.info("[Thinking Config] model_id=%s, auto_thinking=%s", model_id, thinking)

    if isinstance(thinking, dict):
        ttype = thinking.get("type")
        if ttype in ("enabled", "adaptive"):
            if is_v3:
                return {"reasoning_effort": "medium"}
            # "adaptive" → modest budget, let Gemini decide but cap thinking time
            if ttype == "adaptive":
                budget = 4096 if "flash" in m else 8192
                return {"thinking": {"type": "enabled", "budget_tokens": budget}}
            budget = thinking.get("budget_tokens")
            if budget == -1 or budget is None:
                budget = 32768 if "pro" in m else 24576
            return {"thinking": {"type": "enabled", "budget_tokens": budget}}
        return {}  # disabled

    # 2. OpenAI-style explicit params
    thinking_level = body.get("thinking_level")
    thinking_budget = body.get("thinking_budget")
    include_thoughts = body.get("include_thoughts", False)

    # No user params → no defaults (let each model use API default)
    if thinking_level is None and thinking_budget is None and not include_thoughts:
        return {}

    # 3. If V3 model, translate all thinking requests to reasoning_effort
    if is_v3:
        if thinking_level is not None:
            return {"reasoning_effort": thinking_level}
        return {"reasoning_effort": "medium"}

    # 4. V2.5 thinking_level overrides
    if thinking_level is not None:
        budget_map = {"low": 1024, "medium": 2048, "high": 4096}
        return {"thinking": {"type": "enabled", "budget_tokens": budget_map.get(thinking_level, 2048)}}

    # 5. V2.5 thinking_budget / include_thoughts
    d: Dict[str, Any] = {}
    budget = thinking_budget
    if budget == -1 or (budget is None and include_thoughts):
        budget = 32768 if "pro" in m else 24576
    if budget is not None:
        d["budget_tokens"] = budget
    if include_thoughts:
        d["include_thoughts"] = True
    if d:
        d["type"] = "enabled"
        return {"thinking": d}
    return {}

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
            if reservation.get("provider") == "custom":
                tools = [
                    t for t in openai_tools
                    if t.get("function", {}).get("name") not in ("WebSearch", "WebFetch")
                ]
                if tools:
                    kwargs["tools"] = tools
            else:
                kwargs["tools"] = openai_tools

        if reservation.get("provider") == "custom":
            kwargs["api_base"] = reservation["api_base"]
            # Forward raw thinking params to custom endpoints
            extra: Dict[str, Any] = {}
            for k in ("thinking", "thinking_level", "thinking_budget", "include_thoughts", "enableThinking"):
                if k in body:
                    extra[k] = body[k]
            if extra:
                kwargs["extra_body"] = extra
        else:
            kwargs.update(_build_litellm_thinking(body, litellm_model_val))

        return _clean_kwargs_for_model(kwargs, litellm_model_val)

    async def _call_lm_with_retry(
        self, body: Dict[str, Any], openai_messages: List[Dict[str, Any]], openai_tools: List[Dict[str, Any]],
        pool: Any, model_alias: str, is_stream: bool = False,
        auth_key_prefix: str = "",
        account: Optional[Dict[str, Any]] = None,
    ) -> Any:
        # Check sub-agent context first
        from src.core.router.core.router import is_sub_agent_context
        is_sub = is_sub_agent_context.get()

        pool.start()
        while not pool.exhausted:
            if is_sub:
                # Sub-agents must fail fast if pool attempts >= 3 or time spent >= 15 seconds
                if pool.total_attempts >= 3 or pool.elapsed >= 15.0:
                    logger.warning("[Sub-Agent Fast-Fail] Pool attempts: %d, elapsed: %.1fs. Failing pool routing early.", pool.total_attempts, pool.elapsed)
                    break

            actual_alias = pool.current_model
            model_alias_val = None
            api_key_val = None
            litellm_model_val = None
            model_id_val = ""
            reservation = {}
            member_used = actual_alias

            try:
                est_input = len(str(openai_messages)) // 4
                max_output = min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS)
                estimated_tokens = est_input + max_output
                model_alias_val, model_id_val, api_key_val, litellm_model_val, reservation = await _resolve_model(body, model_alias, account=account, estimated_tokens=estimated_tokens, retry_attempt=pool.total_attempts, pool_mode=True)
                model_id_val = reservation.get("model_id", model_id_val)
                member_used = reservation.get("model_alias", actual_alias)
                logger.info(
                    "[Pool Reserve] Reserved key ...%s for pool=%s model_alias=%s (model_id=%s) | Attempt: %d (remaining=%ds) | Estimated tokens: %d",
                    api_key_val[-8:] if api_key_val else "N/A", model_alias, reservation.get("model_alias", model_alias), model_id_val, pool.total_attempts + 1, int(pool.remaining_time()), estimated_tokens
                )
                try:
                    from src.logical_HQ_translator import save_resolved_model_for_cwd
                    save_resolved_model_for_cwd(body.get("system", ""), model_alias_val, model_id_val)
                except Exception as ex_sync:
                    logger.error("[Statusline Sync Error] Failed to call sync helper: %s", ex_sync)
            except HTTPException as e:
                if e.status_code in (429, 503):
                    logger.info("[Pool Retry] _resolve_model returned %d for %s (cooldown=%s), retrying (attempt %d, remaining=%ds)",
                                e.status_code, member_used,
                                "global cooldown" if e.status_code == 503 else "all keys frozen",
                                pool.total_attempts + 1, int(pool.remaining_time()))
                    if pool.record_failure(member_used, "rate_limit"):
                        if not pool.swap():
                            if pool.exhausted:
                                break
                            wait = min(15.0, pool.remaining_time())
                            await asyncio.sleep(wait)
                            pool.reset_cycle()
                            continue
                    await asyncio.sleep(_retry_delay(pool.total_attempts))
                    continue
                raise

            try:
                try:
                    try:
                        input_tokens = await token_counter(model=litellm_model_val, messages=openai_messages)
                    except Exception:
                        input_tokens = max(1, len(str(openai_messages)) // 4)

                    attempt_val = pool.total_attempts
                    is_lite = "lite" in str(litellm_model_val).lower()
                    limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
                    if attempt_val >= 10:
                        _div = max(3, attempt_val - 7)
                        limit = max(20000, limit // _div)
                    openai_messages[:] = _emergency_truncate_to_limit(openai_messages, limit)
                    try:
                        input_tokens = await token_counter(model=litellm_model_val, messages=openai_messages)
                    except Exception:
                        input_tokens = max(1, len(str(openai_messages)) // 4)

                    max_output = min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS)
                    has_quota = await router.acquire_quota(input_tokens + max_output, model_alias)
                    if not has_quota:
                        apply_error_penalty(api_key_val, "rate_limit_rpm_tpm", model_id_val)
                        router.freeze_key(api_key_val, 15, model_id_val, "rate_limit")
                        if pool.record_failure(member_used, "rate_limit"):
                            if not pool.swap():
                                if pool.exhausted:
                                    break
                                wait = min(15.0, pool.remaining_time())
                                await asyncio.sleep(wait)
                                pool.reset_cycle()
                                continue
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
                        resp = await _execute_nonstream(self, kwargs, api_key_val, model_id_val, actual_alias, input_tokens, pool, body, auth_key_prefix, account=account)
                        if reservation.get("provider") == "custom":
                            endpoint_manager.mark_endpoint_success(reservation.get("name", actual_alias))
                        return resp
                finally:
                    if api_key_val:
                        router.release_key(api_key_val)

            except HTTPException:
                raise
            except Exception as e:
                error_text = str(e).lower()
                is_custom = reservation.get("provider") == "custom" if "reservation" in locals() else False
                if is_custom:
                    logger.warning("[CustomEndpoint] Failed on custom endpoint %s: %s, falling back to Gemini pool", model_id_val, e)
                    ep_name = reservation.get("name", member_used)
                    endpoint_manager.mark_endpoint_failure(ep_name)
                    if pool.record_failure(member_used, "custom_endpoint_error"):
                        if not pool.swap():
                            if pool.exhausted:
                                break
                            wait = min(15.0, pool.remaining_time())
                            await asyncio.sleep(wait)
                            pool.reset_cycle()
                            continue
                    await asyncio.sleep(_retry_delay(pool.total_attempts))
                    continue

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
                    if pool.record_failure(member_used, "billing_error"):
                        if not pool.swap():
                            if pool.exhausted:
                                break
                            wait = min(15.0, pool.remaining_time())
                            await asyncio.sleep(wait)
                            pool.reset_cycle()
                            continue
                    await asyncio.sleep(_retry_delay(pool.total_attempts))
                    continue

                if "499" in error_text or "cancelled" in error_text:
                    raise HTTPException(status_code=503, detail={
                        "type": "error", "error": {"type": "api_error", "message": "Request cancelled by client"}
                    })

                duration = config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS
                reason = classify(e)

                if reason == "unknown":
                    logger.error("[Pool Failure Detail] Unexpected error on key ...%s: %s", (api_key_val or "N/A")[-4:], e, exc_info=True)

                # 429 (rate_limit) và 503 (unavailable): không freeze key, không count failure
                # — chỉ backoff rồi retry
                if reason in ("rate_limit", "unavailable"):
                    logger.warning("[Pool Temp Unavailable] model=%s key=...%s reason=%s — waiting 5s then retry (attempt %d)",
                                  member_used, (api_key_val or "N/A")[-4:] if api_key_val else "N/A", reason,
                                  pool.total_attempts + 1)
                    if reason == "rate_limit":
                        router.record_429()
                    await asyncio.sleep(5.0)
                    continue

                if api_key_val:
                    router.freeze_key(api_key_val, duration, model_id_val, reason)
                    if reason not in ("bad_request_spam_prevent", "invalid_key"):
                        apply_error_penalty(api_key_val, reason, model_id_val)
                router.record_failure(reason)
                failure_state = pool.failure_state_after_next(member_used, reason)
                logger.warning("[Pool Failure] Key ...%s failed on model %s | Reason: %s | Action: %s (model failures: %d/%d, pool total attempts: %d, remaining=%ds)",
                              (api_key_val or "N/A")[-4:], member_used, reason, failure_state["action"],
                              failure_state["failures_after"], failure_state["threshold"],
                              pool.total_attempts + 1, int(pool.remaining_time()))
                if pool.record_failure(member_used, reason):
                    if not pool.swap():
                        if pool.exhausted:
                            break
                        wait = min(15.0, pool.remaining_time())
                        await asyncio.sleep(wait)
                        pool.reset_cycle()
                        continue
                await asyncio.sleep(_retry_delay(pool.total_attempts))

        logger.warning("[Pool Exhausted] Request timed out after %.1fs.", pool.elapsed)
        raise HTTPException(status_code=503, detail={
            "type": "error", "error": {"type": "api_error", "message": "Pool exhausted."}
        })

claude_proxy = ClaudeProxy()

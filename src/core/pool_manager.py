"""Centralized Pool Manager — manages key pool, rotation, quota check, and retries.

This handles routing to either Gemini native (via GenAI SDK or HTTP) or Custom Endpoints.
Both stream and non-stream methods are supported, returning/yielding OpenAI-compatible structures.
Proxies (OpenCodeProxy, ClaudeProxy) delegate pool/retry logic to this class.
"""

import asyncio
import time
import random
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_system as logger
from src.core.router import router
from src.core.limits import get_rate_limiter, apply_error_penalty, count_transient_error
from src.core.providers import _custom_endpoint_manager as endpoint_manager
from src.core.providers.custom_endpoint_client import check_custom_pool_rate
from src.core.providers.gemini_facade import acompletion, token_counter
from src.logical_HQ_translator import _resolve_model, _retry_delay
from src.core.providers.gemini.error import classify as classify_gemini_error

# Key error classification reasons
TRANSIENT_REASONS = {"rate_limit", "unavailable", "server_error", "timeout", "unknown_error"}


class PoolCallError(Exception):
    def __init__(self, original_error: Exception, api_key: Optional[str], model_id: Optional[str], reservation: Optional[dict]):
        self.original_error = original_error
        self.api_key = api_key
        self.model_id = model_id
        self.reservation = reservation
        super().__init__(str(original_error))


def _compute_thinking_for_model(
    thinking_params: Optional[Dict[str, Any]],
    model_id: str,
) -> Optional[Dict[str, Any]]:
    """Recompute thinking config for a specific model (handles V3 vs V2 correctly)."""
    if not thinking_params:
        return None
    m = model_id.lower()
    if "lite" in m:
        return None
    is_v3 = "gemini-3" in m and "gemini-2" not in m

    thinking_level = thinking_params.get("thinking_level")
    thinking_budget = thinking_params.get("thinking_budget")
    include_thoughts = thinking_params.get("include_thoughts")

    if thinking_level is not None:
        if is_v3:
            return {"thinking_level": str(thinking_level).lower(), "include_thoughts": include_thoughts if include_thoughts is not None else True}
        budget_map = {"low": 1024, "medium": 2048, "high": 4096}
        return {"thinking_budget": budget_map.get(str(thinking_level).lower(), 2048), "include_thoughts": include_thoughts if include_thoughts is not None else True}

    if thinking_budget is not None:
        if is_v3:
            return {"thinking_level": "medium", "include_thoughts": include_thoughts if include_thoughts is not None else True}
        return {"thinking_budget": int(thinking_budget), "include_thoughts": include_thoughts if include_thoughts is not None else True}

    if include_thoughts is not None and include_thoughts:
        if is_v3:
            return {"thinking_level": "low" if "flash" in m and "pro" not in m else "medium", "include_thoughts": True}
        return {"thinking_budget": 8192 if "flash" in m and "pro" not in m else 16384, "include_thoughts": True}

    if is_v3:
        return {"thinking_level": "low" if "flash" in m and "pro" not in m else "medium", "include_thoughts": True}
    return {"thinking_budget": 8192 if "flash" in m and "pro" not in m else 16384, "include_thoughts": True}


def _classify_error(e: Exception) -> str:
    """Helper to classify errors into unified reasons."""
    err_str = str(e)
    # Check Gemini error classification
    reason = classify_gemini_error(e)
    if reason != "unknown":
        return reason
    # Text fallback matching
    msg_lower = err_str.lower()
    if "rate limit" in msg_lower or "too many requests" in msg_lower or "429" in msg_lower or "quota_exhausted" in msg_lower:
        return "rate_limit"
    if "quota exceeded" in msg_lower:
        return "rate_limit_rpd"
    if "billing" in msg_lower or "precondition" in msg_lower:
        return "billing_error"
    if "api key" in msg_lower or "invalid key" in msg_lower or "401" in msg_lower:
        return "invalid_key"
    if "permission denied" in msg_lower or "403" in msg_lower:
        return "permission_denied"
    if "bad request" in msg_lower or "400" in msg_lower:
        return "bad_request"
    if "unavailable" in msg_lower or "overloaded" in msg_lower or "503" in msg_lower:
        return "unavailable"
    return "unknown"


class PoolManager:
    """Manages monolithic pool loops and routes to Gemini or Custom endpoints."""

    async def call_nonstream(
        self,
        model_alias: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking_config: Optional[Dict[str, Any]] = None,
        account: Optional[Dict[str, Any]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        thinking_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Unified pool call for non-streaming completions."""
        pool = router.resolve_pool(model_alias)
        if pool:
            pool.start()
            pool_try = -1
            while not pool.exhausted:
                pool_try += 1
                api_key_val = None
                model_id_val = None
                reservation = {}
                try:
                    resp, api_key_val, model_id_val, input_tokens, reservation = await self._resolve_and_call(
                        model_alias, messages, tools, temperature, max_tokens, thinking_config,
                        account, extra_body, pool_mode=True, pool=pool, is_stream=False,
                        attempt=pool_try, thinking_params=thinking_params,
                    )
                    # Successful call
                    member_used = reservation.get("model_alias", pool.current_model)
                    is_custom = reservation.get("provider") == "custom"
                    if is_custom:
                        endpoint_manager.mark_endpoint_success(reservation.get("name", member_used))
                    else:
                        router.update_model_health(member_used, success=True)
                    pool.record_success()
                    return {
                        "response": resp,
                        "api_key": api_key_val,
                        "model_id": model_id_val,
                        "input_tokens": input_tokens,
                        "reservation": reservation
                    }
                except Exception as e:
                    if isinstance(e, PoolCallError):
                        api_key_val = e.api_key
                        model_id_val = e.model_id
                        reservation = e.reservation or {}
                        e = e.original_error
                    member_used = reservation.get("model_alias", pool.current_model) if reservation else pool.current_model
                    is_custom = reservation.get("provider") == "custom"
                    if is_custom:
                        logger.warning("[PoolManager] Custom endpoint failed: %s, swapping...", e)
                        endpoint_manager.mark_endpoint_failure(reservation.get("name", member_used))
                        pool.record_failure(member_used, "custom_endpoint_error")
                        pool.swap()
                        continue

                    # Classify error
                    reason = _classify_error(e)
                    if reason in TRANSIENT_REASONS:
                        # Update transient metrics
                        count_transient_error(reason)
                        if reason == "rate_limit":
                            router.record_429()

                        # Apply temporary score penalty and freeze the key
                        if api_key_val and model_id_val:
                            router.freeze_key(api_key_val, config.KEY_429_COOLDOWN_SECONDS, model_id_val, reason)
                            apply_error_penalty(api_key_val, reason, model_id_val)

                        pool._consecutive_transient += 1
                        if pool._consecutive_transient >= config.POOL_SWAP_FAILURES:
                            logger.warning("[PoolManager] Transient error %s on member %s, too many retries - swapping...", reason, member_used)
                            pool._consecutive_transient = 0
                            pool.swap()
                        else:
                            delay = _retry_delay(pool._consecutive_transient)
                            logger.warning("[PoolManager] Transient error %s on member %s, retrying in %.1fs (attempt %d)", reason, member_used, delay, pool._consecutive_transient)
                            await asyncio.sleep(delay)
                        continue
                    else:
                        pool._consecutive_transient = 0
                        # Permanent errors: freeze key, record failure & swap
                        logger.error("[PoolManager] Hard error %s on member %s: %s", reason, member_used, e)
                        if api_key_val and model_id_val:
                            cooldown = config.KEY_INVALID_COOLDOWN_SECONDS if reason == "invalid_key" else config.KEY_429_COOLDOWN_SECONDS
                            router.freeze_key(api_key_val, cooldown, model_id_val, reason)
                            apply_error_penalty(api_key_val, reason, model_id_val)
                        pool.record_failure(member_used, reason)
                        pool.swap()
            raise RuntimeError("Pool exhausted or all attempts failed")
        else:
            # Standalone mode (no pool config)
            for attempt in range(config.MAX_RETRIES):
                api_key_val = None
                model_id_val = None
                try:
                    resp, api_key_val, model_id_val, input_tokens, reservation = await self._resolve_and_call(
                        model_alias, messages, tools, temperature, max_tokens, thinking_config,
                        account, extra_body, pool_mode=False, is_stream=False, attempt=attempt,
                        thinking_params=thinking_params,
                    )
                    return {
                        "response": resp,
                        "api_key": api_key_val,
                        "model_id": model_id_val,
                        "input_tokens": input_tokens,
                        "reservation": reservation
                    }
                except Exception as e:
                    if isinstance(e, PoolCallError):
                        api_key_val = e.api_key
                        model_id_val = e.model_id
                        reservation = e.reservation or {}
                        e = e.original_error
                    reason = _classify_error(e)
                    logger.warning("[PoolManager] Standalone error (attempt %d): %s", attempt, e)
                    if reason in TRANSIENT_REASONS:
                        count_transient_error(reason)
                        if reason == "rate_limit":
                            router.record_429()
                    if api_key_val and model_id_val:
                        cooldown = config.KEY_INVALID_COOLDOWN_SECONDS if reason in ("invalid_key", "permission_denied") else config.KEY_429_COOLDOWN_SECONDS
                        router.freeze_key(api_key_val, cooldown, model_id_val, reason)
                        apply_error_penalty(api_key_val, reason, model_id_val)
                    await asyncio.sleep(_retry_delay(attempt))

            raise RuntimeError("Standalone calls failed after retries")

    async def call_stream(
        self,
        model_alias: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking_config: Optional[Dict[str, Any]] = None,
        account: Optional[Dict[str, Any]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        thinking_params: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Unified pool call for streaming completions. Yields dict chunks."""
        pool = router.resolve_pool(model_alias)
        if pool:
            pool.start()
            committed = False
            pool_try = -1
            while not pool.exhausted:
                pool_try += 1
                api_key_val = None
                model_id_val = None
                reservation = {}
                try:
                    # Resolve key and pool member
                    max_output = min(int(max_tokens or config.MAX_OUTPUT_TOKENS), config.MAX_OUTPUT_TOKENS)
                    estimated_tokens = len(str(messages)) // 4 + max_output
                    model_alias_val, model_id_val, api_key_val, model_full_val, reservation = await _resolve_model(
                        {"model": model_alias}, model_alias, account=account, estimated_tokens=estimated_tokens,
                        retry_attempt=pool_try, pool_mode=True
                    )

                    is_custom = reservation.get("provider") == "custom"
                    member_used = reservation.get("model_alias", pool.current_model)

                    # Per-member thinking config
                    member_tc = _compute_thinking_for_model(thinking_params, model_full_val) if not is_custom else None
                    if member_tc is None:
                        member_tc = thinking_config

                    # Quota checks
                    has_quota = await router.acquire_quota(estimated_tokens, model_alias)
                    if not has_quota:
                        apply_error_penalty(api_key_val, "rate_limit_rpm_tpm", model_id_val)
                        router.freeze_key(api_key_val, config.KEY_429_COOLDOWN_SECONDS, model_id_val, "rate_limit")
                        raise RuntimeError("quota_exhausted")

                    # Invoke stream
                    try:
                        kwargs = {
                            "model": model_full_val,
                            "messages": messages,
                            "api_key": api_key_val,
                            "max_tokens": max_output,
                            "temperature": temperature,
                            "stream": True,
                            "tools": tools,
                            "thinking_config": member_tc if not is_custom else None,
                        }
                        if is_custom:
                            kwargs["api_base"] = reservation["api_base"]
                            if extra_body:
                                kwargs["extra_body"] = extra_body

                        gen = await acompletion(**kwargs)
                        async for chunk in gen:
                            committed = True
                            yield {
                                "chunk": chunk,
                                "api_key": api_key_val,
                                "model_id": model_id_val,
                                "input_tokens": estimated_tokens,
                                "reservation": reservation
                            }
                        
                        # Streaming completed successfully
                        if is_custom:
                            endpoint_manager.mark_endpoint_success(reservation.get("name", member_used))
                        else:
                            router.update_model_health(member_used, success=True)
                        pool.record_success()
                        return
                    finally:
                        if api_key_val:
                            router.release_key(api_key_val)

                except Exception as e:
                    if committed:
                        # Once stream starts and yields, we cannot retry or swap pool
                        logger.error("[PoolManager] Stream interrupted after committing: %s", e)
                        raise

                    member_used = reservation.get("model_alias", pool.current_model) if reservation else pool.current_model
                    is_custom = reservation.get("provider") == "custom"
                    if is_custom:
                        logger.warning("[PoolManager] Custom endpoint stream failed: %s, swapping...", e)
                        endpoint_manager.mark_endpoint_failure(reservation.get("name", member_used))
                        pool.record_failure(member_used, "custom_endpoint_error")
                        pool.swap()
                        continue

                    reason = _classify_error(e)
                    if reason in TRANSIENT_REASONS:
                        # Update transient metrics
                        count_transient_error(reason)
                        if reason == "rate_limit":
                            router.record_429()

                        # Apply temporary score penalty and freeze the key
                        if api_key_val and model_id_val:
                            router.freeze_key(api_key_val, config.KEY_429_COOLDOWN_SECONDS, model_id_val, reason)
                            apply_error_penalty(api_key_val, reason, model_id_val)

                        if not reservation:
                            logger.warning("[PoolManager] Transient stream error %s on member %s, no key available - swapping...", reason, member_used)
                            pool._consecutive_transient = 0
                            pool.swap()
                        else:
                            pool._consecutive_transient += 1
                            if pool._consecutive_transient >= config.POOL_SWAP_FAILURES:
                                logger.warning("[PoolManager] Transient stream error %s on member %s, too many retries - swapping...", reason, member_used)
                                pool._consecutive_transient = 0
                                pool.swap()
                            else:
                                delay = _retry_delay(pool._consecutive_transient)
                                logger.warning("[PoolManager] Transient stream error %s on member %s, retrying in %.1fs (attempt %d)", reason, member_used, delay, pool._consecutive_transient)
                                await asyncio.sleep(delay)
                        continue
                    else:
                        pool._consecutive_transient = 0
                        logger.error("[PoolManager] Hard stream error %s: %s", reason, e)
                        if api_key_val and model_id_val:
                            cooldown = config.KEY_INVALID_COOLDOWN_SECONDS if reason == "invalid_key" else config.KEY_429_COOLDOWN_SECONDS
                            router.freeze_key(api_key_val, cooldown, model_id_val, reason)
                            apply_error_penalty(api_key_val, reason, model_id_val)
                        pool.record_failure(member_used, reason)
                        pool.swap()
            raise RuntimeError("Pool stream exhausted or failed")
        else:
            # Standalone stream mode
            for attempt in range(config.MAX_RETRIES):
                api_key_val = None
                model_id_val = None
                try:
                    max_output = min(int(max_tokens or config.MAX_OUTPUT_TOKENS), config.MAX_OUTPUT_TOKENS)
                    estimated_tokens = len(str(messages)) // 4 + max_output
                    model_alias_val, model_id_val, api_key_val, model_full_val, reservation = await _resolve_model(
                        {"model": model_alias}, model_alias, account=account, estimated_tokens=estimated_tokens,
                        retry_attempt=attempt, pool_mode=False
                    )

                    # Per-member thinking config (standalone mode)
                    member_tc = _compute_thinking_for_model(thinking_params, model_full_val) if not thinking_config else thinking_config

                    has_quota = await router.acquire_quota(estimated_tokens, model_alias)
                    if not has_quota:
                        raise RuntimeError("quota_exhausted")

                    try:
                        kwargs = {
                            "model": model_full_val,
                            "messages": messages,
                            "api_key": api_key_val,
                            "max_tokens": max_output,
                            "temperature": temperature,
                            "stream": True,
                            "tools": tools,
                            "thinking_config": member_tc,
                        }

                        gen = await acompletion(**kwargs)
                        async for chunk in gen:
                            yield {
                                "chunk": chunk,
                                "api_key": api_key_val,
                                "model_id": model_id_val,
                                "input_tokens": estimated_tokens,
                                "reservation": reservation
                            }
                        return
                    finally:
                        if api_key_val:
                            router.release_key(api_key_val)
                except Exception as e:
                    reason = _classify_error(e)
                    logger.warning("[PoolManager] Standalone stream attempt %d failed: %s", attempt, e)
                    if reason in TRANSIENT_REASONS:
                        count_transient_error(reason)
                        if reason == "rate_limit":
                            router.record_429()
                    if api_key_val and model_id_val:
                        cooldown = config.KEY_INVALID_COOLDOWN_SECONDS if reason in ("invalid_key", "permission_denied") else config.KEY_429_COOLDOWN_SECONDS
                        router.freeze_key(api_key_val, cooldown, model_id_val, reason)
                        apply_error_penalty(api_key_val, reason, model_id_val)
                    await asyncio.sleep(_retry_delay(attempt))
            raise RuntimeError("Standalone stream failed after retries")

    # ── Internal Helpers ──────────────────────────────────────────

    async def _resolve_and_call(
        self,
        model_alias: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking_config: Optional[Dict[str, Any]] = None,
        account: Optional[Dict[str, Any]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        pool_mode: bool = False,
        pool: Optional[Any] = None,
        is_stream: bool = False,
        attempt: int = 0,
        thinking_params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, str, str, int, dict]:
        """Internal helper to resolve key, check quota, and perform a single call."""
        max_output = min(int(max_tokens or config.MAX_OUTPUT_TOKENS), config.MAX_OUTPUT_TOKENS)
        estimated_tokens = len(str(messages)) // 4 + max_output

        model_alias_val, model_id_val, api_key_val, model_full_val, reservation = await _resolve_model(
            {"model": model_alias}, model_alias, account=account, estimated_tokens=estimated_tokens,
            retry_attempt=attempt, pool_mode=pool_mode,
        )

        is_custom = reservation.get("provider") == "custom"
        member_used = reservation.get("model_alias", pool.current_model) if (pool_mode and pool) else model_alias_val

        # Per-member thinking config
        member_tc = _compute_thinking_for_model(thinking_params, model_full_val) if not is_custom else None
        if member_tc is None:
            member_tc = thinking_config

        # Central pool quota / rate checks
        try:
            try:
                if is_custom:
                    if not await check_custom_pool_rate(model_id_val):
                        raise RuntimeError("custom_endpoint_rate_limited")
                else:
                    has_quota = await router.acquire_quota(estimated_tokens, model_alias)
                    if not has_quota:
                        apply_error_penalty(api_key_val, "rate_limit_rpm_tpm", model_id_val)
                        router.freeze_key(api_key_val, config.KEY_429_COOLDOWN_SECONDS, model_id_val, "rate_limit")
                        raise RuntimeError("quota_exhausted")

                # Build arguments for single acompletion call
                kwargs = {
                    "model": model_full_val,
                    "messages": messages,
                    "api_key": api_key_val,
                    "max_tokens": max_output,
                    "temperature": temperature,
                    "stream": is_stream,
                    "tools": tools,
                    "thinking_config": member_tc if not is_custom else None,
                }
                if is_custom:
                    kwargs["api_base"] = reservation["api_base"]
                    if extra_body:
                        kwargs["extra_body"] = extra_body

                resp = await acompletion(**kwargs)
                return resp, api_key_val, model_id_val, estimated_tokens, reservation
            except Exception as e:
                raise PoolCallError(e, api_key_val, model_id_val, reservation)
        finally:
            # Key resolution release handles concurrency
            if api_key_val:
                router.release_key(api_key_val)


pool_manager = PoolManager()

"""Centralized Pool Manager — manages key pool, rotation, quota check, and retries.

This handles routing to either Gemini native (via GenAI SDK or HTTP) or Custom Endpoints.
Both stream and non-stream methods are supported, returning/yielding OpenAI-compatible structures.
Proxies (OpenCodeProxy, ClaudeProxy) delegate pool/retry logic to this class.
"""

import asyncio
import time
import random
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple, Union

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
    """
    Recomputes the thinking configuration for a specific model.
    This is necessary to correctly handle differences in thinking config formats
    or capabilities between different model versions (e.g., V3 vs V2).

    Args:
        thinking_params: Original thinking parameters from the request.
        model_id: The concrete model ID for which to compute the thinking config.

    Returns:
        A dictionary representing the resolved thinking configuration, or None if not applicable.
    """
    if not thinking_params:
        return None
    from src.core.providers.gemini_thinking import resolve_thinking_config
    res = resolve_thinking_config(
        model_id=model_id,
        thinking_level=thinking_params.get("thinking_level"),
        thinking_budget=thinking_params.get("thinking_budget"),
        include_thoughts=thinking_params.get("include_thoughts"),
        is_sub_agent=False,
    )
    return res if res else None


def _classify_error(e: Exception) -> str:
    """
    Helper function to classify raw exceptions into unified, categorized reasons.
    This classification is crucial for the PoolManager's adaptive retry and key freezing logic.
    It first attempts to classify using specific Gemini error classifications, then falls back
    to text-based matching of common error messages.

    Design Decision (Text Matching):
    The text-based fallback matching for error classification (`if "rate limit" in msg_lower...`)
    is a pragmatic decision. While less robust than a dedicated error code registry,
    it provides immediate resilience against varied error messages from external APIs
    and allows for rapid adaptation without requiring a full refactor of error handling
    across all providers. This is a trade-off for speed and operational robustness in a dynamic environment.
    """
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
    """
    Centralized Pool Manager responsible for orchestrating model pool loops,
    key rotation, quota checks, and intelligent retry mechanisms.
    This class handles routing requests to either Gemini native APIs (via GenAI SDK or HTTP)
    or Custom Endpoints, supporting both streaming and non-streaming completions.
    Proxies (e.g., OpenCodeProxy, ClaudeProxy) delegate their core pool/retry logic to this class.

    Design Decision (Monolithic Complexity):
    The `PoolManager` is intentionally designed as a more monolithic component,
    encapsulating extensive retry and failover logic. This choice prioritizes:
    1. Streamlined Control Flow: Centralizing complex error handling and retry loops
       makes the system more predictable and easier to manage in production,
       especially given the unpredictable nature of external LLM API failures.
    2. Performance Optimization: Minimizes overhead from inter-service calls,
       crucial for low-latency API routing.
    3. Rapid Reusability: Allows various proxies to delegate to a single, robust
       mechanism without replicating intricate logic.
    """

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
        """
        Handles unified pool calls for non-streaming completions.
        This method orchestrates the selection of models and API keys, applies retry logic,
        and manages error handling within a resilient pool loop. It supports both
        pool-based rotation (failover between multiple model members) and standalone modes.

        Args:
            model_alias: The alias of the model to use.
            messages: List of message dictionaries for the completion request.
            tools: Optional list of tool definitions.
            temperature: Optional sampling temperature.
            max_tokens: Optional maximum number of tokens to generate.
            thinking_config: Optional configuration for thinking process (e.g., XML thinking).
            account: Optional account information for limits and overrides.
            extra_body: Optional extra parameters to include in the request body.
            thinking_params: Optional parameters related to thinking configuration.

        Returns:
            A dictionary containing the LLM response, used API key, model ID, input tokens,
            and reservation details.

        Raises:
            RuntimeError: If the pool is exhausted or all retry attempts fail.
        """
        pool = router.resolve_pool(model_alias)
        if pool:
            # --- POOL MODE (concurrent worker pool) ---
            # Shared pool: each member handles 1 request at a time.
            # Local tracking per request: exhausted_members, consecutive_transient, start_time.
            exhausted_members: Set[str] = set()
            consecutive_transient = 0
            start_time = time.time()
            member: str
            member = await pool.acquire(timeout=pool.max_retry_seconds)
            pool_try = -1

            try:
                while time.time() - start_time < pool.max_retry_seconds:
                    if member in exhausted_members:
                        member = await pool.acquire(skip=exhausted_members, timeout=max(1.0, pool.max_retry_seconds - (time.time() - start_time)))

                    pool_try += 1
                    api_key_val = None
                    model_id_val = None
                    reservation = {}
                    try:
                        resp, api_key_val, model_id_val, input_tokens, reservation = await self._resolve_and_call(
                            model_alias, messages, tools, temperature, max_tokens, thinking_config,
                            account, extra_body, pool_mode=True, pool=pool, is_stream=False,
                            attempt=pool_try, thinking_params=thinking_params,
                            member_override=member,
                        )
                        member_used = reservation.get("model_alias", member)
                        is_custom = reservation.get("provider") == "custom"
                        if is_custom:
                            endpoint_manager.mark_endpoint_success(reservation.get("name", member_used))
                        else:
                            router.update_model_health(member_used, success=True)
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
                        member_used = reservation.get("model_alias", member) if reservation else member
                        is_custom = reservation.get("provider") == "custom"
                        if is_custom:
                            logger.warning("[PoolManager] Custom endpoint failed: %s, swapping...", e)
                            endpoint_manager.mark_endpoint_failure(reservation.get("name", member_used))
                            pool.release(member)
                            exhausted_members.add(member)
                            continue

                        reason = _classify_error(e)
                        if reason in TRANSIENT_REASONS:
                            count_transient_error(reason)
                            if reason == "rate_limit":
                                router.record_429()

                            if api_key_val and model_id_val:
                                router.freeze_key(api_key_val, config.KEY_429_COOLDOWN_SECONDS, model_id_val, reason)
                                apply_error_penalty(api_key_val, reason, model_id_val)

                            consecutive_transient += 1
                            if consecutive_transient >= config.POOL_SWAP_FAILURES:
                                logger.warning("[PoolManager] Transient error %s on member %s, too many retries - swapping...", reason, member_used)
                                pool.release(member)
                                exhausted_members.add(member)
                                consecutive_transient = 0
                            else:
                                delay = _retry_delay(consecutive_transient)
                                logger.warning("[PoolManager] Transient error %s on member %s, retrying in %.1fs (attempt %d)", reason, member_used, delay, consecutive_transient)
                                await asyncio.sleep(delay)
                            continue
                        else:
                            consecutive_transient = 0
                            logger.error("[PoolManager] Hard error %s on member %s: %s", reason, member_used, e)
                            if api_key_val and model_id_val:
                                cooldown = config.KEY_INVALID_COOLDOWN_SECONDS if reason == "invalid_key" else config.KEY_429_COOLDOWN_SECONDS
                                router.freeze_key(api_key_val, cooldown, model_id_val, reason)
                                apply_error_penalty(api_key_val, reason, model_id_val)
                            pool.release(member)
                            exhausted_members.add(member)

                raise RuntimeError("Pool max_retry_seconds exhausted")
            except:
                raise
            finally:
                if member:
                    try:
                        pool.release(member)
                    except RuntimeError:
                        pass
        else:
            # --- STANDALONE MODE ---
            # Directly calls the model without pool rotation.
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
                        # Đóng băng key tương tự và áp dụng Timing Jitter khi ngủ
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
        """
        Handles unified pool calls for streaming completions.
        This method orchestrates the selection of models and API keys, applies retry logic,
        and manages error handling within a resilient pool loop for streaming responses.
        It supports both pool-based rotation (failover between multiple model members) and standalone modes.

        Args:
            model_alias: The alias of the model to use.
            messages: List of message dictionaries for the completion request.
            tools: Optional list of tool definitions.
            temperature: Optional sampling temperature.
            max_tokens: Optional maximum number of tokens to generate.
            thinking_config: Optional configuration for thinking process (e.g., XML thinking).
            account: Optional account information for limits and overrides.
            extra_body: Optional extra parameters to include in the request body.
            thinking_params: Optional parameters related to thinking configuration.

        Yields:
            A dictionary representing chunks of the streaming LLM response, along with
            used API key, model ID, input tokens, and reservation details.

        Raises:
            RuntimeError: If the pool is exhausted or all retry attempts fail.
            Exception: If the stream is interrupted after committing (sending headers/chunks to client),
                       retry with a different key/model is not possible.
        """
        pool = router.resolve_pool(model_alias)
        if pool:
            # --- POOL STREAM MODE (concurrent worker pool) ---
            exhausted_members: Set[str] = set()
            consecutive_transient = 0
            start_time = time.time()
            member: str
            member = await pool.acquire(timeout=pool.max_retry_seconds)
            committed = False

            try:
                while time.time() - start_time < pool.max_retry_seconds:
                    if member in exhausted_members:
                        member = await pool.acquire(skip=exhausted_members, timeout=max(1.0, pool.max_retry_seconds - (time.time() - start_time)))

                    pool_try = -1
                    pool_try += 1
                    api_key_val = None
                    model_id_val = None
                    reservation = {}
                    try:
                        max_output = min(int(max_tokens or config.MAX_OUTPUT_TOKENS), config.MAX_OUTPUT_TOKENS)
                        estimated_tokens = len(str(messages)) // 4 + max_output
                        model_alias_val, model_id_val, api_key_val, model_full_val, reservation = await _resolve_model(
                            {"model": model_alias}, model_alias, account=account, estimated_tokens=estimated_tokens,
                            retry_attempt=pool_try, pool_mode=True, member_override=member,
                        )

                        is_custom = reservation.get("provider") == "custom"
                        member_used = reservation.get("model_alias", member)

                        member_tc = _compute_thinking_for_model(thinking_params, model_full_val) if not is_custom else None
                        if member_tc is None:
                            member_tc = thinking_config
                        if member_tc and "lite" in model_full_val.lower():
                            member_tc = {}

                        has_quota = await router.acquire_quota(estimated_tokens, model_alias)
                        if not has_quota:
                            apply_error_penalty(api_key_val, "rate_limit_rpm_tpm", model_id_val)
                            router.freeze_key(api_key_val, config.KEY_429_COOLDOWN_SECONDS, model_id_val, "rate_limit")
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

                            if is_custom:
                                endpoint_manager.mark_endpoint_success(reservation.get("name", member_used))
                            else:
                                router.update_model_health(member_used, success=True)
                            return
                        finally:
                            if api_key_val:
                                router.release_key(api_key_val)

                    except Exception as e:
                        if committed:
                            logger.error("[PoolManager] Stream interrupted after committing: %s", e)
                            raise

                        member_used = reservation.get("model_alias", member) if reservation else member
                        is_custom = reservation.get("provider") == "custom"
                        if is_custom:
                            logger.warning("[PoolManager] Custom endpoint stream failed: %s, swapping...", e)
                            endpoint_manager.mark_endpoint_failure(reservation.get("name", member_used))
                            pool.release(member)
                            exhausted_members.add(member)
                            continue

                        reason = _classify_error(e)
                        if reason in TRANSIENT_REASONS:
                            count_transient_error(reason)
                            if reason == "rate_limit":
                                router.record_429()

                            if api_key_val and model_id_val:
                                router.freeze_key(api_key_val, config.KEY_429_COOLDOWN_SECONDS, model_id_val, reason)
                                apply_error_penalty(api_key_val, reason, model_id_val)

                            if not reservation:
                                logger.warning("[PoolManager] Transient stream error %s on member %s, no key available - swapping...", reason, member_used)
                                pool.release(member)
                                exhausted_members.add(member)
                                consecutive_transient = 0
                            else:
                                consecutive_transient += 1
                                if consecutive_transient >= config.POOL_SWAP_FAILURES:
                                    logger.warning("[PoolManager] Transient stream error %s on member %s, too many retries - swapping...", reason, member_used)
                                    pool.release(member)
                                    exhausted_members.add(member)
                                    consecutive_transient = 0
                                else:
                                    delay = _retry_delay(consecutive_transient)
                                    logger.warning("[PoolManager] Transient stream error %s on member %s, retrying in %.1fs (attempt %d)", reason, member_used, delay, consecutive_transient)
                                    await asyncio.sleep(delay)
                            continue
                        else:
                            consecutive_transient = 0
                            logger.error("[PoolManager] Hard stream error %s: %s", reason, e)
                            if api_key_val and model_id_val:
                                cooldown = config.KEY_INVALID_COOLDOWN_SECONDS if reason == "invalid_key" else config.KEY_429_COOLDOWN_SECONDS
                                router.freeze_key(api_key_val, cooldown, model_id_val, reason)
                                apply_error_penalty(api_key_val, reason, model_id_val)
                            pool.release(member)
                            exhausted_members.add(member)

                raise RuntimeError("Pool stream max_retry_seconds exhausted")
            finally:
                try:
                    pool.release(member)
                except RuntimeError:
                    pass
        else:
            # --- CHẾ ĐỘ ĐƠN LẺ STREAM (STANDALONE STREAM MODE) ---
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

                    member_tc = _compute_thinking_for_model(thinking_params, model_full_val) if not thinking_config else thinking_config
                    if member_tc and "lite" in model_full_val.lower():
                        member_tc = {}

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
        member_override: Optional[str] = None,
    ) -> Tuple[Any, str, str, int, dict]:
        """
        Internal helper method to resolve the optimal API key and model, check quotas,
        and perform a single API call to the LLM (either Gemini native or Custom Endpoint).
        This method encapsulates the core logic of key reservation, rate limiting checks,
        and preparing arguments for the `acompletion` facade.

        Args:
            model_alias: The alias of the model to use.
            messages: List of message dictionaries for the completion request.
            tools: Optional list of tool definitions.
            temperature: Optional sampling temperature.
            max_tokens: Optional maximum number of tokens to generate.
            thinking_config: Optional configuration for thinking process.
            account: Optional account information.
            extra_body: Optional extra parameters for the request body.
            pool_mode: True if operating within a pool loop, False for standalone.
            pool: The ModelPool instance if in pool_mode.
            is_stream: True if the call is for a streaming response.
            attempt: Current retry attempt count.
            thinking_params: Optional parameters related to thinking configuration.

        Returns:
            A tuple containing: (response from LLM, used API key, concrete model ID,
            estimated input tokens, reservation details).

        Raises:
            PoolCallError: Wraps any exception encountered during the API call or quota check.
            RuntimeError: If custom endpoint rate limit is exceeded or quota is exhausted.
        """
        max_output = min(int(max_tokens or config.MAX_OUTPUT_TOKENS), config.MAX_OUTPUT_TOKENS)
        estimated_tokens = len(str(messages)) // 4 + max_output

        model_alias_val, model_id_val, api_key_val, model_full_val, reservation = await _resolve_model(
            {"model": model_alias}, model_alias, account=account, estimated_tokens=estimated_tokens,
            retry_attempt=attempt, pool_mode=pool_mode, member_override=member_override,
        )

        is_custom = reservation.get("provider") == "custom"
        member_used = reservation.get("model_alias", member_override) if (pool_mode and pool and member_override) else model_alias_val

        # Per-member thinking config
        member_tc = _compute_thinking_for_model(thinking_params, model_full_val) if not is_custom else None
        if member_tc is None:
            member_tc = thinking_config
        if member_tc and "lite" in model_full_val.lower():
            member_tc = {}

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

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
            # --- CHẾ ĐỘ POOL (POOL MODE) ---
            # Sử dụng nhóm các model thành viên (ví dụ: gemini-flash xoay vòng giữa 35, 30, 25)
            pool.start()
            pool_try = -1
            while not pool.exhausted:
                pool_try += 1
                api_key_val = None
                model_id_val = None
                reservation = {}
                try:
                    # Quyết định chọn model và key thích hợp nhất (có cơ chế Double Random & Jitter)
                    resp, api_key_val, model_id_val, input_tokens, reservation = await self._resolve_and_call(
                        model_alias, messages, tools, temperature, max_tokens, thinking_config,
                        account, extra_body, pool_mode=True, pool=pool, is_stream=False,
                        attempt=pool_try, thinking_params=thinking_params,
                    )
                    # Giao dịch thành công -> đánh dấu sức khỏe của model / custom endpoint hoạt động tốt
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
                    # Rút trích thông tin key và model từ lỗi PoolCallError đóng gói
                    if isinstance(e, PoolCallError):
                        api_key_val = e.api_key
                        model_id_val = e.model_id
                        reservation = e.reservation or {}
                        e = e.original_error
                    member_used = reservation.get("model_alias", pool.current_model) if reservation else pool.current_model
                    is_custom = reservation.get("provider") == "custom"
                    if is_custom:
                        # Gặp lỗi với custom endpoint (OpenAI SDK backend) -> swap ngay lập tức
                        logger.warning("[PoolManager] Custom endpoint failed: %s, swapping...", e)
                        endpoint_manager.mark_endpoint_failure(reservation.get("name", member_used))
                        pool.record_failure(member_used, "custom_endpoint_error")
                        pool.swap()
                        continue

                    # Phân loại lỗi trả về từ Gemini API
                    reason = _classify_error(e)
                    if reason in TRANSIENT_REASONS:
                        # --- XỬ LÝ LỖI TẠM THỜI (SOFT HANDLING) ---
                        # 429, 503, timeout: KHÔNG đóng băng lâu (3600s), KHÔNG tính là lỗi thành viên hỏng.
                        count_transient_error(reason)
                        if reason == "rate_limit":
                            router.record_429()

                        # Đóng băng key cực ngắn (KEY_429_COOLDOWN_SECONDS ~ 8-15s) và áp penalty để giảm điểm ưu tiên
                        if api_key_val and model_id_val:
                            router.freeze_key(api_key_val, config.KEY_429_COOLDOWN_SECONDS, model_id_val, reason)
                            apply_error_penalty(api_key_val, reason, model_id_val)

                        # Tăng bộ đếm lỗi tạm thời của pool. Nếu lỗi liên tiếp >= POOL_SWAP_FAILURES (5) -> swap sang model khác
                        pool._consecutive_transient += 1
                        if pool._consecutive_transient >= config.POOL_SWAP_FAILURES:
                            logger.warning("[PoolManager] Transient error %s on member %s, too many retries - swapping...", reason, member_used)
                            pool._consecutive_transient = 0
                            pool.swap()
                        else:
                            # Đợi một thời gian trễ ngẫu nhiên (Jitter) trước khi thử lại với key khác
                            delay = _retry_delay(pool._consecutive_transient)
                            logger.warning("[PoolManager] Transient error %s on member %s, retrying in %.1fs (attempt %d)", reason, member_used, delay, pool._consecutive_transient)
                            await asyncio.sleep(delay)
                        continue
                    else:
                        # --- XỬ LÝ LỖI VĨNH VIỄN (HARD ERROR) ---
                        # Lỗi invalid_key, billing, bad_request: Đóng băng lâu và swap model lập tức.
                        pool._consecutive_transient = 0
                        logger.error("[PoolManager] Hard error %s on member %s: %s", reason, member_used, e)
                        if api_key_val and model_id_val:
                            cooldown = config.KEY_INVALID_COOLDOWN_SECONDS if reason == "invalid_key" else config.KEY_429_COOLDOWN_SECONDS
                            router.freeze_key(api_key_val, cooldown, model_id_val, reason)
                            apply_error_penalty(api_key_val, reason, model_id_val)
                        pool.record_failure(member_used, reason)
                        pool.swap()
            raise RuntimeError("Pool exhausted or all attempts failed")
        else:
            # --- CHẾ ĐỘ ĐƠN LẺ (STANDALONE MODE) ---
            # Gọi trực tiếp model không qua pool xoay vòng
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
        """Unified pool call for streaming completions. Yields dict chunks."""
        pool = router.resolve_pool(model_alias)
        if pool:
            # --- CHẾ ĐỘ POOL STREAM ---
            pool.start()
            committed = False  # Khi stream đã yield ra chunk đầu tiên, committed=True và KHÔNG được phép retry/swap nữa
            pool_try = -1
            while not pool.exhausted:
                pool_try += 1
                api_key_val = None
                model_id_val = None
                reservation = {}
                try:
                    # Đăng ký và giữ key từ resolver
                    max_output = min(int(max_tokens or config.MAX_OUTPUT_TOKENS), config.MAX_OUTPUT_TOKENS)
                    estimated_tokens = len(str(messages)) // 4 + max_output
                    model_alias_val, model_id_val, api_key_val, model_full_val, reservation = await _resolve_model(
                        {"model": model_alias}, model_alias, account=account, estimated_tokens=estimated_tokens,
                        retry_attempt=pool_try, pool_mode=True
                    )

                    is_custom = reservation.get("provider") == "custom"
                    member_used = reservation.get("model_alias", pool.current_model)

                    # Tính toán thinking config động cho member được chọn
                    member_tc = _compute_thinking_for_model(thinking_params, model_full_val) if not is_custom else None
                    if member_tc is None:
                        member_tc = thinking_config
                    if member_tc and "lite" in model_full_val.lower():
                        member_tc = {}

                    # Kiểm tra và trừ hạn ngạch tài khoản (Quota check)
                    has_quota = await router.acquire_quota(estimated_tokens, model_alias)
                    if not has_quota:
                        apply_error_penalty(api_key_val, "rate_limit_rpm_tpm", model_id_val)
                        router.freeze_key(api_key_val, config.KEY_429_COOLDOWN_SECONDS, model_id_val, "rate_limit")
                        raise RuntimeError("quota_exhausted")

                    # Thực hiện gọi API Stream
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
                            committed = True  # Đánh dấu stream đã ghi nhận thành công dữ liệu đầu tiên
                            yield {
                                "chunk": chunk,
                                "api_key": api_key_val,
                                "model_id": model_id_val,
                                "input_tokens": estimated_tokens,
                                "reservation": reservation
                            }
                        
                        # Thành công -> cập nhật trạng thái tốt
                        if is_custom:
                            endpoint_manager.mark_endpoint_success(reservation.get("name", member_used))
                        else:
                            router.update_model_health(member_used, success=True)
                        pool.record_success()
                        return
                    finally:
                        # Bắt buộc phải giải phóng key sau khi tác vụ stream kết thúc/gặp lỗi
                        if api_key_val:
                            router.release_key(api_key_val)

                except Exception as e:
                    # Nếu stream đang chạy dở mà đứt giữa chừng -> không thể thực hiện retry với key/model khác (đã gửi headers/chunk cho client)
                    if committed:
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
                        # Lỗi tạm thời (Soft handling) trong luồng stream
                        count_transient_error(reason)
                        if reason == "rate_limit":
                            router.record_429()

                        if api_key_val and model_id_val:
                            router.freeze_key(api_key_val, config.KEY_429_COOLDOWN_SECONDS, model_id_val, reason)
                            apply_error_penalty(api_key_val, reason, model_id_val)

                        if not reservation:
                            # Không có key nào để giữ -> swap model luôn
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
                                # Chờ Timing Jitter rồi retry
                                delay = _retry_delay(pool._consecutive_transient)
                                logger.warning("[PoolManager] Transient stream error %s on member %s, retrying in %.1fs (attempt %d)", reason, member_used, delay, pool._consecutive_transient)
                                await asyncio.sleep(delay)
                        continue
                    else:
                        # Lỗi cứng (Hard error) -> đóng băng key dài hạn & swap ngay
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

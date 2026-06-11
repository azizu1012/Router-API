"""Error classification and pool-retry logic for OpenCode proxy.

Functions here handle error classification, key freezing, penalty
application, and pool-swap coordination.
"""

from typing import Any, Optional

from fastapi import HTTPException

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.limits import apply_error_penalty
from src.core.providers.gemini.error import classify
from .stream_executor import LiteLLMTransientError


async def classify_pool_error(
    e: Exception,
    pool: Any,
    actual_alias: str,
    api_key_val: Optional[str],
    model_id_val: Optional[str],
) -> bool:
    """Classify a pool-mode error, freeze keys, and trigger pool swap.

    Returns True if the caller should retry (pool can still rotate).
    Raises ``HTTPException`` for non-recoverable errors.
    """
    if isinstance(e, LiteLLMTransientError):
        reason = "rate_limit"
        is_region_quota = e.is_region_quota
    else:
        text = str(e).lower()

        # Bad Request (400) — instant fail, no retry
        if "400" in text and "failed_precondition" not in text and ("invalid_argument" in text or "bad_request" in text):
            logger.error("[OpenCode Bad Request] %s", text[:200])
            if api_key_val:
                router.freeze_key(api_key_val, 2, model_id_val, "bad_request_spam_prevent")
            raise HTTPException(status_code=400, detail={
                "error": {"message": f"LLM rejected payload: {text[:200]}", "type": "invalid_request_error"}
            })

        # Billing/Failed Precondition (400) — freeze hard, swap pool
        if "400" in text and "failed_precondition" in text:
            logger.error("[OpenCode Billing] %s", text[:200])
            if api_key_val:
                router.freeze_key(api_key_val, 300, model_id_val, "billing_error")
                apply_error_penalty(api_key_val, "billing_error", model_id_val)
            router.record_failure("billing_error")
            pool.record_failure(actual_alias, "billing_error")
            if not pool.swap():
                if pool.exhausted:
                    raise HTTPException(status_code=503, detail={"error": {"message": "Pool exhausted", "type": "api_error"}})
                import asyncio
                await asyncio.sleep(min(15.0, pool.remaining_time()))
                pool.reset_cycle()
            return True

        # Client Cancelled
        if "499" in text or "cancelled" in text:
            raise HTTPException(status_code=503, detail={
                "error": {"message": "Request cancelled by client", "type": "api_error"}
            })

        is_region_quota = "apirequestsperminuteperprojectperregion" in text or "api_requests_per_minute_per_project_per_region" in text
        reason = classify(e)

    if reason == "rate_limit":
        router.record_429()
    if reason == "unknown":
        logger.error("[OpenCode Pool Error] key=...%s model=%s: %s", (api_key_val or "N/A")[-4:], actual_alias, e)

    if api_key_val:
        router.freeze_key(api_key_val, 0, model_id_val, reason)
        if reason not in ("bad_request_spam_prevent", "invalid_key"):
            apply_error_penalty(api_key_val, reason, model_id_val)
    router.record_failure(reason)
    logger.warning("[OpenCode Pool Retry] key=...%s model=%s reason=%s region_quota=%s", (api_key_val or "N/A")[-4:], actual_alias, reason, is_region_quota)

    pool.record_failure(actual_alias, reason)
    if not pool.swap():
        if pool.exhausted:
            raise HTTPException(status_code=503, detail={"error": {"message": "Pool exhausted", "type": "api_error"}})
        if is_region_quota:
            import asyncio
            await asyncio.sleep(25)
        import asyncio
        wait = min(15.0, pool.remaining_time())
        await asyncio.sleep(wait)
        pool.reset_cycle()
        return True

    if is_region_quota:
        logger.warning("[Region Quota] Hit region limit, waiting 25s before retry...")
        import asyncio
        await asyncio.sleep(25)

    return True


def classify_standalone_error(
    e: Exception,
    attempt: int,
    api_key_val: Optional[str],
    model_id_val: Optional[str],
) -> bool:
    """Classify a standalone (non-pool) error, freeze keys, apply penalty.

    Returns True if the caller should retry.
    """
    text = str(e).lower()

    if "400" in text and "failed_precondition" not in text and ("invalid_argument" in text or "bad_request" in text):
        logger.error("[OpenCode Bad Request] %s", text[:200])
        if api_key_val:
            router.freeze_key(api_key_val, 2, model_id_val, "bad_request_spam_prevent")
        return False

    if "499" in text or "cancelled" in text:
        return False

    reason = classify(e)
    if reason == "rate_limit":
        router.record_429()
    if reason == "unknown":
        logger.error("[OpenCode Error] key=...%s: %s", (api_key_val or "N/A")[-4:], e)

    if api_key_val:
        router.freeze_key(api_key_val, 0, model_id_val, reason)
        if reason not in ("bad_request_spam_prevent", "invalid_key"):
            apply_error_penalty(api_key_val, reason, model_id_val)
    router.record_failure(reason)
    logger.warning("[OpenCode Retry] key=...%s attempt=%d/%d reason=%s", (api_key_val or "N/A")[-4:], attempt + 1, config.MAX_RETRIES, reason)

    return attempt < config.MAX_RETRIES - 1

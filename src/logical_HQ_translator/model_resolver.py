import asyncio
import random
import time
from typing import Any, Dict, Optional, Tuple

# pyright: reportAttributeAccessIssue=false

from fastapi import HTTPException

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.providers import _custom_endpoint_manager as endpoint_manager
from src.core.router import router

def _has_account_endpoint(account: Optional[Dict[str, Any]]) -> bool:
    if not account:
        return False
    eps = endpoint_manager.get_endpoints_for_account(account)
    return any(ep.get("enabled", True) for ep in eps)

def _retry_delay(attempt: int) -> float:
    return random.uniform(0.5, 3.0)

async def _resolve_model(body: Dict[str, Any], pool_alias_override: Optional[str] = None, account: Optional[Dict[str, Any]] = None, estimated_tokens: int = 0, retry_attempt: int = 0, pool_mode: bool = False, member_override: Optional[str] = None) -> Tuple[str, str, str, str, Dict[str, Any]]:
    """Resolve model alias and acquire an available Gemini API key.

    Resolves target model alias (using account-specific alias if provided) and
    reserves a key through APIRouter.reserve_key using the Double Random algorithm.
    Custom endpoints are routed via custom_endpoint_passthrough.py; if standard Gemini
    keys are frozen in standalone mode, legacy fallback custom endpoints can be engaged.

    Args:
        body: Request payload dictionary containing model name.
        pool_alias_override: Optional explicit pool alias override.
        account: Authenticated account details for quota and per-account aliases.
        estimated_tokens: Estimated token count for rate limiter check.
        retry_attempt: Current retry attempt index.
        pool_mode: Whether running in pool worker mode (True) or standalone mode (False).
        member_override: Specific member model to use.

    Returns:
        Tuple of (model_alias, actual_model_id, api_key, model_identifier, reservation_dict)
    """
    if pool_alias_override:
        model_alias = pool_alias_override
    else:
        model_alias = router.resolve_model_alias(body.get("model", ""), account=account)
    if not model_alias:
        model_alias = config.DEFAULT_MODEL_ALIAS
    model_id = router.get_model_id(model_alias)

    # In pool_mode, don't wait long — the pool loop handles retry timing.
    # In standalone mode, wait up to KEY_429_COOLDOWN × 2 for a key to become available.
    max_wait = config.GEMINI_API_KEY_INTERVAL * 2 if pool_mode else config.KEY_429_COOLDOWN_SECONDS * 2
    start_time = time.time()
    attempt = 0
    while True:
        if not router.is_global_cooldown_active():
            reservation = router.reserve_key(model_alias, model_id, account=account, estimated_tokens=estimated_tokens, retry_attempt=retry_attempt)
            if reservation:
                actual_model_id = reservation["model_id"]
                api_key = reservation["key"]
                
                # Spacing and throttling (random jitter + global spacing) to be safe
                try:
                    from src.core.providers.gemini_api_manager import api_manager
                    last_used = api_manager.pool.get_key_last_used(api_key)
                    logger.info("[Throttle] key=...%s last_used=%.1fs ago", api_key[-8:], time.time() - last_used)
                    await api_manager.pool.throttle(api_key, last_used)
                    api_manager.pool.record_key_usage(api_key)
                except Exception as e:
                    logger.warning("[Throttling] Failed to apply api_manager pacing delay: %s", e)
                
                return model_alias, actual_model_id, api_key, actual_model_id, reservation
        
        elapsed = time.time() - start_time
        if elapsed >= max_wait:
            break
        
        attempt += 1
        wait_time = min(_retry_delay(attempt), config.GEMINI_API_KEY_INTERVAL)
        if elapsed + wait_time > max_wait:
            wait_time = max_wait - elapsed
        if wait_time <= 0:
            break
        await asyncio.sleep(wait_time)

    # If standard keys are overloaded/frozen, try to use a fallback custom endpoint
    # (skip in pool_mode — PoolManager handles fallback via member iteration)
    fallback_info = endpoint_manager.get_first_fallback_model()
    if not pool_mode and fallback_info:
        fb_ep = fallback_info["endpoint"]
        fb_model = fallback_info["model_id"]
        logger.info("[FallbackEndpoint] Standard keys overloaded, routing to fallback endpoint %s with model %s", fb_ep["name"], fb_model)
        return model_alias, fb_model, fb_ep["auth_key"], fb_model, {
            "key": fb_ep["auth_key"],
            "model_alias": model_alias,
            "model_id": fb_model,
            "provider": "custom",
            "api_base": fb_ep["base_url"],
"api_format": fb_ep.get("api_format") or "openai",
        }

    if router.is_global_cooldown_active():
        raise HTTPException(status_code=503, detail={
            "type": "error", "error": {"type": "api_error", "message": "Global IP cooldown active. Please wait."}
        })
    raise HTTPException(
        status_code=429,
        detail={
            "type": "error",
            "error": {"type": "rate_limit_error", "message": "All API Keys are overloaded. Please try again."},
        },
    )


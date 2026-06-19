import asyncio
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
    ep = endpoint_manager.get_endpoint_for_account(account)
    return ep is not None and ep.get("enabled", True)

def _retry_delay(attempt: int) -> float:
    import random
    if attempt >= config.POOL_SWAP_FAILURES * 2:
        return random.uniform(0.3, 0.7)
    base = min(config.GEMINI_API_KEY_INTERVAL * (2 ** attempt), config.KEY_429_COOLDOWN_SECONDS * 2)
    jitter = random.uniform(-base * 0.2, base * 0.2)
    return max(config.GEMINI_API_KEY_INTERVAL, base + jitter)

async def _resolve_model(body: Dict[str, Any], pool_alias_override: Optional[str] = None, account: Optional[Dict[str, Any]] = None, estimated_tokens: int = 0, retry_attempt: int = 0, pool_mode: bool = False) -> Tuple[str, str, str, str, Dict[str, Any]]:
    if pool_alias_override:
        model_alias = pool_alias_override
    else:
        model_alias = router.resolve_model_alias(body.get("model", ""))
    if not model_alias:
        model_alias = config.DEFAULT_MODEL_ALIAS
    model_id = router.get_model_id(model_alias)

    # If account has dedicated endpoint, route 100% there on first attempt only
    ep = endpoint_manager.get_endpoint_for_account(account)
    if ep and ep.get("enabled", True) and retry_attempt == 0:
        ep_name = ep.get("name", "")
        if endpoint_manager.is_endpoint_frozen(ep_name):
            logger.warning("[AccountEndpoint] %s is frozen (circuit breaker), skipping", ep_name)
        else:
            enabled_models = ep.get("enabled_models", [])
            all_models = ep.get("models", [])

            if not enabled_models and not all_models:
                logger.warning("[AccountEndpoint] %s has no models configured, skipping", ep_name)
            else:
                # Lấy model đầu tiên từ enabled_models hoặc all_models
                target_model = (enabled_models or all_models)[0]

                try:
                    alive = await endpoint_manager.ping_endpoint(ep)
                    if not alive:
                        logger.warning("[AccountEndpoint] %s ping failed (%s), fallback to Gemini", ep_name, ep.get("base_url", "?"))
                    else:
                        return model_alias, target_model, ep["auth_key"], target_model, {
                            "key": ep["auth_key"],
                            "name": ep_name,
                            "model_alias": model_alias,
                            "model_id": target_model,
                            "provider": "custom",
                            "api_base": ep["base_url"],
                        }
                except Exception as e:
                    logger.warning("[AccountEndpoint] %s ping error (%s), fallback to Gemini", ep_name, e)

    # Prioritize pool-assigned custom endpoints if available and enabled (circuit breaker and ping check)
    pool_models = router.get_pool_custom_models(model_alias)
    if pool_models and retry_attempt == 0:
        for pm in pool_models:
            ep = pm["endpoint"]
            ep_name = ep.get("name", "")
            if endpoint_manager.is_endpoint_frozen(ep_name):
                logger.warning("[CustomPoolEndpoint] %s is frozen, skipping", ep_name)
                continue

            target_model = pm["model_id"]
            try:
                alive = await endpoint_manager.ping_endpoint(ep)
                if not alive:
                    logger.warning("[CustomPoolEndpoint] %s ping failed, skipping", ep_name)
                    continue

                logger.info("[CustomPoolEndpoint] Routing model_alias=%s to custom endpoint %s with model %s", model_alias, ep_name, target_model)
                return model_alias, target_model, ep["auth_key"], target_model, {
                    "key": ep["auth_key"],
                    "name": ep_name,
                    "model_alias": model_alias,
                    "model_id": target_model,
                    "provider": "custom",
                    "api_base": ep["base_url"],
                }
            except Exception as e:
                logger.warning("[CustomPoolEndpoint] %s ping error: %s", ep_name, e)

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


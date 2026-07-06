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
    if pool_alias_override:
        model_alias = pool_alias_override
    else:
        model_alias = router.resolve_model_alias(body.get("model", ""))
    if not model_alias:
        model_alias = config.DEFAULT_MODEL_ALIAS
    model_id = router.get_model_id(model_alias)

    # Step 0: Member override in pool mode — route directly to the acquired member
    if pool_mode and member_override:
        pool_models = router.get_pool_custom_models(model_alias)
        for pm in pool_models:
            if pm["endpoint"].get("name", "") == member_override:
                ep = pm["endpoint"]
                ep_name = ep.get("name", "")
                if endpoint_manager.is_endpoint_frozen(ep_name):
                    logger.warning("[MemberOverride] %s is frozen, falling back to Gemini", ep_name)
                    break
                target_model = pm["model_id"]
                try:
                    alive = await endpoint_manager.ping_endpoint(ep)
                    if not alive:
                        logger.warning("[MemberOverride] %s ping failed, falling back to Gemini", ep_name)
                        break
                    logger.info("[MemberOverride] Routing member=%s to endpoint %s with model %s", member_override, ep_name, target_model)
                    return model_alias, target_model, ep["auth_key"], target_model, {
                        "key": ep["auth_key"],
                        "name": ep_name,
                        "model_alias": model_alias,
                        "model_id": target_model,
                        "provider": "custom",
                        "api_base": ep["base_url"],
                    }
                except Exception as e:
                    logger.warning("[MemberOverride] %s error: %s, falling back to Gemini", ep_name, e)
                    break
        # member_override is a Gemini member or custom endpoint unavailable → skip steps 1,2, go to key reservation
    else:
        # Step 1: Account endpoint (100% override mode — no pool_assignments)
        # Endpoints with pool_assignments skip step1 and are handled by step2 as pool members.
        if retry_attempt == 0:
            for ep in endpoint_manager.get_endpoints_for_account(account):
                if not ep.get("enabled", True):
                    continue
                ep_name = ep.get("name", "")
                if endpoint_manager.is_endpoint_frozen(ep_name):
                    logger.warning("[AccountEndpoint] %s is frozen (circuit breaker), skipping", ep_name)
                    continue

                pool_assignments = ep.get("pool_assignments", {})
                if pool_assignments:
                    logger.debug("[AccountEndpoint] %s has pool_assignments, skipping step1 (handled by pool members)", ep_name)
                    continue

                # No pool_assignments → route 100% traffic through this endpoint (backward compat)
                enabled_models = ep.get("enabled_models", [])
                all_models = ep.get("models", [])

                if not enabled_models and not all_models:
                    logger.warning("[AccountEndpoint] %s has no models configured, skipping", ep_name)
                    continue

                target_model = (enabled_models or all_models)[0]

                try:
                    alive = await endpoint_manager.ping_endpoint(ep)
                    if not alive:
                        logger.warning("[AccountEndpoint] %s ping failed (%s), fallback to Gemini", ep_name, ep.get("base_url", "?"))
                        continue
                    return model_alias, target_model, ep["auth_key"], target_model, {
                        "key": ep["auth_key"],
                        "name": ep_name,
                        "model_alias": model_alias,
                        "model_id": target_model,
                        "provider": "custom",
                        "api_base": ep["base_url"],
                    }
                except Exception as e:
                    logger.warning("[AccountEndpoint] %s ping error (%s), trying next endpoint", ep_name, e)

        # Step 2: Prioritize pool-assigned custom endpoints — only when no member_override
        # (with member_override, custom endpoints are resolved in step 0)
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


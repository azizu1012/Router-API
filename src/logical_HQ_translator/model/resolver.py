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
    """Calculate retry delay with exponential backoff and +-20% jitter.

    Prevents Thundering Herd when multiple requests or pool members fail concurrently.
    """
    if attempt >= getattr(config, "POOL_SWAP_FAILURES", 5) * 2:
        return random.uniform(0.3, 0.7)
    interval = getattr(config, "GEMINI_API_KEY_INTERVAL", 2.5)
    max_cooldown = getattr(config, "KEY_429_COOLDOWN_SECONDS", 15) * 2
    base = min(interval * (2 ** max(0, attempt)), max_cooldown)
    jitter = random.uniform(-base * 0.2, base * 0.2)
    return max(0.5, base + jitter)

async def _resolve_model(body: Dict[str, Any], pool_alias_override: Optional[str] = None, account: Optional[Dict[str, Any]] = None, estimated_tokens: int = 0, retry_attempt: int = 0, pool_mode: bool = False, member_override: Optional[str] = None) -> Tuple[str, str, str, str, Dict[str, Any]]:
    """Resolve model alias and acquire an available Gemini API key.

    Resolves target model alias (using account-specific alias if provided) and
    reserves a key through APIRouter.reserve_key using the Double Random algorithm.
    Custom endpoints are routed via providers/custom_endpoint/passthrough.py; if standard Gemini
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

    from src.core.config.api_config import is_model_enabled, is_model_sunset, is_sunset_25
    if is_model_sunset(model_alias):
        raise HTTPException(
            status_code=400,
            detail={"type": "error", "error": {"type": "invalid_request_error", "message": f"Model '{model_alias}' has been discontinued."}}
        )
    if not is_model_enabled(model_alias):
        raise HTTPException(
            status_code=400,
            detail={"type": "error", "error": {"type": "invalid_request_error", "message": f"Model '{model_alias}' is currently disabled."}}
        )

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
                        "api_format": ep.get("api_format") or "openai",
                    }
                except Exception as e:
                    logger.warning("[MemberOverride] %s error: %s, falling back to Gemini", ep_name, e)
                    break
    else:
        # Step 1: Account endpoint (100% override mode — no pool_assignments)
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
                    continue

                enabled_models = ep.get("enabled_models", [])
                all_models = ep.get("models", [])
                if not enabled_models and not all_models:
                    continue

                target_model = (enabled_models or all_models)[0]
                try:
                    alive = await endpoint_manager.ping_endpoint(ep)
                    if not alive:
                        continue
                    return model_alias, target_model, ep["auth_key"], target_model, {
                        "key": ep["auth_key"],
                        "name": ep_name,
                        "model_alias": model_alias,
                        "model_id": target_model,
                        "provider": "custom",
                        "api_base": ep["base_url"],
                        "api_format": ep.get("api_format") or "openai",
                    }
                except Exception as e:
                    logger.warning("[AccountEndpoint] %s ping error (%s), trying next endpoint", ep_name, e)

        # Step 2: Prioritize pool-assigned custom endpoints — only when no member_override
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
                        "api_format": ep.get("api_format") or "openai",
                    }
                except Exception as e:
                    logger.warning("[CustomPoolEndpoint] %s ping error: %s", ep_name, e)

    # In pool_mode, don't wait long — the pool loop handles retry timing.
    # In standalone mode, keep key acquisition wait bounded so requests don't hang.
    max_wait = config.GEMINI_API_KEY_INTERVAL * 2 if pool_mode else min(float(config.KEY_429_COOLDOWN_SECONDS), 2.0)
    start_time = time.time()
    attempt = 0
    while True:
        if not router.is_global_cooldown_active():
            reservation = router.reserve_key(model_alias, model_id, account=account, estimated_tokens=estimated_tokens, retry_attempt=retry_attempt)
            if reservation:
                actual_model_id = reservation["model_id"]
                api_key = reservation["key"]
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



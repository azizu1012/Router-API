import asyncio
import time
from typing import Any, Dict, Optional, Tuple
from fastapi import HTTPException

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.providers import _custom_endpoint_manager
from src.core.router import router

def _has_account_endpoint(account: Optional[Dict[str, Any]]) -> bool:
    if not account:
        return False
    ep = _custom_endpoint_manager.get_endpoint_for_account(account)
    return ep is not None and ep.get("enabled", True)

def _retry_delay(attempt: int) -> float:
    import random
    if attempt >= 10:
        return random.uniform(0.3, 0.7)
    base = min(1.5 * (2 ** attempt), 10.0)
    jitter = random.uniform(-base * 0.2, base * 0.2)
    return max(1.0, base + jitter)

async def _resolve_model(body: Dict[str, Any], pool_alias_override: Optional[str] = None, account: Optional[Dict[str, Any]] = None, estimated_tokens: int = 0, retry_attempt: int = 0, pool_mode: bool = False) -> Tuple[str, str, str, str, Dict[str, Any]]:
    if pool_alias_override:
        model_alias = pool_alias_override
    else:
        model_alias = router.resolve_model_alias(body.get("model", ""))
    if not model_alias:
        model_alias = config.DEFAULT_MODEL_ALIAS
    model_id = router.get_model_id(model_alias)

    # If account has dedicated endpoint, route 100% there
    # Nếu endpoint lỗi → fallback Gemini Lite (pool_mode xử lý retry)
    ep = _custom_endpoint_manager.get_endpoint_for_account(account)
    if ep and ep.get("enabled", True):
        model_to_use = body.get("model", model_id)
        enabled_models = ep.get("enabled_models", [])
        if model_to_use not in enabled_models and model_id not in enabled_models:
            logger.warning("[AccountEndpoint] Model %s is not enabled on custom endpoint %s, fallback to Gemini Lite pool", model_to_use or model_id, ep["name"])
        else:
            try:
                import litellm
                test = await litellm.acompletion(
                    model=f"openai/{model_id}",
                    messages=[{"role": "user", "content": "ping"}],
                    api_key=ep["auth_key"],
                    api_base=ep["base_url"],
                    max_tokens=1,
                    stream=False,
                    request_timeout=5,
                )
                litellm_model = f"openai/{model_id}"
                return model_alias, model_id, ep["auth_key"], litellm_model, {
                    "key": ep["auth_key"],
                    "model_alias": model_alias,
                    "model_id": model_id,
                    "provider": "custom",
                    "api_base": ep["base_url"],
                }
            except Exception as e:
                logger.warning("[AccountEndpoint] %s ping failed (%s), fallback to Gemini Lite pool", model_id, e)

    # In pool_mode, don't wait long — the pool loop handles retry timing.
    # In standalone mode, wait up to 15s for a key to become available.
    max_wait = 2.0 if pool_mode else 15.0
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
                    last_used = api_manager._key_last_used.get(api_key, 0.0)
                    await api_manager._throttle_api_request(api_key, last_used)
                    api_manager._key_last_used[api_key] = time.time()
                except Exception as e:
                    logger.warning("[Throttling] Failed to apply api_manager pacing delay: %s", e)
                
                litellm_model = f"gemini/{actual_model_id}"
                return model_alias, actual_model_id, api_key, litellm_model, reservation
        
        elapsed = time.time() - start_time
        if elapsed >= max_wait:
            break
        
        attempt += 1
        wait_time = min(0.5 if pool_mode else 1.0, 0.2 * (1.5 ** attempt))
        if elapsed + wait_time > max_wait:
            wait_time = max_wait - elapsed
        if wait_time <= 0:
            break
        await asyncio.sleep(wait_time)

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


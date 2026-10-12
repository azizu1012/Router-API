"""Account and token quota enforcement for authenticated requests."""

from typing import Any, Dict, Optional

from src.backend.keys.accounts import get_key_db
from src.core.limits import account_limiter, token_limiter
from src.core.router import router
from src.core.providers import _custom_endpoint_manager
from src.core.limits.account_limiter import get_effective_limits_by_pool


_TOKEN_LIMIT_MESSAGES = {
    "token_concurrency_limit": "Token concurrency limit reached",
    "token_rpm_exceeded": "Token requests-per-minute limit reached",
    "token_tpm_exceeded": "Token tokens-per-minute limit reached",
    "token_rpd_exceeded": "Token daily request limit reached",
}


class TokenLimitFailure(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AccountLimitFailure(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def token_limit_message(reason: str, row: Optional[Dict[str, Any]]) -> str:
    base = _TOKEN_LIMIT_MESSAGES.get(reason.split(":")[0])
    if not base:
        return "Token rate interval not elapsed"
    limits = token_limiter.limits_for(row)
    if reason.startswith("token_concurrency_limit"):
        return f"{base} ({limits['max_concurrency']})"
    if reason.startswith("token_rpm_exceeded"):
        return f"{base} ({limits['rpm']}/min)"
    if reason.startswith("token_tpm_exceeded"):
        return f"{base} ({limits['tpm']} tok/min)"
    if reason.startswith("token_rpd_exceeded"):
        return f"{base} ({limits['rpd']}/day)"
    return base


async def enforce_token_limits(account: Dict[str, Any], estimated_tokens: int = 0):
    """Reserve a token slot and return an async release callback."""
    key_id = account.get("token_key_id")
    if not key_id:
        return None

    row = get_key_db(key_id)
    allowed, reason = await token_limiter.acquire(row, key_id, estimated_tokens=estimated_tokens)
    if not allowed:
        raise TokenLimitFailure(token_limit_message(reason, row))

    async def release(actual_tokens: int = 0) -> None:
        await token_limiter.release(row, key_id, actual_tokens=actual_tokens)

    return release


def _is_custom_request(account: Dict[str, Any], body: Dict[str, Any], model_alias: str) -> bool:
    requested_model = body.get("model", model_alias)
    for endpoint in _custom_endpoint_manager.get_endpoints_for_account(account):
        if endpoint.get("enabled", True) and requested_model in endpoint.get("enabled_models", []):
            return True

    for member in router.get_pool_custom_models(model_alias):
        endpoint = member["endpoint"]
        if not _custom_endpoint_manager.is_endpoint_frozen(endpoint.get("name", "")):
            return True
    return False


async def enforce_account_limit(account: Dict[str, Any], body: Dict[str, Any], is_opencode: bool = False) -> None:
    if account.get("name") in {"anonymous", "legacy-env-token"}:
        return

    model = body.get("model") or ""
    if "non-existent-model-to-trigger-error-directly" in model.lower():
        raise AccountLimitFailure("Account rate limit exceeded: simulated error for testing")

    model_alias = router.resolve_model_alias(model)
    pool_type = "lite" if model_alias and ("lite" in model_alias.lower() or "flash-lite" in model_alias.lower()) else "flash"
    if _is_custom_request(account, body, model_alias):
        pool_type = "custom"

    rpm, tpm, rpd = await get_effective_limits_by_pool(account, pool_type)
    effective = {**account, "rpm": rpm, "tpm": tpm, "rpd": rpd}
    max_tokens = int(body.get("max_tokens") or body.get("max_completion_tokens") or 4096)
    estimated = account_limiter.estimate_messages_tokens(body.get("messages") or [], max_tokens)
    estimated += sum(
        1600
        for message in body.get("messages") or []
        for item in (message.get("content") or [])
        if isinstance(message.get("content"), list)
        and isinstance(item, dict)
        and item.get("type") in ("image_url", "image")
    )
    allowed, reason = await account_limiter.acquire(effective, estimated, pool_type)
    if not allowed:
        raise AccountLimitFailure(f"Account rate limit exceeded: {reason}")



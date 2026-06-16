"""Admin system-settings endpoints."""
from fastapi import Request, HTTPException
from ..app_init import app
from ..auth_session import _require_admin
from .helpers import update_env_var


@app.get("/dashboard/admin/settings")
async def admin_get_settings(request: Request):
    _require_admin(request)
    from src.core.config_n_logg import config
    return {
        "ROUTER_API_MAX_RETRIES": config.MAX_RETRIES,
        "ROUTER_API_REQUEST_TIMEOUT_SEC": config.REQUEST_TIMEOUT_SECONDS,
        "POOL_SWAP_FAILURES": config.POOL_SWAP_FAILURES,
        "POOL_MAX_ATTEMPTS": config.POOL_MAX_ATTEMPTS,
        "COMPACTION_TOKEN_THRESHOLD": config.COMPACTION_TOKEN_THRESHOLD,
        "CLAUDE_CODE_COMPACTION_THRESHOLD": config.CLAUDE_CODE_COMPACTION_THRESHOLD,
        "COMPACTION_TARGET_LIMIT": config.COMPACTION_TARGET_LIMIT,
        "CLAUDE_CODE_COMPACTION_TARGET_LIMIT": config.CLAUDE_CODE_COMPACTION_TARGET_LIMIT,
        "EMERGENCY_MAX_INPUT_TOKENS": config.EMERGENCY_MAX_INPUT_TOKENS,
        "CLAUDE_CODE_EMERGENCY_MAX_INPUT_TOKENS": config.CLAUDE_CODE_EMERGENCY_MAX_INPUT_TOKENS,
        "CLIENT_DEFAULT_RPM": config.CLIENT_DEFAULT_RPM,
        "CLIENT_BURST_RPM": config.CLIENT_BURST_RPM,
    }


@app.post("/dashboard/admin/settings")
async def admin_update_settings(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Map settings keys from body
    keys_map = {
        "ROUTER_API_MAX_RETRIES": "MAX_RETRIES",
        "ROUTER_API_REQUEST_TIMEOUT_SEC": "REQUEST_TIMEOUT_SECONDS",
        "POOL_SWAP_FAILURES": "POOL_SWAP_FAILURES",
        "POOL_MAX_ATTEMPTS": "POOL_MAX_ATTEMPTS",
        "COMPACTION_TOKEN_THRESHOLD": "COMPACTION_TOKEN_THRESHOLD",
        "CLAUDE_CODE_COMPACTION_THRESHOLD": "CLAUDE_CODE_COMPACTION_THRESHOLD",
        "COMPACTION_TARGET_LIMIT": "COMPACTION_TARGET_LIMIT",
        "CLAUDE_CODE_COMPACTION_TARGET_LIMIT": "CLAUDE_CODE_COMPACTION_TARGET_LIMIT",
        "EMERGENCY_MAX_INPUT_TOKENS": "EMERGENCY_MAX_INPUT_TOKENS",
        "CLAUDE_CODE_EMERGENCY_MAX_INPUT_TOKENS": "CLAUDE_CODE_EMERGENCY_MAX_INPUT_TOKENS",
        "CLIENT_DEFAULT_RPM": "CLIENT_DEFAULT_RPM",
        "CLIENT_BURST_RPM": "CLIENT_BURST_RPM",
    }

    # Update the env vars
    for env_key in keys_map.keys():
        if env_key in body:
            val = str(body[env_key]).strip()
            if val:
                update_env_var(env_key, val)

    # Reload config
    from src.core.config_n_logg import reload_config
    reload_config()

    return {"status": "success"}

"""Account snapshot route."""

from typing import Any, Dict

from fastapi import Header

from src.core.limits import account_limiter
from src.server.openai_server.auth import _resolve_auth, _check_auth
from ..app_init import app


@app.get("/account")
async def current_account(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Dict[str, Any]:
    auth = _resolve_auth(authorization, x_api_key)
    account = _check_auth(auth)
    snapshot = await account_limiter.snapshot(account)
    return {
        "account": {
            "name": account.get("name"),
            "enabled": account.get("enabled", True),
            "rpm": account.get("rpm", 0),
            "tpm": account.get("tpm", 0),
            "rpd": account.get("rpd", 0),
        },
        "usage": snapshot,
    }


__all__ = ["current_account"]


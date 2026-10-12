"""Authentication dependency for member self-service routes."""

from typing import Any, Dict

from fastapi import HTTPException, Request


def get_account(request: Request) -> Dict[str, Any]:
    """Resolve a dashboard session or API key to the current account."""
    token = request.headers.get("X-Dashboard-Token", "").strip()
    if not token:
        auth = request.headers.get("Authorization", "").strip()
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth

    if token:
        from ..auth_session import _verify_session_token

        payload = _verify_session_token(token)
        if payload and payload.get("account_id"):
            return payload

        from ...auth import _check_auth

        try:
            account = _check_auth(token)
            if account and account.get("account_id"):
                return account
        except Exception:
            pass

    state_account = getattr(request.state, "auth_account", None)
    if state_account and state_account.get("account_id"):
        return state_account
    raise HTTPException(status_code=401, detail="Authentication required")


__all__ = ["get_account"]


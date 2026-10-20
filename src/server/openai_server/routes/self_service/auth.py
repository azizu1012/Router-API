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
        if payload and (payload.get("account_id") or payload.get("name")):
            from src.backend.accounts.manager import find_account_by_id, find_account_by_name

            account_id = payload.get("account_id")
            name = payload.get("name")
            acc = find_account_by_id(account_id) if account_id else None
            if not acc and name:
                candidate = find_account_by_name(name)
                if candidate and (not account_id or candidate.get("account_id") == account_id):
                    acc = candidate

            if not acc or not acc.get("enabled", 1):
                raise HTTPException(status_code=401, detail="Account is disabled or does not exist")

            if account_id and acc.get("account_id") and acc["account_id"] != account_id:
                raise HTTPException(status_code=401, detail="Account ID mismatch")

            payload["tier"] = acc.get("tier", "free")
            payload["name"] = acc.get("name", payload.get("name", ""))
            payload["rpm"] = acc.get("rpm", payload.get("rpm", 0))
            payload["tpm"] = acc.get("tpm", payload.get("tpm", 0))
            payload["rpd"] = acc.get("rpd", payload.get("rpd", 0))
            return payload

        from ...auth import _check_auth

        try:
            account = _check_auth(token)
            if account and account.get("account_id") and account.get("enabled", 1):
                return account
        except Exception:
            pass

    state_account = getattr(request.state, "auth_account", None)
    if state_account and state_account.get("account_id") and state_account.get("enabled", 1):
        return state_account
    raise HTTPException(status_code=401, detail="Authentication required")


__all__ = ["get_account"]


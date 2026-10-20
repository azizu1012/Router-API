import base64
import hashlib
import hmac as _hmac
import json
import secrets
import time
from typing import Any, Dict, Optional
from fastapi import Request, HTTPException

_SESSION_SECRET: str = secrets.token_hex(32)  # rotates on restart
_SESSION_TTL: int = 8 * 3600  # 8 hours

def _make_session_token(account: Dict[str, Any]) -> str:
    now = int(time.time())
    payload = json.dumps({
        "account_id": account.get("account_id"),
        "name": account.get("name"),
        "tier": account.get("tier", "free"),
        "rpm": account.get("rpm", 0),
        "tpm": account.get("tpm", 0),
        "rpd": account.get("rpd", 0),
        "iat": now,
        "exp": now + _SESSION_TTL,
    })
    b64 = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = _hmac.new(_SESSION_SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"

def _verify_session_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        last_dot = token.rfind(".")
        if last_dot < 0:
            return None
        b64, sig = token[:last_dot], token[last_dot + 1:]
        expected = _hmac.new(_SESSION_SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()
        if not secrets.compare_digest(sig, expected):
            return None
        padded = b64 + "=" * (-len(b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None

def _require_dashboard(request: Request) -> Dict[str, Any]:
    token = request.headers.get("X-Dashboard-Token", "")
    payload = _verify_session_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail={"error": "Invalid or expired session"})

    account_id = payload.get("account_id")
    name = payload.get("name")
    if not account_id and not name:
        raise HTTPException(status_code=401, detail={"error": "Invalid session payload"})

    from src.backend.accounts.manager import find_account_by_id, find_account_by_name
    from src.backend.keys.accounts import get_credential_db

    acc = find_account_by_id(account_id) if account_id else None
    if not acc and name:
        candidate = find_account_by_name(name)
        if candidate and (not account_id or candidate.get("account_id") == account_id):
            acc = candidate

    if not acc or not acc.get("enabled", 1):
        raise HTTPException(status_code=401, detail={"error": "Account is disabled or does not exist"})

    if account_id and acc.get("account_id") and acc["account_id"] != account_id:
        raise HTTPException(status_code=401, detail={"error": "Account ID mismatch"})

    # Synchronize tier, name, and limits with live DB state
    payload["tier"] = acc.get("tier", "free")
    payload["name"] = acc.get("name", payload.get("name", ""))
    payload["rpm"] = acc.get("rpm", payload.get("rpm", 0))
    payload["tpm"] = acc.get("tpm", payload.get("tpm", 0))
    payload["rpd"] = acc.get("rpd", payload.get("rpd", 0))

    return payload

def _require_admin(request: Request) -> Dict[str, Any]:
    payload = _require_dashboard(request)
    if payload.get("tier") != "admin":
        raise HTTPException(status_code=403, detail="Admin privilege required")
    return payload


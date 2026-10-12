"""Admin account token and key-management routes."""
from fastapi import Request, HTTPException

from ...app_init import app
from ...auth_session import _require_admin

from src.backend.keys.accounts import (
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MIN_INTERVAL_SECONDS,
    compose_token,
    create_key_db,
    delete_key_db,
    get_key_db,
    list_keys_db,
)
from src.core.accounts import account_manager


@app.post("/dashboard/admin/accounts/keys")
async def admin_account_keys(request: Request):
    """List one account's auth tokens (admin view)."""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        return {"keys": []}
    name = str(body.get("name", "")).strip()
    from src.backend.accounts.manager import find_account_by_name
    acc = find_account_by_name(name)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    return {
        "keys": [
            {
                "key_id": r["key_id"],
                "token": compose_token(r["name"], r["token_code"]),
                "label": r.get("label", ""),
                "enabled": bool(r["enabled"]),
                "tier": r["tier"],
                "rpm": r["rpm"], "tpm": r["tpm"], "rpd": r["rpd"],
                "max_concurrency": r["max_concurrency"],
                "min_interval_seconds": r["min_interval_seconds"],
                "created_at": r["created_at"],
            }
            for r in list_keys_db(acc["account_id"])
        ]
    }


@app.post("/dashboard/admin/accounts/keys/issue")
async def admin_issue_key(request: Request):
    """Issue an auth token for any account. Admin may raise concurrency ceilings."""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    name = str(body.get("name", "")).strip()
    from src.backend.accounts.manager import find_account_by_name
    acc = find_account_by_name(name)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")

    def _int(key: str, default: int, lo: int = 1, hi: int = 1000) -> int:
        try:
            val = int(body.get(key, default))
        except (TypeError, ValueError):
            val = default
        return max(lo, min(hi, val))

    try:
        interval = float(body.get("min_interval_seconds", DEFAULT_MIN_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        interval = DEFAULT_MIN_INTERVAL_SECONDS

    row = create_key_db(
        acc["account_id"],
        name=acc["name"],
        tier=str(body.get("tier") or acc.get("tier", "free")),
        rpm=_int("rpm", int(acc.get("rpm", 30))),
        tpm=_int("tpm", int(acc.get("tpm", 200000)), 1, 100_000_000),
        rpd=_int("rpd", int(acc.get("rpd", 1000))),
        max_concurrency=_int("max_concurrency", DEFAULT_MAX_CONCURRENCY, 1, 64),
        min_interval_seconds=max(0.0, interval),
        label=str(body.get("label", ""))[:64],
    )
    account_manager.invalidate_cache()
    return {
        "key_id": row["key_id"],
        "token": compose_token(row["name"], row["token_code"]),
        "max_concurrency": row["max_concurrency"],
        "rpm": row["rpm"],
        "tpm": row["tpm"],
        "rpd": row["rpd"],
        "min_interval_seconds": row["min_interval_seconds"],
        "interval_effective": row["max_concurrency"] == 1,
    }


@app.post("/dashboard/admin/accounts/keys/update")
async def admin_update_key(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    key_id = str(body.get("key_id", "")).strip()
    if not key_id or not get_key_db(key_id):
        raise HTTPException(status_code=404, detail="Key not found")

    updates = {}
    for field in ("tier", "label"):
        if field in body:
            updates[field] = body[field]
    for field in ("rpm", "tpm", "rpd", "max_concurrency"):
        if field in body:
            try:
                updates[field] = int(body[field])
            except (TypeError, ValueError):
                pass
    if "min_interval_seconds" in body:
        try:
            updates["min_interval_seconds"] = max(0.0, float(body["min_interval_seconds"]))
        except (TypeError, ValueError):
            pass
    if "enabled" in body:
        updates["enabled"] = 1 if body["enabled"] else 0

    row = account_manager.update_key(key_id, **updates)
    return {"key_id": key_id, "max_concurrency": (row or {}).get("max_concurrency")}


@app.post("/dashboard/admin/accounts/keys/revoke")
async def admin_revoke_key(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    key_id = str(body.get("key_id", "")).strip()
    if not key_id or not get_key_db(key_id):
        raise HTTPException(status_code=404, detail="Key not found")
    delete_key_db(key_id)
    account_manager.invalidate_cache()
    from src.core.limits import token_limiter
    token_limiter.reset(key_id)
    return {"ok": True, "key_id": key_id}



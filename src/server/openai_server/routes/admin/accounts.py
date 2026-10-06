"""Admin account-management endpoints."""
from fastapi import Request, HTTPException

from ..app_init import app
from ..auth_session import _require_admin, _require_dashboard

from src.backend.account_keys import (
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MIN_INTERVAL_SECONDS,
    compose_token,
    consume_invite_db,
    create_invite_db,
    create_key_db,
    delete_key_db,
    get_key_db,
    list_keys_db,
    set_password_db,
)
from src.core.accounts import account_manager


@app.post("/dashboard/admin/invites/issue")
async def admin_issue_invite(request: Request):
    """Mint a one-time enrollment code.

    Valid for 3 minutes and single-use. Issuing supersedes any live code, so a
    refresh always invalidates what the admin was previously shown.
    """
    actor = _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        ttl = int(body.get("ttl_seconds", 180))
    except (TypeError, ValueError):
        ttl = 180
    ttl = max(30, min(900, ttl))
    invite = create_invite_db(actor.get("name", "admin"), ttl_seconds=ttl)
    return {
        "code": invite["code"],
        "expires_at": invite["expires_at"],
        "ttl_seconds": invite["ttl_seconds"],
    }


@app.post("/dashboard/admin/accounts/keys")
async def admin_account_keys(request: Request):
    """List one account's auth tokens (admin view)."""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        return {"keys": []}
    name = str(body.get("name", "")).strip()
    from src.backend.accounts import find_account_by_name
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
    from src.backend.accounts import find_account_by_name
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


@app.post("/dashboard/register")
async def public_register(request: Request):
    """Self-service enrollment, gated on a one-time admin-issued code.

    A fresh account lands with a single auth token so the user can start calling
    immediately. The web password defaults to 1234 and the account is flagged
    must_change so the dashboard can prompt for it.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    code = str(body.get("invite_code", "")).strip()
    name = str(body.get("name", "")).strip()
    password = str(body.get("password", "")).strip() or "1234"

    if not code:
        raise HTTPException(status_code=400, detail="invite_code is required")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if len(name) < 2 or len(name) > 32:
        raise HTTPException(status_code=400, detail="name must be 2-32 characters")
    # Hyphens are fine (names like "azure-yena" already exist); only whitespace and
    # path separators are rejected, since the name is embedded in the auth token.
    if any(ch.isspace() or ch in "/\\" for ch in name):
        raise HTTPException(
            status_code=400, detail="name cannot contain spaces or slashes"
        )

    from src.backend.accounts import find_account_by_name
    if find_account_by_name(name):
        raise HTTPException(status_code=409, detail="Account already exists")

    invite = consume_invite_db(code)
    if not invite:
        raise HTTPException(
            status_code=403, detail="Invite code is invalid, expired, or already used"
        )

    acc = account_manager.create_account(
        name=name,
        tier="free",
        search_engine="auto",
        web_search_enabled=True,
    )
    set_password_db(acc["account_id"], password, must_change=(password == "1234"))
    key = create_key_db(
        acc["account_id"],
        name=acc["name"],
        tier=acc.get("tier", "free"),
        rpm=int(acc.get("rpm", 30)),
        tpm=int(acc.get("tpm", 200000)),
        rpd=int(acc.get("rpd", 1000)),
        max_concurrency=DEFAULT_MAX_CONCURRENCY,
        min_interval_seconds=DEFAULT_MIN_INTERVAL_SECONDS,
        label="initial",
    )
    account_manager.invalidate_cache()
    return {
        "ok": True,
        "name": acc["name"],
        "token": compose_token(key["name"], key["token_code"]),
        "max_concurrency": key["max_concurrency"],
        "min_interval_seconds": key["min_interval_seconds"],
        "must_change_password": password == "1234",
    }


@app.post("/dashboard/admin/accounts/create")
async def admin_create_account(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        rpm = body.get("rpm")
        tpm = body.get("tpm")
        rpd = body.get("rpd")
        tier = str(body.get("tier", "free")).strip().lower()
        password = str(body.get("password", "")).strip()
        search_engine = str(body.get("search_engine", "auto")).strip().lower()
        web_search_enabled = body.get("web_search_enabled")
        if web_search_enabled is None:
            web_search_enabled = False
        else:
            web_search_enabled = bool(web_search_enabled)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    try:
        rpm_val = int(rpm) if rpm is not None else None
        tpm_val = int(tpm) if tpm is not None else None
        rpd_val = int(rpd) if rpd is not None else None
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Limits must be numbers")

    if tier not in ("free", "premium", "admin"):
        tier = "free"
    if search_engine not in ("auto", "google_grounding", "duckduckgo", "disabled"):
        search_engine = "auto"

    # Clamp against the tier before anything is written. The tier is the ceiling;
    # an admin asking for 10M TPM on a free account gets the free ceiling, not
    # whatever the form let them type.
    from src.core.tier_limits import clamp_to_tier
    capped = clamp_to_tier(tier, {"rpm": rpm_val, "tpm": tpm_val, "rpd": rpd_val})
    rpm_val, tpm_val, rpd_val = capped["rpm"], capped["tpm"], capped["rpd"]

    # An account with no web credential cannot be listed on the dashboard, and
    # an admin who created it has no other way in. So a password is part of
    # creating an account, not an extra step afterwards.
    from src.backend.account_keys import DEFAULT_PASSWORD, set_password_db
    if not password:
        password = DEFAULT_PASSWORD

    import asyncio
    from src.core.accounts import account_manager
    try:
        acct = await asyncio.to_thread(
            account_manager.create_account,
            name=name, rpm=rpm_val, tpm=tpm_val, rpd=rpd_val, tier=tier, search_engine=search_engine, web_search_enabled=web_search_enabled
        )
        await asyncio.to_thread(set_password_db, acct["account_id"], password)
        return {
            "status": "success",
            "account": acct,
            "has_web_credential": True,
            "must_change_password": password == DEFAULT_PASSWORD,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/dashboard/admin/accounts/toggle")
async def admin_toggle_account(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        enabled = bool(body.get("enabled", True))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    import asyncio
    from src.core.accounts import account_manager
    try:
        acct = await asyncio.to_thread(account_manager.update_account, name, enabled=enabled)
        return {"status": "success", "account": acct}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/dashboard/admin/accounts/rotate-key")
async def admin_rotate_account_key(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    import asyncio
    from src.core.accounts import account_manager
    try:
        acct = await asyncio.to_thread(account_manager.rotate_key, name)
        return {"status": "success", "account": acct}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/dashboard/admin/accounts/delete")
async def admin_delete_account(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    import asyncio
    from src.core.accounts import account_manager
    try:
        await asyncio.to_thread(account_manager.delete_account, name)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/dashboard/admin/accounts/update")
async def admin_update_account(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        rpm = body.get("rpm")
        tpm = body.get("tpm")
        rpd = body.get("rpd")
        tier = body.get("tier")
        web_search_enabled = body.get("web_search_enabled")
        search_engine = body.get("search_engine")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    updates = {}
    if rpm is not None:
        try:
            updates["rpm"] = int(rpm)
        except (ValueError, TypeError):
            pass
    if tpm is not None:
        try:
            updates["tpm"] = int(tpm)
        except (ValueError, TypeError):
            pass
    if rpd is not None:
        try:
            updates["rpd"] = int(rpd)
        except (ValueError, TypeError):
            pass
    if tier is not None and tier in ("free", "premium", "admin"):
        updates["tier"] = tier
    if web_search_enabled is not None:
        updates["web_search_enabled"] = bool(web_search_enabled)
    if search_engine is not None and search_engine in ("auto", "google_grounding", "duckduckgo", "disabled"):
        updates["search_engine"] = search_engine

    # Clamp limits against the tier that will be in effect *after* this update.
    # A downgrade is the case that matters: dropping an account to free must
    # not leave it holding 6M TPM because the request only mentioned the tier.
    if any(k in updates for k in ("rpm", "tpm", "rpd")) or "tier" in updates:
        import asyncio as _asyncio
        from src.backend.accounts import find_account_by_name
        from src.core.tier_limits import clamp_to_tier, normalise_tier

        current = await _asyncio.to_thread(find_account_by_name, name)
        if current:
            effective = normalise_tier(updates.get("tier", current.get("tier")))
            merged = {
                "rpm": updates.get("rpm", current.get("rpm")),
                "tpm": updates.get("tpm", current.get("tpm")),
                "rpd": updates.get("rpd", current.get("rpd")),
            }
            capped = clamp_to_tier(effective, merged)
            for k, v in capped.items():
                if v is not None:
                    updates[k] = v

    import asyncio
    from src.core.accounts import account_manager
    try:
        acct = await asyncio.to_thread(account_manager.update_account, name, **updates)
        return {"status": "success", "account": acct}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/dashboard/admin/accounts/recovery")
async def admin_account_recovery(request: Request, name: str):
    """Read a user's password back, for the operator.

    Separate from the account listing on purpose: a password must never ride
    along with a table of accounts, because that table is what an operator
    glances at and what a screenshot captures. This has to be asked for by name.

    Admin-only, and it reports honestly when it cannot answer. A NULL means one
    of three things — the account predates the feature, no key is configured, or
    the row does not decrypt — and none of them are worth guessing at.
    """
    _require_admin(request)
    clean = str(name or "").strip()
    if not clean:
        raise HTTPException(status_code=400, detail="name is required")

    import asyncio
    from src.backend.accounts import find_account_by_name
    from src.backend.account_keys import get_recoverable_password_db
    from src.backend.password_recovery import is_enabled

    acct = await asyncio.to_thread(find_account_by_name, clean)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")

    if not is_enabled():
        return {
            "name": clean,
            "password": None,
            "available": False,
            "reason": "ROUTER_API_PASSWORD_KEY chưa được đặt — tính năng xem mật khẩu đang tắt",
        }

    plain = await asyncio.to_thread(get_recoverable_password_db, acct["account_id"])
    if plain is None:
        return {
            "name": clean,
            "password": None,
            "available": False,
            "reason": "Tài khoản này chưa có bản mật khẩu có thể đọc lại. "
                      "Đặt lại mật khẩu để tạo bản mới.",
        }
    return {"name": clean, "password": plain, "available": True}


@app.post("/dashboard/admin/accounts/search-engine")
async def admin_set_search_engine(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        search_engine = str(body.get("search_engine", "auto")).strip().lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if search_engine not in ("auto", "google_grounding", "duckduckgo", "disabled"):
        raise HTTPException(status_code=400, detail="search_engine must be auto/google_grounding/duckduckgo/disabled")

    import asyncio
    from src.core.accounts import account_manager
    try:
        acct = await asyncio.to_thread(account_manager.update_account, name, search_engine=search_engine)
        return {"status": "success", "search_engine": acct.get("search_engine", "auto")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/dashboard/my/search-engine")
async def my_search_engine(request: Request):
    payload = _require_dashboard(request)
    try:
        body = await request.json()
        search_engine = str(body.get("search_engine", "auto")).strip().lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if search_engine not in ("auto", "google_grounding", "duckduckgo", "disabled"):
        raise HTTPException(status_code=400, detail="search_engine must be auto/google_grounding/duckduckgo/disabled")

    import asyncio
    from src.core.accounts import account_manager
    try:
        acct = await asyncio.to_thread(account_manager.update_account, payload.get("name", ""), search_engine=search_engine)
        return {"status": "success", "search_engine": acct.get("search_engine", "auto")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

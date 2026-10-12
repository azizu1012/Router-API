"""Admin account lifecycle and public enrollment routes."""
from fastapi import Request, HTTPException

from ...app_init import app
from ...auth_session import _require_admin

from src.backend.keys.accounts import (
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MIN_INTERVAL_SECONDS,
    compose_token,
    consume_invite_db,
    create_invite_db,
    create_key_db,
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

    from src.backend.accounts.manager import find_account_by_name
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
        search_engine="duckduckgo",
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
        search_engine = str(body.get("search_engine", "duckduckgo")).strip().lower()
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
    if search_engine not in ("duckduckgo", "disabled"):
        search_engine = "duckduckgo"

    # Clamp against the tier before anything is written. The tier is the ceiling;
    # an admin asking for 10M TPM on a free account gets the free ceiling, not
    # whatever the form let them type.
    from src.core.policy.tier_limits import clamp_to_tier
    capped = clamp_to_tier(tier, {"rpm": rpm_val, "tpm": tpm_val, "rpd": rpd_val})
    rpm_val, tpm_val, rpd_val = capped["rpm"], capped["tpm"], capped["rpd"]

    # An account with no web credential cannot be listed on the dashboard, and
    # an admin who created it has no other way in. So a password is part of
    # creating an account, not an extra step afterwards.
    from src.backend.keys.accounts import DEFAULT_PASSWORD, set_password_db
    if not password:
        password = DEFAULT_PASSWORD

    import asyncio
    from src.core.accounts import account_manager
    try:
        acct = await asyncio.to_thread(
            account_manager.create_account,
            name=name, rpm=rpm_val, tpm=tpm_val, rpd=rpd_val, tier=tier, search_engine=search_engine, web_search_enabled=web_search_enabled
        )
        await asyncio.to_thread(
            set_password_db, acct["account_id"], password,
            must_change=1 if password == DEFAULT_PASSWORD else 0,
        )
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
    if search_engine is not None and search_engine in ("duckduckgo", "disabled"):
        updates["search_engine"] = search_engine

    # Clamp limits against the tier that will be in effect *after* this update.
    # A downgrade is the case that matters: dropping an account to free must
    # not leave it holding 6M TPM because the request only mentioned the tier.
    if any(k in updates for k in ("rpm", "tpm", "rpd")) or "tier" in updates:
        import asyncio as _asyncio
        from src.backend.accounts.manager import find_account_by_name
        from src.core.policy.tier_limits import clamp_to_tier, normalise_tier

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



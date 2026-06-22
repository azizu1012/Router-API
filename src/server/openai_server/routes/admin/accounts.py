"""Admin account-management endpoints."""
from fastapi import Request, HTTPException

from ..app_init import app
from ..auth_session import _require_admin, _require_dashboard


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
        search_engine = str(body.get("search_engine", "auto")).strip().lower()
        web_search_enabled = body.get("web_search_enabled")
        if web_search_enabled is None:
            web_search_enabled = True
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

    import asyncio
    from src.core.accounts import account_manager
    try:
        acct = await asyncio.to_thread(
            account_manager.create_account,
            name=name, rpm=rpm_val, tpm=tpm_val, rpd=rpd_val, tier=tier, search_engine=search_engine, web_search_enabled=web_search_enabled
        )
        return {"status": "success", "account": acct}
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

    import asyncio
    from src.core.accounts import account_manager
    try:
        acct = await asyncio.to_thread(account_manager.update_account, name, **updates)
        return {"status": "success", "account": acct}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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

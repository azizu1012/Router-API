import asyncio
import json
from typing import Any, Dict
from fastapi import Request, HTTPException

from src.core.config_n_logg import config
from src.core.router import router
from .app_init import app
from .auth_session import _require_admin

# ── Admin-only functions & endpoints ──

def _append_key_to_env(api_key: str) -> None:
    from src.core.config_n_logg import ENV_PATH
    if not ENV_PATH.exists():
        ENV_PATH.touch()
    content = ENV_PATH.read_text(encoding="utf-8")
    if api_key in content:
        return
    import uuid
    key_name = f"GEMINI_API_KEY_{uuid.uuid4().hex[:8].upper()}"
    new_line = f"\n{key_name}={api_key}\n"
    with open(ENV_PATH, "a", encoding="utf-8") as f:
        f.write(new_line)


def _remove_key_from_env(api_key: str) -> bool:
    from src.core.config_n_logg import ENV_PATH
    if not ENV_PATH.exists():
        return False
    content = ENV_PATH.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines = []
    found = False
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            parts = line.split("=", 1)
            val = parts[1].strip()
            if val == api_key:
                found = True
                continue
        new_lines.append(line)
    if found:
        ENV_PATH.write_text("\n".join(new_lines), encoding="utf-8")
    return found


def _normalize_key_name(key: str) -> str:
    from src.backend._db import _LOCK, conn as _conn
    with _LOCK:
        c = _conn()
        row = c.execute("SELECT key FROM key_status").fetchall()
        for r in row:
            db_k = r["key"]
            if db_k.lower().replace("\\", "/") == key.lower().replace("\\", "/"):
                c.close()
                return db_k
        c.close()
    return key


@app.post("/dashboard/admin/keys/add")
async def admin_add_key(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        api_key = str(body.get("api_key", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key is required")
        
    _append_key_to_env(api_key)
    
    from src.core.config_n_logg import reload_config
    new_keys = reload_config()
    from src.backend.key_status import register_keys_in_db
    register_keys_in_db(new_keys)
    router.refresh_keys()
    
    return {"status": "success"}


@app.post("/dashboard/admin/keys/delete")
async def admin_delete_key(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        target_key = str(body.get("key", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not target_key:
        raise HTTPException(status_code=400, detail="key is required")
        
    target_key = _normalize_key_name(target_key)
    success = _remove_key_from_env(target_key)
        
    if success:
        from src.core.config_n_logg import reload_config
        new_keys = reload_config()
        from src.backend.key_status import register_keys_in_db
        register_keys_in_db(new_keys)
        router.refresh_keys()
        return {"status": "success"}
    else:
        raise HTTPException(status_code=404, detail="Key not found or could not delete")


@app.post("/dashboard/admin/keys/pool")
async def admin_pool_key(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        target_key = str(body.get("key", "")).strip()
        pool_name = str(body.get("pool", "all")).strip().lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not target_key:
        raise HTTPException(status_code=400, detail="key is required")
        
    target_key = _normalize_key_name(target_key)
        
    from src.backend._db import _LOCK, conn as _conn
    with _LOCK:
        c = _conn()
        try:
            row = c.execute("SELECT data FROM key_status WHERE key = ?", (target_key,)).fetchone()
            current_data = {}
            if row and row["data"]:
                try:
                    current_data = json.loads(row["data"])
                except Exception:
                    pass
            if pool_name == "all":
                current_data.pop("allowed_pools", None)
            else:
                current_data["allowed_pools"] = [pool_name]
            c.execute("UPDATE key_status SET data = ? WHERE key = ?", (json.dumps(current_data), target_key))
            c.commit()
        except Exception as e:
            c.close()
            raise HTTPException(status_code=500, detail=f"Database save failed: {e}")
        c.close()
        
    router.refresh_keys()
    return {"status": "success"}


@app.post("/dashboard/admin/endpoints/add")
async def admin_add_endpoint(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        base_url = str(body.get("base_url", "")).strip()
        auth_key = str(body.get("auth_key", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name or not base_url or not auth_key:
        raise HTTPException(status_code=400, detail="name, base_url, and auth_key are required")
        
    from src.backend.endpoints import add_endpoint_db
    try:
        ep = add_endpoint_db(name, base_url, auth_key)
        from src.core.providers import _custom_endpoint_manager
        await _custom_endpoint_manager.fetch_models(ep["name"])
        return {"status": "success", "endpoint": ep}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/dashboard/admin/endpoints/delete")
async def admin_delete_endpoint(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
        
    from src.backend.endpoints import remove_endpoint_db
    ep = remove_endpoint_db(name)
    if ep:
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Endpoint not found")


@app.post("/dashboard/admin/endpoints/toggle")
async def admin_toggle_endpoint(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        action = str(body.get("action", "")).strip().lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name or not action:
        raise HTTPException(status_code=400, detail="name and action are required")
        
    from src.backend.endpoints import enable_endpoint_db, disable_endpoint_db, set_fallback_db
    if action == "enable":
        enable_endpoint_db(name)
    elif action == "disable":
        disable_endpoint_db(name)
    elif action == "fallback_on":
        set_fallback_db(name, True)
    elif action == "fallback_off":
        set_fallback_db(name, False)
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
    return {"status": "success"}


@app.post("/dashboard/admin/endpoints/pool")
async def admin_pool_endpoint(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        model_id = str(body.get("model_id", "")).strip()
        pool_name = str(body.get("pool_name", "")).strip().lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name or not model_id or not pool_name:
        raise HTTPException(status_code=400, detail="name, model_id, and pool_name are required")

    _pool_alias = {
        "flash": "gemini-flash", "gemini-flash": "gemini-flash",
        "lite": "gemini-flash-lite", "gemini-flash-lite": "gemini-flash-lite",
    }
    from src.backend.endpoints import assign_to_pool_db, remove_from_pool_db
    try:
        if pool_name == "remove" or pool_name == "none":
            remove_from_pool_db(name, model_id)
        elif pool_name in _pool_alias:
            assign_to_pool_db(name, model_id, _pool_alias[pool_name])
        elif pool_name == "both":
            assign_to_pool_db(name, model_id, "gemini-flash")
            assign_to_pool_db(name, model_id, "gemini-flash-lite")
        else:
            raise HTTPException(status_code=400, detail=f"Invalid pool name: {pool_name}")
        from src.core.providers import _custom_endpoint_manager
        _custom_endpoint_manager._invalidate_cache()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/dashboard/admin/endpoints/refresh")
async def admin_refresh_endpoint(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    from src.core.providers import _custom_endpoint_manager
    try:
        models = await _custom_endpoint_manager.fetch_models(name)
        return {"status": "success", "models": models, "count": len(models)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    
    try:
        rpm_val = int(rpm) if rpm is not None else None
        tpm_val = int(tpm) if tpm is not None else None
        rpd_val = int(rpd) if rpd is not None else None
    except Exception:
        raise HTTPException(status_code=400, detail="Limits must be numbers")
        
    if tier not in ("free", "premium", "admin"):
        tier = "free"
        
    from src.core.accounts import account_manager
    try:
        acct = await asyncio.to_thread(
            account_manager.create_account,
            name=name, rpm=rpm_val, tpm=tpm_val, rpd=rpd_val, tier=tier
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
        
    from src.core.accounts import account_manager
    try:
        await asyncio.to_thread(account_manager.delete_account, name)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/dashboard/my/web-search-toggle")
async def my_web_search_toggle(request: Request):
    from .auth_session import _require_dashboard
    payload = _require_dashboard(request)
    try:
        body = await request.json()
        enabled = bool(body.get("enabled", True))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    from src.core.accounts import account_manager
    try:
        acct = await asyncio.to_thread(account_manager.update_account, payload.get("name", ""), web_search_enabled=enabled)
        return {"status": "success", "web_search_enabled": bool(acct.get("web_search_enabled", 0))}
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
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
        
    updates = {}
    if rpm is not None:
        try: updates["rpm"] = int(rpm)
        except ValueError: pass
    if tpm is not None:
        try: updates["tpm"] = int(tpm)
        except ValueError: pass
    if rpd is not None:
        try: updates["rpd"] = int(rpd)
        except ValueError: pass
    if tier is not None:
        if tier in ("free", "premium", "admin"):
            updates["tier"] = tier
            
    from src.core.accounts import account_manager
    try:
        acct = await asyncio.to_thread(account_manager.update_account, name, **updates)
        return {"status": "success", "account": acct}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

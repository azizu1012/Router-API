"""Admin custom-endpoint management endpoints."""
from fastapi import Request, HTTPException

from ..app_init import app
from ..auth_session import _require_admin


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


@app.post("/dashboard/admin/endpoints/assign")
async def admin_assign_endpoint(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        account_id = str(body.get("account_id", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    from src.core.providers import _custom_endpoint_manager
    try:
        if account_id:
            from src.backend.accounts import find_account_by_name
            acct = find_account_by_name(account_id)
            if not acct:
                raise HTTPException(status_code=404, detail=f"Account '{account_id}' not found")
            _custom_endpoint_manager.assign_to_account(name, acct["account_id"])
        else:
            _custom_endpoint_manager.assign_to_account(name, "")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/dashboard/admin/endpoints/toggle-model")
async def admin_toggle_endpoint_model(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        model_id = str(body.get("model_id", "")).strip()
        enabled = bool(body.get("enabled", True))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name or not model_id:
        raise HTTPException(status_code=400, detail="name and model_id are required")

    from src.core.providers import _custom_endpoint_manager
    r = _custom_endpoint_manager.toggle_model(name, model_id, enabled)
    if not r:
        raise HTTPException(status_code=404, detail=f"Endpoint '{name}' not found")
    return {"status": "success", "endpoint": r}


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
    models = await _custom_endpoint_manager.fetch_models(name)
    return {"status": "success", "models": models, "count": len(models)}

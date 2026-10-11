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
        api_format = str(body.get("api_format") or "openai").strip().lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name or not base_url or not auth_key:
        raise HTTPException(status_code=400, detail="name, base_url, and auth_key are required")
    if api_format not in ("openai", "anthropic"):
        raise HTTPException(
            status_code=400,
            detail="api_format must be 'openai' or 'anthropic'",
        )

    from src.backend.endpoints import add_endpoint_db
    try:
        ep = add_endpoint_db(name, base_url, auth_key)
        if api_format != "openai":
            from src.backend.endpoints import update_endpoint_db
            from src.core.providers import _custom_endpoint_manager

            ep = update_endpoint_db(name, api_format=api_format) or ep
            _custom_endpoint_manager._invalidate_cache()
        from src.core.providers import _custom_endpoint_manager
        await _custom_endpoint_manager.fetch_models(ep["name"])
        return {"status": "success", "endpoint": ep}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/dashboard/admin/endpoints/format")
async def admin_set_endpoint_format(request: Request):
    """Change which wire format an endpoint speaks.

    The endpoint belongs to whoever paid for it, so this is the admin declaring
    the owner's dialect, not overriding it.
    """
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        api_format = str(body.get("api_format") or "").strip().lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if api_format not in ("openai", "anthropic"):
        raise HTTPException(
            status_code=400,
            detail="api_format must be 'openai' or 'anthropic'",
        )

    from src.backend.endpoints import update_endpoint_db
    from src.core.providers import _custom_endpoint_manager

    ep = update_endpoint_db(name, api_format=api_format)
    if not ep:
        raise HTTPException(status_code=404, detail=f"Endpoint '{name}' not found")
    _custom_endpoint_manager._invalidate_cache()
    return {"status": "success", "endpoint": ep}


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


@app.post("/dashboard/admin/endpoints/pool-assign")
async def admin_endpoint_pool_assign(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        pool_name = str(body.get("pool_name", "")).strip()
        model_id = str(body.get("model_id", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name or not pool_name:
        raise HTTPException(status_code=400, detail="name and pool_name are required")

    from src.core.providers import _custom_endpoint_manager
    if model_id:
        r = _custom_endpoint_manager.assign_pool_model(name, pool_name, model_id)
    else:
        r = _custom_endpoint_manager.remove_pool_model(name, pool_name)
    if not r:
        raise HTTPException(status_code=404, detail=f"Endpoint '{name}' not found")
    return {"status": "success", "endpoint": r}


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


@app.post("/dashboard/admin/endpoints/assign-to-key")
async def admin_assign_endpoint_to_key(request: Request):
    """Assign custom endpoint to a specific account key (key-level assignment).

    This is more granular than account-level assignment:
    - Account-level: All keys in account use this endpoint
    - Key-level: Only specific key uses this endpoint

    Body:
        {
            "endpoint_name": "my-endpoint",
            "key_id": "sk-abc123",  # Optional - empty to unassign
            "account_id": "admin"   # Owner of the key
        }
    """
    _require_admin(request)
    try:
        body = await request.json()
        endpoint_name = str(body.get("endpoint_name", "")).strip()
        key_id = str(body.get("key_id", "")).strip()
        account_id = str(body.get("account_id", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not endpoint_name:
        raise HTTPException(status_code=400, detail="endpoint_name is required")

    if not account_id:
        raise HTTPException(status_code=400, detail="account_id is required")

    from src.backend.endpoints import assign_endpoint_to_key_db
    from src.backend.accounts import find_account_by_name

    # Verify account exists
    acct = find_account_by_name(account_id)
    if not acct:
        raise HTTPException(status_code=404, detail=f"Account '{account_id}' not found")

    # Verify key exists and belongs to account
    if key_id:
        from src.backend.account_keys import get_keys_for_account
        keys = get_keys_for_account(acct["account_id"])
        if not any(k["key_id"] == key_id for k in keys):
            raise HTTPException(
                status_code=404,
                detail=f"Key '{key_id}' not found in account '{account_id}'"
            )

    try:
        ep = assign_endpoint_to_key_db(endpoint_name, key_id if key_id else None)
        if not ep:
            raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_name}' not found")

        from src.core.providers import _custom_endpoint_manager
        _custom_endpoint_manager._invalidate_cache()

        return {
            "status": "success",
            "endpoint": ep,
            "message": f"Endpoint '{endpoint_name}' assigned to key '{key_id}'" if key_id else f"Endpoint '{endpoint_name}' key assignment cleared"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


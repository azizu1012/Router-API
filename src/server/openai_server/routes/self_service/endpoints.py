"""Member-owned custom endpoint routes."""

from typing import Any, Dict
from fastapi import Depends, HTTPException

from src.backend.endpoints import manager as endpoints_db
from .router import router, logger
from .auth import get_account
from .endpoint_models import (
    CreateEndpointRequest, UpdateEndpointRequest, ToggleModelRequest,
)
# ============================================================


@router.get("/endpoints")
async def list_my_endpoints(account: Dict[str, Any] = Depends(get_account)):
    """List custom endpoints owned by current account."""
    account_id = account.get("account_id", "")
    if not account_id:
        raise HTTPException(status_code=401, detail="Account ID required")

    endpoints = endpoints_db.get_endpoints_by_account_db(account_id, include_disabled=True)
    res = [
        {
            "name": ep["name"],
            "base_url": ep["base_url"],
            "enabled": ep["enabled"],
            "api_format": ep.get("api_format", "openai"),
            "account_id": ep["account_id"],
            "models": ep.get("models", []),
            "enabled_models": ep.get("enabled_models", []),
            "created_at": ep.get("created_at"),
            "updated_at": ep.get("updated_at"),
        }
        for ep in endpoints
    ]
    return {"endpoints": res}


@router.post("/endpoints")
async def create_my_endpoint(
    req: CreateEndpointRequest,
    account: Dict[str, Any] = Depends(get_account),
):
    """Create a new custom endpoint owned by current account."""
    account_id = account.get("account_id", "")
    if not account_id:
        raise HTTPException(status_code=401, detail="Account ID required")

    try:
        ep = endpoints_db.add_member_endpoint_db(
            name=req.name,
            base_url=req.base_url,
            auth_key=req.auth_key,
            account_id=account_id,
            api_format=req.api_format,
            enabled=req.enabled,
        )
        logger.info(
            "[SelfService] Member %s created endpoint %s (format=%s)",
            account_id,
            ep["name"],
            ep.get("api_format", "openai"),
        )
        from src.core.providers import _custom_endpoint_manager

        _custom_endpoint_manager._invalidate_cache()
        try:
            await _custom_endpoint_manager.fetch_models(ep["name"])
            ep = endpoints_db.get_endpoint_db(ep["name"]) or ep
        except Exception as e:
            logger.warning("[SelfService] fetch_models for %s: %s", ep["name"], e)

        return {
            "name": ep["name"],
            "base_url": ep["base_url"],
            "enabled": ep["enabled"],
            "api_format": ep.get("api_format", "openai"),
            "account_id": ep["account_id"],
            "models": ep.get("models", []),
            "enabled_models": ep.get("enabled_models", []),
            "created_at": ep.get("created_at"),
            "updated_at": ep.get("updated_at"),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/endpoints/{name}")
async def update_my_endpoint(
    name: str,
    req: UpdateEndpointRequest,
    account: Dict[str, Any] = Depends(get_account),
):
    """Update custom endpoint (only if owned by current account)."""
    account_id = account.get("account_id", "")
    if not account_id:
        raise HTTPException(status_code=401, detail="Account ID required")

    ep = endpoints_db.get_endpoint_db(name)
    if not ep:
        raise HTTPException(status_code=404, detail=f"Endpoint '{name}' not found")

    if ep.get("account_id") and ep["account_id"] != account_id and account.get("tier") != "admin":
        raise HTTPException(status_code=403, detail="You can only update your own endpoints")

    updates = {}
    if req.base_url is not None:
        updates["base_url"] = req.base_url
    if req.auth_key is not None:
        updates["auth_key"] = req.auth_key
    if req.enabled is not None:
        updates["enabled"] = req.enabled
    if req.api_format is not None:
        updates["api_format"] = req.api_format

    ep = endpoints_db.update_endpoint_db(name, **updates)
    if not ep:
        raise HTTPException(status_code=500, detail="Failed to update endpoint")

    from src.core.providers import _custom_endpoint_manager

    _custom_endpoint_manager._invalidate_cache()
    logger.info("[SelfService] Member %s updated endpoint %s", account_id, name)
    return {
        "name": ep["name"],
        "base_url": ep["base_url"],
        "enabled": ep["enabled"],
        "api_format": ep.get("api_format", "openai"),
        "account_id": ep["account_id"],
        "models": ep.get("models", []),
        "enabled_models": ep.get("enabled_models", []),
        "created_at": ep.get("created_at"),
        "updated_at": ep.get("updated_at"),
    }


@router.delete("/endpoints/{name}")
async def delete_my_endpoint(
    name: str,
    account: Dict[str, Any] = Depends(get_account),
):
    """Delete custom endpoint (only if owned by current account)."""
    account_id = account.get("account_id", "")
    if not account_id:
        raise HTTPException(status_code=401, detail="Account ID required")

    ep = endpoints_db.get_endpoint_db(name)
    if not ep:
        raise HTTPException(status_code=404, detail=f"Endpoint '{name}' not found")

    if ep.get("account_id") and ep["account_id"] != account_id and account.get("tier") != "admin":
        raise HTTPException(status_code=403, detail="You can only delete your own endpoints")

    endpoints_db.remove_endpoint_db(name)
    from src.core.providers import _custom_endpoint_manager

    _custom_endpoint_manager._invalidate_cache()
    logger.info("[SelfService] Member %s deleted endpoint %s", account_id, name)
    return {"message": f"Endpoint '{name}' deleted"}


@router.post("/endpoints/{name}/refresh")
async def refresh_my_endpoint(
    name: str,
    account: Dict[str, Any] = Depends(get_account),
):
    """Fetch/refresh models from custom endpoint."""
    account_id = account.get("account_id", "")
    ep = endpoints_db.get_endpoint_db(name)
    if not ep:
        raise HTTPException(status_code=404, detail=f"Endpoint '{name}' not found")
    if ep.get("account_id") and ep["account_id"] != account_id and account.get("tier") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")

    from src.core.providers import _custom_endpoint_manager

    try:
        models = await _custom_endpoint_manager.fetch_models(name)
        ep = endpoints_db.get_endpoint_db(name) or ep
        return {"status": "success", "models": models, "count": len(models), "endpoint": ep}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/endpoints/{name}/toggle-model")
async def toggle_my_endpoint_model(
    name: str,
    req: ToggleModelRequest,
    account: Dict[str, Any] = Depends(get_account),
):
    """Toggle or manually add a model to custom endpoint."""
    account_id = account.get("account_id", "")
    ep = endpoints_db.get_endpoint_db(name)
    if not ep:
        raise HTTPException(status_code=404, detail=f"Endpoint '{name}' not found")
    if ep.get("account_id") and ep["account_id"] != account_id and account.get("tier") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")

    # If model not in all models list, add it
    all_models = list(ep.get("models", []))
    if req.model_id not in all_models:
        all_models.append(req.model_id)
        endpoints_db.update_endpoint_db(name, models=all_models)

    from src.core.providers import _custom_endpoint_manager

    r = _custom_endpoint_manager.toggle_model(name, req.model_id, req.enabled)
    return {"status": "success", "endpoint": r}



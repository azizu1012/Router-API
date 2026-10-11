"""Admin API routes for managing model aliases and custom endpoints."""

from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from src.backend import model_aliases as aliases_db
from src.core.config_n_logg.logger import logger_api as logger

from ..app_init import app
from ..auth_session import _require_admin


# ============================================================
# Request/Response Models
# ============================================================


class AdminCreateAliasRequest(BaseModel):
    alias_name: str = Field(..., description="Name client will call")
    target_model: str = Field(..., description="Actual model to route to")
    target_endpoint: Optional[str] = Field(None, description="Custom endpoint name (null = use pool)")
    account_id: Optional[str] = Field(None, description="Owner account ID (default: admin)")
    account_key_id: Optional[str] = Field(None, description="Key-specific alias (null = account-wide)")
    label: str = Field(default="", description="User description")
    enabled: bool = Field(default=True, description="Initial enabled state")


class AdminUpdateAliasRequest(BaseModel):
    enabled: Optional[bool] = Field(None, description="Force enable/disable")
    label: Optional[str] = Field(None, description="New label")
    target_model: Optional[str] = Field(None, description="New target model")
    target_endpoint: Optional[str] = Field(None, description="New target endpoint")


# ============================================================
# Admin Endpoints (both /dashboard/admin/aliases and /admin/aliases)
# ============================================================


@app.get("/dashboard/admin/aliases")
@app.get("/admin/aliases")
async def admin_list_aliases(
    request: Request,
    account_id: Optional[str] = None,
    include_disabled: bool = True,
):
    """List all aliases (optionally filtered by account). Admin only."""
    _require_admin(request)
    aliases = aliases_db.list_aliases_db(
        account_id=account_id,
        include_disabled=include_disabled,
    )
    return {"aliases": aliases}


@app.post("/dashboard/admin/aliases")
@app.post("/admin/aliases")
async def admin_create_alias(
    request: Request,
    req: AdminCreateAliasRequest,
):
    """Create alias for any account (admin only)."""
    admin = _require_admin(request)
    target_account = req.account_id or admin.get("account_id") or "admin"

    try:
        alias_id = aliases_db.add_alias_db(
            account_id=target_account,
            alias_name=req.alias_name,
            target_model=req.target_model,
            account_key_id=req.account_key_id,
            target_endpoint=req.target_endpoint,
            enabled=req.enabled,
            label=req.label,
        )
        logger.info(
            "[Admin] Created alias %s (%s → %s, target_ep=%s)",
            alias_id, req.alias_name, req.target_model, req.target_endpoint
        )
        alias = aliases_db.get_alias_by_id_db(alias_id)
        return {"status": "success", "alias": alias, "alias_id": alias_id}
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(
                status_code=400,
                detail=f"Alias '{req.alias_name}' already exists for this account/key",
            )
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dashboard/admin/aliases/{alias_id}")
@app.get("/admin/aliases/{alias_id}")
async def admin_get_alias(alias_id: str, request: Request):
    """Get alias by ID (admin only)."""
    _require_admin(request)
    alias = aliases_db.get_alias_by_id_db(alias_id)
    if not alias:
        raise HTTPException(status_code=404, detail=f"Alias '{alias_id}' not found")
    return alias


@app.patch("/dashboard/admin/aliases/{alias_id}")
@app.patch("/admin/aliases/{alias_id}")
async def admin_update_alias(alias_id: str, req: AdminUpdateAliasRequest, request: Request):
    """Update alias (admin can override any alias)."""
    _require_admin(request)
    alias = aliases_db.get_alias_by_id_db(alias_id)
    if not alias:
        raise HTTPException(status_code=404, detail=f"Alias '{alias_id}' not found")

    success = aliases_db.update_alias_db(
        alias_id=alias_id,
        enabled=req.enabled,
        label=req.label,
        target_model=req.target_model,
        target_endpoint=req.target_endpoint,
    )
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update alias")

    logger.info("[Admin] Force updated alias %s", alias_id)
    alias = aliases_db.get_alias_by_id_db(alias_id)
    return alias


@app.delete("/dashboard/admin/aliases/{alias_id}")
@app.delete("/admin/aliases/{alias_id}")
async def admin_delete_alias(alias_id: str, request: Request):
    """Delete alias (admin can delete any alias)."""
    _require_admin(request)
    alias = aliases_db.get_alias_by_id_db(alias_id)
    if not alias:
        raise HTTPException(status_code=404, detail=f"Alias '{alias_id}' not found")

    success = aliases_db.delete_alias_db(alias_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete alias")

    logger.info("[Admin] Deleted alias %s (%s → %s)", alias_id, alias["alias_name"], alias["target_model"])
    return {"message": f"Alias '{alias_id}' deleted"}


@app.get("/dashboard/admin/endpoints")
async def admin_list_all_endpoints(request: Request):
    """List all endpoints for admin."""
    _require_admin(request)
    from src.backend.endpoints import list_endpoints_db
    eps = list_endpoints_db()
    return {"endpoints": eps}

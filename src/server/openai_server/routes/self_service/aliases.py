"""Member-owned model alias routes and templates."""

from typing import Any, Dict
from fastapi import Depends, HTTPException

from src.backend.endpoints import manager as endpoints_db
from src.backend.models import aliases as aliases_db
from .router import router, logger
from .auth import get_account
from .alias_models import (
    CreateAliasRequest, UpdateAliasRequest,
)

@router.get("/aliases")
async def list_my_aliases(
    account: Dict[str, Any] = Depends(get_account),
    include_disabled: bool = False,
):
    """List model aliases owned by current account."""
    account_id = account.get("account_id", "")
    if not account_id:
        raise HTTPException(status_code=401, detail="Account ID required")

    aliases = aliases_db.list_aliases_db(
        account_id=account_id,
        account_key_id=None,
        include_disabled=include_disabled,
    )
    return {"aliases": aliases}


@router.post("/aliases")
async def create_my_alias(
    req: CreateAliasRequest,
    account: Dict[str, Any] = Depends(get_account),
):
    """Create a new model alias owned by current account."""
    account_id = account.get("account_id", "")
    if not account_id:
        raise HTTPException(status_code=401, detail="Account ID required")

    # Validate target_endpoint exists if specified
    if req.target_endpoint:
        ep = endpoints_db.get_endpoint_db(req.target_endpoint)
        if not ep:
            raise HTTPException(
                status_code=400,
                detail=f"Target endpoint '{req.target_endpoint}' not found",
            )
        # Check ownership
        if ep.get("account_id") and ep["account_id"] != account_id and account.get("tier") != "admin":
            raise HTTPException(
                status_code=403,
                detail="You can only create aliases pointing to your own endpoints",
            )

    try:
        alias_id = aliases_db.add_alias_db(
            account_id=account_id,
            alias_name=req.alias_name,
            target_model=req.target_model,
            account_key_id=req.account_key_id,
            target_endpoint=req.target_endpoint,
            enabled=req.enabled,
            label=req.label,
            targets=req.targets or "",
            strategy=req.strategy or "waterfall",
            context_length=req.context_length or 0,
        )
        logger.info(
            "[SelfService] Member %s created alias %s → %s (endpoint=%s)",
            account_id,
            req.alias_name,
            req.target_model,
            req.target_endpoint or "pool",
        )
        alias = aliases_db.get_alias_by_id_db(alias_id)
        if not alias:
            raise HTTPException(status_code=500, detail="Failed to retrieve created alias")
        return alias
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(
                status_code=400,
                detail=f"Alias '{req.alias_name}' already exists for this account/key",
            )
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/aliases/{alias_id}")
async def update_my_alias(
    alias_id: str,
    req: UpdateAliasRequest,
    account: Dict[str, Any] = Depends(get_account),
):
    """Update model alias (only if owned by current account)."""
    account_id = account.get("account_id", "")
    if not account_id:
        raise HTTPException(status_code=401, detail="Account ID required")

    alias = aliases_db.get_alias_by_id_db(alias_id)
    if not alias:
        raise HTTPException(status_code=404, detail=f"Alias '{alias_id}' not found")

    if alias["account_id"] != account_id and account.get("tier") != "admin":
        raise HTTPException(status_code=403, detail="You can only update your own aliases")

    # Validate target_endpoint if being updated
    if req.target_endpoint is not None and req.target_endpoint != "":
        ep = endpoints_db.get_endpoint_db(req.target_endpoint)
        if not ep:
            raise HTTPException(
                status_code=400,
                detail=f"Target endpoint '{req.target_endpoint}' not found",
            )
        if ep.get("account_id") and ep["account_id"] != account_id and account.get("tier") != "admin":
            raise HTTPException(
                status_code=403,
                detail="You can only point aliases to your own endpoints",
            )

    success = aliases_db.update_alias_db(
        alias_id=alias_id,
        enabled=req.enabled,
        label=req.label,
        target_model=req.target_model,
        target_endpoint=req.target_endpoint,
        targets=req.targets,
        strategy=req.strategy,
        context_length=req.context_length,
    )
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update alias")

    logger.info("[SelfService] Member %s updated alias %s", account_id, alias_id)
    alias = aliases_db.get_alias_by_id_db(alias_id)
    if not alias:
        raise HTTPException(status_code=500, detail="Failed to retrieve updated alias")
    return alias


@router.delete("/aliases/{alias_id}")
async def delete_my_alias(
    alias_id: str,
    account: Dict[str, Any] = Depends(get_account),
):
    """Delete model alias (only if owned by current account)."""
    account_id = account.get("account_id", "")
    if not account_id:
        raise HTTPException(status_code=401, detail="Account ID required")

    alias = aliases_db.get_alias_by_id_db(alias_id)
    if not alias:
        raise HTTPException(status_code=404, detail=f"Alias '{alias_id}' not found")

    if alias["account_id"] != account_id and account.get("tier") != "admin":
        raise HTTPException(status_code=403, detail="You can only delete your own aliases")

    success = aliases_db.delete_alias_db(alias_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete alias")

    logger.info("[SelfService] Member %s deleted alias %s", account_id, alias_id)
    return {"message": f"Alias '{alias_id}' deleted"}


# ============================================================
# Alias Templates
# ============================================================



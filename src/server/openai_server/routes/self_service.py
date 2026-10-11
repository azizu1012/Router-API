"""Self-service API routes for members to manage their own custom endpoints and model aliases."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.backend import endpoints as endpoints_db
from src.backend import model_aliases as aliases_db
from src.core.config_n_logg.logger import logger_api as logger

router = APIRouter(prefix="/api/me", tags=["self-service"])


def _get_account(request: Request) -> Dict[str, Any]:
    """Extract authenticated account from dashboard session or API key."""
    # 1. Check dashboard session token from X-Dashboard-Token header
    token = request.headers.get("X-Dashboard-Token", "").strip()
    if not token:
        # Check Authorization header (could be session token or Bearer sk-...)
        auth = request.headers.get("Authorization", "").strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        elif auth:
            token = auth

    if token:
        from .auth_session import _verify_session_token

        payload = _verify_session_token(token)
        if payload and payload.get("account_id"):
            return payload

        # Check API key if not session token
        from ..auth import _check_auth

        try:
            acc = _check_auth(token)
            if acc and acc.get("account_id"):
                return acc
        except Exception:
            pass

    # Also check if request.state has auth_account from token_limit_middleware
    state_acc = getattr(request.state, "auth_account", None)
    if state_acc and state_acc.get("account_id"):
        return state_acc

    raise HTTPException(status_code=401, detail="Authentication required")


# ============================================================
# Request/Response Models
# ============================================================


class CreateEndpointRequest(BaseModel):
    name: str = Field(..., description="Endpoint name (will be sanitized)")
    base_url: str = Field(..., description="API base URL")
    auth_key: str = Field(..., description="API authentication key")
    api_format: str = Field(default="openai", description="API format (openai, anthropic, etc.)")
    enabled: bool = Field(default=True, description="Initial enabled state")


class UpdateEndpointRequest(BaseModel):
    base_url: Optional[str] = Field(None, description="New base URL")
    auth_key: Optional[str] = Field(None, description="New auth key")
    enabled: Optional[bool] = Field(None, description="New enabled state")
    api_format: Optional[str] = Field(None, description="New API format")


class ToggleModelRequest(BaseModel):
    model_id: str = Field(..., description="Model ID to toggle or add")
    enabled: bool = Field(default=True, description="Enable or disable this model")


class CreateAliasRequest(BaseModel):
    alias_name: str = Field(..., description="Name client will call (e.g., 'claude-3-5-sonnet')")
    target_model: str = Field(..., description="Actual model to route to (e.g., 'gemini-flash')")
    target_endpoint: Optional[str] = Field(None, description="Custom endpoint name (null = use pool)")
    account_key_id: Optional[str] = Field(None, description="Key-specific alias (null = account-wide)")
    label: str = Field(default="", description="User description")
    enabled: bool = Field(default=True, description="Initial enabled state")


class UpdateAliasRequest(BaseModel):
    enabled: Optional[bool] = Field(None, description="New enabled state")
    label: Optional[str] = Field(None, description="New label")
    target_model: Optional[str] = Field(None, description="New target model")
    target_endpoint: Optional[str] = Field(None, description="New target endpoint")


class AliasResponse(BaseModel):
    alias_id: str
    account_id: str
    account_key_id: Optional[str]
    alias_name: str
    target_model: str
    target_endpoint: Optional[str]
    enabled: bool
    label: str
    created_at: int
    updated_at: int


class EndpointResponse(BaseModel):
    name: str
    base_url: str
    enabled: bool
    api_format: str
    account_id: str
    models: List[str]
    enabled_models: Optional[List[str]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AliasTemplateResponse(BaseModel):
    name: str
    alias_name: str
    target_model: str
    target_endpoint: Optional[str] = None
    description: str
    category: Optional[str] = "General"


# ============================================================
# Custom Endpoints Management (Member Self-Service)
# ============================================================


@router.get("/endpoints")
async def list_my_endpoints(account: Dict[str, Any] = Depends(_get_account)):
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
    account: Dict[str, Any] = Depends(_get_account),
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
    account: Dict[str, Any] = Depends(_get_account),
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
    account: Dict[str, Any] = Depends(_get_account),
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
    account: Dict[str, Any] = Depends(_get_account),
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
    account: Dict[str, Any] = Depends(_get_account),
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


# ============================================================
# Model Aliases Management
# ============================================================


@router.get("/aliases")
async def list_my_aliases(
    account: Dict[str, Any] = Depends(_get_account),
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
    account: Dict[str, Any] = Depends(_get_account),
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
    account: Dict[str, Any] = Depends(_get_account),
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
    account: Dict[str, Any] = Depends(_get_account),
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


@router.get("/alias-templates", response_model=List[AliasTemplateResponse])
async def get_alias_templates():
    """Get suggested alias templates with latest Claude and GPT models.

    Includes latest official models (Claude 3.7 Sonnet, GPT-4.5 Preview, o3-mini, o1)
    as well as next-gen / relay models (Sonnet 5.5, Opus 5.5, Haiku 5.5).
    """
    return [
        # --- CLAUDE (Anthropic) ---
        {
            "name": "Claude 3.7 Sonnet (Hybrid Reasoning)",
            "alias_name": "claude-3-7-sonnet-20250219",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "description": "Flash = Sonnet 3.7: Model mới nhất của Anthropic, chạy Gemini Flash",
            "category": "Claude",
        },
        {
            "name": "Claude-style Gemini Flash (Sonnet 3.5)",
            "alias_name": "claude-3-5-sonnet-20241022",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "description": "Flash = Sonnet: Model coding chuẩn của Anthropic, chạy Gemini Flash",
            "category": "Claude",
        },
        {
            "name": "Claude Sonnet 5.5 (Next-Gen Relay)",
            "alias_name": "claude-sonnet-5-5",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "description": "Sonnet 5.5: Tên model đời cao trên các relay/proxy, chạy Gemini Flash",
            "category": "Claude",
        },
        {
            "name": "Claude Sonnet 5 (Next-Gen)",
            "alias_name": "claude-sonnet-5",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "description": "Sonnet 5: Chạy Gemini Flash cho client yêu cầu sonnet-5",
            "category": "Claude",
        },
        {
            "name": "Claude-style Gemini Lite (Haiku 3.5)",
            "alias_name": "claude-3-5-haiku-20241022",
            "target_model": "gemini-flash-lite",
            "target_endpoint": None,
            "description": "Lite = Haiku: Tốc độ cao, sub-agent rẻ, chạy Gemini Flash Lite",
            "category": "Claude",
        },
        {
            "name": "Claude Haiku 5.5 (Next-Gen Fast)",
            "alias_name": "claude-haiku-5-5",
            "target_model": "gemini-flash-lite",
            "target_endpoint": None,
            "description": "Haiku 5.5: Model tốc độ cao đời mới, chạy Gemini Flash Lite",
            "category": "Claude",
        },
        {
            "name": "Claude Opus 5.5 (Deep Reasoning)",
            "alias_name": "claude-opus-5-5",
            "target_model": "gemini-pro",
            "target_endpoint": None,
            "description": "Opus 5.5: Dành cho client đòi model Opus đời cao, chạy Gemini Pro",
            "category": "Claude",
        },
        {
            "name": "Claude 3 Opus (Classic Flagship)",
            "alias_name": "claude-3-opus-20240229",
            "target_model": "gemini-pro",
            "target_endpoint": None,
            "description": "Claude 3 Opus chính thức, chạy Gemini Pro",
            "category": "Claude",
        },

        # --- OPENAI (GPT & Reasoning) ---
        {
            "name": "GPT-4.5 Preview (Orion Flagship)",
            "alias_name": "gpt-4.5-preview",
            "target_model": "gemini-pro",
            "target_endpoint": None,
            "description": "GPT-4.5 Orion: Model lớn nhất mới nhất của OpenAI, chạy Gemini Pro",
            "category": "OpenAI",
        },
        {
            "name": "OpenAI o3-mini (Reasoning Coder)",
            "alias_name": "o3-mini",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "description": "o3-mini: Model suy luận lập trình mới của OpenAI, chạy Gemini Flash",
            "category": "OpenAI",
        },
        {
            "name": "OpenAI o1 (Full Reasoning)",
            "alias_name": "o1",
            "target_model": "gemini-pro",
            "target_endpoint": None,
            "description": "o1: Model suy luận sâu của OpenAI, chạy Gemini Pro",
            "category": "OpenAI",
        },
        {
            "name": "GPT-4o (Omni Flagship)",
            "alias_name": "gpt-4o",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "description": "gpt-4o: Model chuẩn của OpenAI SDK, chạy Gemini Flash",
            "category": "OpenAI",
        },
        {
            "name": "GPT-4o Mini (Siêu nhẹ)",
            "alias_name": "gpt-4o-mini",
            "target_model": "gemini-flash-lite",
            "target_endpoint": None,
            "description": "gpt-4o-mini: Tác vụ phụ & sub-agent nhanh, chạy Gemini Flash Lite",
            "category": "OpenAI",
        },
        {
            "name": "GPT-5 (Speculative Next-Gen)",
            "alias_name": "gpt-5",
            "target_model": "gemini-pro",
            "target_endpoint": None,
            "description": "Bypass các client/extension đòi hỏi tên model GPT-5",
            "category": "OpenAI",
        },

        # --- CUSTOM ENDPOINTS ---
        {
            "name": "Custom Endpoint: GPT-4o → Claude Sonnet",
            "alias_name": "gpt-4o",
            "target_model": "claude-sonnet-5-5",
            "target_endpoint": "my-endpoint",
            "description": "OpenAI client gọi gpt-4o nhưng chạy Claude trên Custom Endpoint",
            "category": "Custom Endpoint",
        },
        {
            "name": "Custom Endpoint: Claude 3.7 → Custom DeepSeek",
            "alias_name": "claude-3-7-sonnet-20250219",
            "target_model": "deepseek-v4.1-flash",
            "target_endpoint": "my-endpoint",
            "description": "Claude Code gọi Sonnet 3.7 nhưng chạy DeepSeek trên Custom Endpoint",
            "category": "Custom Endpoint",
        },
    ]

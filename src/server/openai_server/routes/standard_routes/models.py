"""OpenAI and Anthropic compatible model discovery routes."""

from typing import Any, Dict

from fastapi import Header, HTTPException

from src.core.config_n_logg import config
from src.core.router import router
from src.server.openai_server.auth import _resolve_auth, _check_auth
from ..app_init import app


_ANTHROPIC_EPOCH = "2025-01-01T00:00:00Z"


def _model_entry(m: Dict[str, Any]) -> Dict[str, Any]:
    model_id = m.get("id")
    entry = {
        "id": model_id,
        "object": "model",
        "type": "model",
        "created": 0,
        "created_at": _ANTHROPIC_EPOCH,
        "owned_by": m.get("owned_by", "router_api"),
        "display_name": m.get("display") or model_id,
        "display": m.get("display") or model_id,
        "root": m.get("root"),
        "context_length": m.get("context_length"),
        "lifecycle": "active",
    }
    cl = m.get("context_length")
    if cl:
        entry["max_input_tokens"] = cl
    entry["max_tokens"] = config.MAX_OUTPUT_TOKENS
    return entry


def _models_payload(account: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Build pool, alias, and account-owned endpoint model entries."""
    entries = [_model_entry(m) for m in router.list_models()]

    if account:
        account_id = account.get("account_id")
        from src.backend.models import aliases as model_aliases
        aliases = model_aliases.list_aliases_db(account_id=account_id, enabled_only=True)

        aliased_models = {
            (alias["target_model"], alias["target_endpoint"])
            for alias in aliases if alias.get("target_endpoint")
        }

        from src.logical_HQ_translator.model.alias import get_alias_context_length
        for alias in aliases:
            safe_ctx = get_alias_context_length(alias)
            alias_entry = {
                "id": alias["alias_name"],
                "object": "model",
                "type": "model",
                "created": alias.get("created_at", 0),
                "created_at": _ANTHROPIC_EPOCH,
                "owned_by": "alias",
                "display_name": alias["alias_name"],
                "display": alias["alias_name"],
                "lifecycle": "active",
                "max_tokens": config.MAX_OUTPUT_TOKENS,
                "context_length": safe_ctx,
                "max_input_tokens": safe_ctx,
                "_alias_target": alias["target_model"],
                "_alias_endpoint": alias.get("target_endpoint"),
            }
            if not any(e["id"] == alias["alias_name"] for e in entries):
                entries.append(alias_entry)

        from src.backend.endpoints import manager as endpoints
        user_endpoints = [
            ep for ep in endpoints.list_endpoints_db()
            if ep.get("account_id") == account_id and ep.get("enabled")
        ]
        for ep in user_endpoints:
            ep_models = ep.get("enabled_models") or ep.get("models") or []
            for model in ep_models:
                if (model, ep["name"]) in aliased_models:
                    continue
                if not any(e["id"] == model for e in entries):
                    entries.append({
                        "id": model,
                        "object": "model",
                        "type": "model",
                        "created": 0,
                        "created_at": _ANTHROPIC_EPOCH,
                        "owned_by": f"custom_endpoint:{ep['name']}",
                        "display_name": model,
                        "display": model,
                        "lifecycle": "active",
                        "max_tokens": config.MAX_OUTPUT_TOKENS,
                        "_custom_endpoint": ep["name"],
                    })

    return {
        "object": "list",
        "data": entries,
        "has_more": False,
        "first_id": entries[0]["id"] if entries else None,
        "last_id": entries[-1]["id"] if entries else None,
    }


@app.get("/v1")
@app.get("/v1/")
async def api_root(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Dict[str, Any]:
    auth = _resolve_auth(authorization, x_api_key)
    account = _check_auth(auth)
    return _models_payload(account)


@app.get("/v1/models")
async def list_models(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Dict[str, Any]:
    auth = _resolve_auth(authorization, x_api_key)
    account = _check_auth(auth)
    return _models_payload(account)


@app.get("/v1/models/{model_id:path}")
async def retrieve_model(
    model_id: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Dict[str, Any]:
    auth = _resolve_auth(authorization, x_api_key)
    _check_auth(auth)
    for m in router.list_models():
        if m["id"] == model_id or m.get("root") == model_id:
            return _model_entry(m)
    raise HTTPException(
        status_code=404,
        detail={"error": {"message": "Model not found", "type": "invalid_request_error"}},
    )


__all__ = ["_model_entry", "_models_payload", "api_root", "list_models", "retrieve_model"]


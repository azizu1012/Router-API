"""Gemini Decisions API routes (/v1/decisions, /decisions)."""

import asyncio
import json

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse

from src.core.config_n_logg.logger import logger_api
from src.core.observability.usage_logger import log_usage
from src.core.providers import api_manager
from src.core.providers.gemini import GeminiBadRequestError
from src.core.router import router
from src.server.openai_server.auth import (
    _apply_account_limit,
    _auth_key_prefix,
    _check_auth,
    _resolve_auth,
)
from ..app_init import app


@app.post("/v1/decisions")
@app.post("/decisions")
async def create_decision(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
):
    account = getattr(request.state, "auth_account", None)
    if account is None:
        auth = _resolve_auth(authorization, x_api_key)
        account = _check_auth(auth)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "Invalid JSON request body", "type": "invalid_request_error"}},
        )
    if not isinstance(body, dict) or not body:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "Request body must be a non-empty JSON object", "type": "invalid_request_error"}},
        )

    raw_model = str(body.get("model") or "gemini-decision-1").strip() or "gemini-decision-1"
    requested_model = raw_model[7:] if raw_model.startswith("models/") else raw_model
    try:
        from src.logical_HQ_translator.model.alias import resolve_model_alias
        account_id = str(account.get("account_id") or "")
        account_key_id = account.get("token_key_id") or account.get("account_key_id")
        resolved_model, _ = resolve_model_alias(requested_model, account_id, account_key_id)
    except Exception:
        resolved_model = requested_model

    model_alias = router.resolve_model_alias(resolved_model)
    if not model_alias:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": (
                        f"Model '{resolved_model or ''}' not found. Add a model alias "
                        "for it or enable a custom endpoint that serves it."
                    ),
                    "type": "invalid_request_error",
                }
            },
        )
    serialized = json.dumps(body, ensure_ascii=False)
    estimated_tokens = max(1, len(serialized) // 4)

    await _apply_account_limit(
        account,
        {
            "model": model_alias,
            "messages": [{"role": "user", "content": serialized}],
            "max_tokens": 0,
        },
    )

    api_version = str(body.get("api_version") or body.get("apiVersion") or "v1beta")
    upstream_payload = {
        k: v for k, v in body.items()
        if k not in ("api_version", "apiVersion")
    }

    try:
        gresult = await api_manager.call_decisions(
            model_alias=model_alias,
            payload=upstream_payload,
            api_version=api_version,
            account=account,
            estimated_tokens=estimated_tokens,
        )
    except GeminiBadRequestError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": str(exc), "type": "invalid_request_error"}},
        )
    except HTTPException:
        raise
    except (TimeoutError, asyncio.TimeoutError):
        return JSONResponse(
            status_code=504,
            content={"error": {"message": "Upstream decision request timed out", "type": "timeout_error"}},
        )
    except Exception as exc:
        msg = str(exc) or exc.__class__.__name__
        if "quota_exhausted" in msg or "no_available_key" in msg or "no keys available" in msg:
            return JSONResponse(
                status_code=429,
                content={"error": {"message": "All API keys are temporarily unavailable, retry later", "type": "rate_limit_error"}},
            )
        logger_api.error("[Decisions] %s failed: %s", model_alias, msg, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": {"message": msg, "type": "api_error"}},
        )

    input_tokens = int(gresult.get("input_tokens") or estimated_tokens)
    output_tokens = int(gresult.get("output_tokens") or 0)
    actual_model_id = gresult.get("model_id") or gresult.get("model_alias") or model_alias
    auth_key_prefix = _auth_key_prefix(account)

    await log_usage(
        actual_model_id,
        (gresult.get("api_key") or "")[-8:],
        input_tokens,
        output_tokens,
        auth_key_prefix,
        0,
        0,
    )

    return JSONResponse(content=gresult.get("response") or {})


__all__ = ["create_decision"]

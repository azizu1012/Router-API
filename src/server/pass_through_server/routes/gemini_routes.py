from fastapi import Header, Request, HTTPException
from fastapi.responses import JSONResponse

from src.server.openai_server.routes.app_init import app
from src.server.openai_server.auth import _check_auth
from .gemini_handlers import _handle_gemini_native
from .gemini_parsers import resolve_gemini_auth


@app.post("/v1beta/models/{model_id:path}:generateContent")
@app.post("/v1alpha/models/{model_id:path}:generateContent")
@app.post("/v1/models/{model_id:path}:generateContent")
async def generate_content(
    model_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_goog_api_key: str | None = Header(default=None),
):
    return await _handle_gemini_native(model_id, request, authorization, x_api_key, x_goog_api_key, stream=False)


@app.post("/v1beta/models/{model_id:path}:streamGenerateContent")
@app.post("/v1alpha/models/{model_id:path}:streamGenerateContent")
@app.post("/v1/models/{model_id:path}:streamGenerateContent")
async def stream_generate_content(
    model_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_goog_api_key: str | None = Header(default=None),
):
    return await _handle_gemini_native(model_id, request, authorization, x_api_key, x_goog_api_key, stream=True)


@app.get("/v1beta/models")
@app.get("/v1alpha/models")
async def list_gemini_models(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_goog_api_key: str | None = Header(default=None),
):
    auth_val = resolve_gemini_auth(request, authorization, x_api_key, x_goog_api_key)
    _check_auth(auth_val)

    from src.core.api_config import AVAILABLE_MODELS
    gemini_models = []
    for alias, cfg in AVAILABLE_MODELS.items():
        if cfg.get("hidden"):
            continue
        gemini_models.append({
            "name": f"models/{alias}",
            "version": "stable",
            "displayName": cfg.get("display", alias),
            "description": f"Router API model alias for {cfg.get('model_id')}",
            "inputTokenLimit": cfg.get("context_length", 220000),
            "outputTokenLimit": 8192,
            "supportedGenerationMethods": ["generateContent", "countTokens"]
        })
    return {"models": gemini_models}


@app.get("/v1beta/models/{model_id:path}")
@app.get("/v1alpha/models/{model_id:path}")
async def get_gemini_model(
    model_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_goog_api_key: str | None = Header(default=None),
):
    auth_val = resolve_gemini_auth(request, authorization, x_api_key, x_goog_api_key)
    _check_auth(auth_val)

    alias = model_id.split("/")[-1]
    from src.core.api_config import AVAILABLE_MODELS
    from src.core.router import router

    model_alias_resolved = router.resolve_model_alias(alias)
    cfg = AVAILABLE_MODELS.get(model_alias_resolved)
    if not cfg:
        # Fallback: check if the alias matches backing model_id
        for a, c in AVAILABLE_MODELS.items():
            if c.get("model_id") == alias:
                cfg = c
                alias = a
                break

    if not cfg:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": 404,
                    "message": f"Model models/{alias} not found.",
                    "status": "NOT_FOUND"
                }
            }
        )

    return {
        "name": f"models/{alias}",
        "version": "stable",
        "displayName": cfg.get("display", alias),
        "description": f"Router API model alias for {cfg.get('model_id')}",
        "inputTokenLimit": cfg.get("context_length", 220000),
        "outputTokenLimit": 8192,
        "supportedGenerationMethods": ["generateContent", "countTokens"]
    }

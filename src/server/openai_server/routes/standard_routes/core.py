"""Root, health, preflight, and connection-warmup routes."""

from typing import Any, Dict

from fastapi import Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.core.config_n_logg import config
from src.core.router import router
from src.server.openai_server.auth import _resolve_auth, _check_auth
from ..app_init import app


@app.api_route("/", methods=["GET", "HEAD", "OPTIONS"])
async def root(request: Request):
    headers = {
        "anthropic-version": "2023-06-01",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }
    if request.method == "HEAD":
        return StreamingResponse(iter([]), headers=headers)
    return JSONResponse(
        content={"status": "ok", "message": "Router API v2 is running", "compat": "anthropic,openai"},
        headers=headers,
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "models": [m["id"] for m in router.list_models()],
        "keys": len(config.GEMINI_API_KEYS),
    }


@app.get("/preflight")
async def preflight(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Dict[str, Any]:
    from src.core.ops.preflight import run_preflight
    auth = _resolve_auth(authorization, x_api_key)
    _check_auth(auth)
    return run_preflight()


@app.api_route("/api/hello", methods=["GET", "HEAD"])
@app.api_route("/v1/api/hello", methods=["GET", "HEAD"])
async def connection_warmup() -> Dict[str, Any]:
    """Claude Code's connection-warming probe."""
    return {"ok": True}


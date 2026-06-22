from pathlib import Path
from typing import Any, Dict
from fastapi import Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_web
from src.core.router import router
from src.core.limits import account_limiter
from src.server.openai_server.auth import _resolve_auth, _check_auth
from .app_init import app

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frontend"

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
    from src.core.preflight import run_preflight
    auth = _resolve_auth(authorization, x_api_key)
    _check_auth(auth)
    return run_preflight()


@app.get("/mcp")
async def mcp_discovery():
    return {
        "servers": [],
        "notifications": False,
        "roots": [],
    }


@app.get("/v1/models")
async def list_models(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Dict[str, Any]:
    auth = _resolve_auth(authorization, x_api_key)
    _check_auth(auth)
    return {"object": "list", "data": router.list_models()}


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
            return m
    raise HTTPException(
        status_code=404,
        detail={"error": {"message": "Model not found", "type": "invalid_request_error"}},
    )


@app.get("/account")
async def current_account(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Dict[str, Any]:
    auth = _resolve_auth(authorization, x_api_key)
    account = _check_auth(auth)
    snapshot = await account_limiter.snapshot(account)
    return {
        "account": {
            "name": account.get("name"),
            "enabled": account.get("enabled", True),
            "rpm": account.get("rpm", 0),
            "tpm": account.get("tpm", 0),
            "rpd": account.get("rpd", 0),
        },
        "usage": snapshot,
    }


@app.post("/v1/search")
@app.post("/search")
async def web_search_endpoint(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Dict[str, Any]:
    auth = _resolve_auth(authorization, x_api_key)
    account = _check_auth(auth)

    try:
        body = await request.json()
    except Exception:
        body = {}

    query = body.get("query")
    if not query or not isinstance(query, str) or not query.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "`query` parameter is required and must be a non-empty string", "type": "invalid_request_error"}},
        )

    search_engine = body.get("search_engine") or "auto"

    from src.server.openai_server.auth import _auth_key_prefix
    akp = _auth_key_prefix(account)

    from src.core.providers.search_manager import execute_hybrid_search
    try:
        search_context, combined_citations = await execute_hybrid_search(
            [query.strip()],
            search_engine=search_engine,
            auth_key_prefix=akp,
            account=account
        )
    except Exception as e:
        logger_web.error("[Search Endpoint] execute_hybrid_search failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"message": f"Search failed: {str(e)}", "type": "api_error"}},
        )

    return {
        "status": "success",
        "query": query.strip(),
        "results": search_context,
        "citations": combined_citations
    }


@app.get("/stats", response_class=HTMLResponse)
@app.get("/stats/{path:path}", response_class=HTMLResponse)
async def stats_page(path: str | None = None):
    html_path = FRONTEND_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard index.html not found")
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger_web.error("[Stats] Failed to read index.html: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error reading dashboard")

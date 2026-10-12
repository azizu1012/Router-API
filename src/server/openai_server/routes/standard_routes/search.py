"""Router-owned citation search routes."""

from typing import Any

from fastapi import Header, Request
from fastapi.responses import JSONResponse

from src.core.config_n_logg.logger import logger_web
from src.server.openai_server.auth import _resolve_auth, _check_auth
from ..app_init import app


@app.post("/v1/search")
@app.post("/search")
async def web_search_endpoint(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Any:
    auth = _resolve_auth(authorization, x_api_key)
    account = _check_auth(auth)

    try:
        body = await request.json()
    except Exception:
        body = {}

    query = body.get("query")
    if not query or not isinstance(query, str) or not query.strip():
        return JSONResponse(
            status_code=400,
            content={"error": {
                "message": "`query` parameter is required and must be a non-empty string",
                "type": "invalid_request_error"}},
        )

    search_engine = body.get("search_engine")
    if not search_engine:
        search_engine = "duckduckgo"
    elif not isinstance(search_engine, str) or search_engine != "duckduckgo":
        return JSONResponse(
            status_code=400,
            content={"error": {
                "message": "Unknown `search_engine`. Valid value is 'duckduckgo' (Router-owned citation search)",
                "type": "invalid_request_error"}},
        )

    from src.server.openai_server.auth import _auth_key_prefix
    akp = _auth_key_prefix(account)
    from src.tools.search.orchestration.manager import execute_hybrid_search
    try:
        search_context, combined_citations = await execute_hybrid_search(
            [query.strip()],
            search_engine=search_engine,
            auth_key_prefix=akp,
            account=account,
        )
    except Exception as e:
        logger_web.error("[Search Endpoint] execute_hybrid_search failed: %s", e, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": {"message": f"Search failed: {str(e)}", "type": "api_error"}},
        )

    if search_context and not combined_citations:
        logger_web.warning(
            "[Search Endpoint] query %r produced %d chars with 0 citations",
            query.strip()[:120], len(search_context))

    return {
        "status": "success",
        "query": query.strip(),
        "results": search_context,
        "citations": combined_citations,
    }


__all__ = ["web_search_endpoint"]


import asyncio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from src.core.config_n_logg import config
from ..security import (
    check_frontend_rate_limit, record_failed_login, add_security_headers,
    MAX_BODY_BYTES
)
from src.core.config_n_logg.logger import logger_system as logger
from src.backend.schema import init_config_tables, migrate_from_json
from src.core.usage_logger import init_db, start_flush_loop
from src.core.limits import account_limiter
from ...log_watcher import log_watcher
from ...stats_pusher import stats_pusher

app = FastAPI(title="Router API v2", version="2.0.0")


@app.middleware("http")
async def token_limit_middleware(request: Request, call_next):
    """Apply per-token concurrency / RPM / TPM / RPD to authenticated API routes.

    Done as middleware so every endpoint is covered uniformly, including routes
    added later. Two deliberate choices:

    - A failed authentication here is **not** turned into a response. The route's
      own ``_check_auth`` remains the single authority on whether a request is
      authorised, so a 401 is still produced exactly where it always was.
    - The resolved account is cached on ``request.state`` so the route's own
      ``_check_auth`` does not repeat the lookup.
    """
    from ..auth import _resolve_auth, _check_auth, _enforce_token_limits

    release = None
    token = _resolve_auth(
        request.headers.get("authorization"),
        request.headers.get("x-api-key"),
    )
    if token:
        try:
            account = _check_auth(token)
        except HTTPException:
            account = None  # let the route produce the 401 as before
        except Exception:
            account = None
        if account:
            request.state.auth_account = account
            try:
                release = await _enforce_token_limits(
                    account, _estimate_request_tokens(request)
                )
            except HTTPException as e:
                return JSONResponse(
                    status_code=e.status_code,
                    content=e.detail if isinstance(e.detail, dict)
                    else {"error": str(e.detail)},
                )

    try:
        return await call_next(request)
    finally:
        if release:
            await release()


def _estimate_request_tokens(request: Request) -> int:
    """Rough input-token estimate for the per-token TPM budget.

    Read from the cached body if the route already parsed it, otherwise fall back
    to the header sizes. Approximate on purpose: TPM here is the operator's own
    allowance, and being slightly generous at admission is preferable to reading
    the body twice.
    """
    try:
        cached = getattr(request, "_cached_body", None)
        if cached:
            return max(1, len(str(cached)) // 4)
    except Exception:
        pass
    try:
        n = sum(
            len(v or "")
            for v in (request.headers.get("x-api-key", ""), request.headers.get("authorization", ""))
        )
        return max(1, n // 4)
    except Exception:
        return 1


@app.middleware("http")
async def cors_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return JSONResponse(
            content="OK",
            headers={
                "access-control-allow-origin": "*",
                "access-control-allow-methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
                "access-control-allow-headers": "*",
                "access-control-max-age": "86400",
            },
        )
    response = await call_next(request)
    response.headers["access-control-allow-origin"] = "*"
    response.headers["access-control-allow-headers"] = "*"
    return response

@app.middleware("http")
async def static_mount_fallback_middleware(request: Request, call_next):
    """Keep API traffic out of the frontend's catch-all mount.

    The SPA is served by ``app.mount("", StaticFiles(...))``, and a mount with an
    empty path matches *every* request. Two consequences, both of which produced
    an error that pointed at the wrong thing:

    - ``POST /v1/messages/`` answered ``405 Method Not Allowed``. Starlette
      returns 405 for a partial method match and only tries the trailing-slash
      redirect afterwards; the mount is a full match, so the redirect branch was
      never reached and StaticFiles rejected the POST.
    - ``GET /health/`` answered a bare 404 instead of redirecting to ``/health``.

    So a client that appends a slash, or a base URL that already ends in ``/v1``,
    gets "Method Not Allowed" on a route that is plainly registered. Stripping
    the slash here fixes both shapes instead of describing them.

    Anything that still looks like API traffic but matched nothing gets a real
    answer with the path echoed back, rather than StaticFiles' plain 404 — a
    misconfigured ANTHROPIC_BASE_URL is the most likely cause of reaching here.
    """
    path = request.scope.get("path", "/")

    if len(path) > 1 and path.endswith("/"):
        stripped = path.rstrip("/")
        if _matches_an_api_route(stripped, request.scope.get("method", "GET")):
            request.scope["path"] = stripped
            path = stripped

    response = await call_next(request)

    if response.status_code in (404, 405) and _looks_like_api_traffic(path):
        return JSONResponse(
            status_code=404,
            content={"error": {
                "message": f"No route for {request.scope.get('method','')} {path}. "
                           "If this is a client setup problem, ANTHROPIC_BASE_URL "
                           "should be the server root without a trailing slash "
                           "and without /v1 — the client appends /v1/messages "
                           "itself.",
                "type": "invalid_request_error",
            }},
        )
    return response


def _api_route_paths() -> set[str]:
    """Paths registered on the app, excluding the catch-all mounts."""
    return {
        getattr(route, "path", "") for route in app.router.routes
        if getattr(route, "methods", None) and getattr(route, "path", "")
    }


def _matches_an_api_route(path: str, method: str) -> bool:
    for route in app.router.routes:
        methods = getattr(route, "methods", None)
        if not methods:                   # a Mount or WebSocket route
            continue
        if getattr(route, "path", "") == path and method in methods:
            return True
    return False


def _looks_like_api_traffic(path: str) -> bool:
    return path.startswith(("/v1/", "/v1beta/", "/v1alpha/", "/messages",
                           "/opencode/"))


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Body size limit
    cl = request.headers.get("content-length")
    if cl and int(cl) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "Request too large"})

    # Frontend rate limit (login/dashboard) — API có limiter riêng
    block = await check_frontend_rate_limit(request)
    if block:
        return block

    response = await call_next(request)

    # Track failed logins
    if request.url.path == "/dashboard/login" and response.status_code in (401, 403):
        ip = request.client.host if request.client else "unknown"
        await record_failed_login(ip)

    # Security headers
    add_security_headers(response)
    return response

# Watch .env file task
async def _watch_env_file():
    from src.core.config_n_logg import ENV_PATH, reload_config
    from src.backend.key_status import register_keys_in_db, set_key_tier_batch_db
    from src.core.limits import clear_rate_limiters
    from src.core.router import router
    from src.core.providers import api_manager

    if not ENV_PATH.exists():
        logger.warning("[EnvWatch] .env file does not exist at %s, skipping watch", ENV_PATH)
        return

    def _set_tiers():
        tiers = {
            k: "free" if i < config.FREE_KEY_END
            else "premium" if i < config.PREMIUM_KEY_END
            else "admin"
            for i, k in enumerate(config.GEMINI_API_KEYS)
        }
        set_key_tier_batch_db(tiers)
    _set_tiers()

    # Initial register of standard keys on startup
    register_keys_in_db(list(config.GEMINI_API_KEYS))
    _set_tiers()
    router.refresh_keys()

    last_mtime = ENV_PATH.stat().st_mtime
    logger.info("[EnvWatch] Started watching .env file for changes at %s", ENV_PATH)

    while True:
        try:
            await asyncio.sleep(3)
            if ENV_PATH.exists():
                mtime = ENV_PATH.stat().st_mtime
                if mtime > last_mtime:
                    logger.info("[EnvWatch] .env file change detected! Reloading keys & models...")
                    last_mtime = mtime
                    from dotenv import load_dotenv
                    load_dotenv(ENV_PATH, override=True)
                    new_keys = reload_config()
                    register_keys_in_db(new_keys)
                    _set_tiers()
                    router.refresh_keys()
                    api_manager.refresh_pool_size()
                    clear_rate_limiters()

                    from src.core.api_config import reload_model_config
                    reload_model_config()
                    from src.backend.model_config import sync_env_to_db
                    sync_env_to_db()

                    logger.info("[EnvWatch] Keys and models successfully reloaded and synced to DB!")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("[EnvWatch] Error in _watch_env_file: %s", e)

_background_tasks = set()

@app.on_event("startup")
async def _init_usage_db():
    init_config_tables()
    migrate_from_json()
    await init_db()

    # Reset active requests for all API keys on startup to clear any counts left hanging from a previous crash/restart
    try:
        from src.core.router import router
        router.reset_active_requests()
        logger.info("[Startup] Reset active request counts for all API keys.")
    except Exception as startup_err:
        logger.error("[Startup] Failed to reset active requests: %s", startup_err)

    await account_limiter.restore_rpd_counts()
    start_flush_loop()
    
    task = asyncio.create_task(_watch_env_file())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    log_watcher.start_all()
    stats_pusher.start()



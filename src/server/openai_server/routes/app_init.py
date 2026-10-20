import asyncio
from contextlib import asynccontextmanager
import json
import urllib.parse
import uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from src.core.config_n_logg import config
from ..security import (
    check_frontend_rate_limit, record_failed_login, add_security_headers,
    MAX_BODY_BYTES
)
from src.core.config_n_logg.logger import logger_system as logger
from src.backend.schema import init_config_tables, migrate_from_json
from src.core.observability.usage_logger import init_db, start_flush_loop
from src.core.limits import account_limiter
from ...realtime.log_watcher import log_watcher
from ...realtime.stats import stats_pusher
from src.core.runtime.request_context import set_request_id

app = FastAPI(title="Router API v2", version="2.0.0")


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """Attach one bounded request ID to every request and response."""
    request_id = request.headers.get("x-request-id", "").strip()[:128] or uuid.uuid4().hex
    request.state.request_id = request_id
    set_request_id(request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# Paths that mean "this account asked for the MCP tool and nothing else".
# An mcp_only account is refused on every model route and allowed through here;
# without this exemption the block would close its own front door, because this
# middleware also runs for requests arriving via the /mcp mount.
MCP_PATH_PREFIX = "/mcp"

# Everything that reaches a model. Enumerated rather than pattern-matched so a
# new route is refused by default instead of quietly becoming reachable.
_MODEL_PATHS = (
    "/v1/messages", "/messages", "/v1/chat/completions", "/v1/completions",
    "/v1/responses", "/opencode/", "/v1beta/", "/v1alpha/", "/v1/models/",
)


def _is_model_route(path: str) -> bool:
    if path.startswith(MCP_PATH_PREFIX):
        return False
    stripped = path.rstrip("/") or "/"
    return any(stripped == p.rstrip("/") or stripped.startswith(p)
               for p in _MODEL_PATHS)


@app.middleware("http")
async def mcp_only_middleware(request: Request, call_next):
    """Refuse model traffic for an account marked mcp_only.

    One check here rather than one per route: this middleware already resolves
    the account for every request, and the model routes are spread across four
    files including the Gemini passthrough — five separate edits would drift.
    """
    path = request.scope.get("path", "/")
    if not _is_model_route(path):
        return await call_next(request)

    account = getattr(request.state, "auth_account", None)
    if account and account.get("mcp_only") and not account.get("token_key_id"):
        # Only a whole account can be mcp_only. A token minted under it stays a
        # normal API credential, so an operator can hand out a scoped one by
        # choosing not to set the flag.
        return JSONResponse(
            status_code=403,
            content={"error": {
                "message": "This account is restricted to the MCP search tool "
                           "and cannot call models.",
                "type": "permission_error",
            }},
        )
    return await call_next(request)


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
    origin = request.headers.get("origin")
    path = request.url.path
    is_sensitive = path.startswith("/dashboard/") or path.startswith("/api/me/")

    # Determine allowed origin
    if is_sensitive and origin:
        host = request.headers.get("host", "").split(":")[0]
        parsed_origin = urllib.parse.urlparse(origin).hostname or ""
        # Allow same host, localhost, 127.0.0.1
        if parsed_origin == host or parsed_origin in ("localhost", "127.0.0.1"):
            allowed_origin = origin
        else:
            allowed_origin = ""
    else:
        allowed_origin = origin or "*" if not is_sensitive else ""

    if request.method == "OPTIONS":
        headers = {
            "access-control-allow-methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
            "access-control-allow-headers": "*",
            "access-control-max-age": "86400",
        }
        if allowed_origin:
            headers["access-control-allow-origin"] = allowed_origin
        return JSONResponse(content="OK", headers=headers)

    response = await call_next(request)
    if allowed_origin:
        response.headers["access-control-allow-origin"] = allowed_origin
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

    # A base URL ending in "/v1" makes the client append "/v1/messages" to a
    # path that already carries one. 9router documents that base URL shape, so
    # this is the shape people copy, and a gateway that answers it has to serve
    # it: /v1/v1/messages -> /v1/messages. Rewriting rather than redirecting,
    # because a 307 would make the client replay a request body.
    if path.startswith("/v1/v1"):
        collapsed = "/v1" + path[len("/v1/v1"):]
        request.scope["path"] = collapsed
        path = collapsed

    # The MCP transport lives at the root of a sub-app mounted at /mcp, and a
    # mount only matches "/mcp/..." — never "/mcp" itself. Clients post to
    # /mcp. Rewrite rather than redirect: a 307 makes the client replay a
    # JSON-RPC POST, and not every MCP client follows one carrying a body.
    if path == MCP_PATH_PREFIX:
        request.scope["path"] = path + "/"
        path = request.scope["path"]

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
async def http_access_log_middleware(request: Request, call_next):
    """Log every inbound request's method, path and status.

    Every other log line in this app sits below the middleware stack, so a
    request rejected by a guard -- 401 from auth, 404 from the SPA catch-all, 429
    from the rate limiter -- produced no trace at all. "Nothing in the log" then
    reads as "the client never called", which is the opposite of what a guard
    rejecting a request means, and it sent the debugging in the wrong direction
    more than once here.
    """
    method = request.scope.get("method", "GET")
    path = request.scope.get("path", "/")
    try:
        response = await call_next(request)
    except Exception:
        logger.warning("[Access] %s %s -> EXCEPTION", method, path, exc_info=True)
        raise
    status = response.status_code
    if status >= 400:
        logger.warning("[Access] %s %s -> %d", method, path, status)
    return response


@app.middleware("http")
async def sub_agent_context_middleware(request: Request, call_next):
    """Bind this request's sub-agent verdict to `is_sub_agent_context`.

    The router reads that flag in `is_global_cooldown_active`, `record_429`,
    `freeze_key` and the gemini rate limiter, and every one of those decisions
    is about key health that is *global and shared*: a worker that trips the
    cooldown trips it for every interactive session on the same keys too. The
    isolation those reads describe was written down and never wired — nothing
    in the repo called `.set()`, so the flag sat at its False default and a
    noisy sub-agent fan-out could still stall the user's session. The "Sub-Agent
    Bypass" in `freeze_key`'s docstring was documentation, not code. See
    docs/bug-logs.md Bug #40 item E.

    The request boundary is the one point every model route passes through,
    streaming and non-streaming alike. BaseHTTPMiddleware runs the rest of the
    app in a task that copies this context, so the value set here reaches the
    route and its streaming generator, and `reset` restores the middleware's own
    context instead of leaving the flag set for whatever this worker serves
    next. `request.body()` caches, so the route still reads its own body.
    """
    path = request.scope.get("path", "/")
    if request.method != "POST" or not _is_model_route(path):
        return await call_next(request)

    try:
        raw = await request.body()
        body = json.loads(raw) if raw else None
    except Exception:
        # Not ours to reject: a body we cannot parse is a body the route will
        # reject on its own terms, with a status the client understands.
        return await call_next(request)

    if not isinstance(body, dict):
        return await call_next(request)

    from src.core.router.core.router import is_sub_agent_context
    from ..auth import is_sub_agent_request

    token = is_sub_agent_context.set(
        is_sub_agent_request(body, is_opencode=path.startswith("/opencode/"))
    )
    try:
        return await call_next(request)
    finally:
        is_sub_agent_context.reset(token)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Body size limit
    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > MAX_BODY_BYTES:
                return JSONResponse(status_code=413, content={"detail": "Request too large"})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header"})

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
    from src.backend.keys.status import register_keys_in_db, set_key_tier_batch_db
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

                    from src.core.config.api_config import reload_model_config
                    reload_model_config()
                    from src.backend.models.config import sync_env_to_db
                    sync_env_to_db()

                    logger.info("[EnvWatch] Keys and models successfully reloaded and synced to DB!")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("[EnvWatch] Error in _watch_env_file: %s", e)

_background_tasks = set()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_config_tables()
    migrate_from_json()
    await init_db()
    try:
        from src.server.openai_server import mcp_routes
        await mcp_routes.start_mcp_session()
    except Exception as mcp_err:
        # The API must still serve without MCP.
        logger.error("[Startup] MCP session failed to start: %s", mcp_err)

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

    yield

    """Stop app-owned tasks before test or process database cleanup."""
    log_watcher.stop_all()
    await stats_pusher.stop()

    tasks = list(_background_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _background_tasks.clear()

    try:
        from src.backend.keys.status import flush_pending_writes
        await asyncio.to_thread(flush_pending_writes)
    except Exception as shutdown_err:
        logger.warning("[Shutdown] Failed to flush key-status writes: %s", shutdown_err)


app.router.lifespan_context = lifespan




import asyncio
from fastapi import FastAPI, Request
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
                    logger.info("[EnvWatch] .env file change detected! Reloading keys...")
                    last_mtime = mtime
                    new_keys = reload_config()
                    register_keys_in_db(new_keys)
                    _set_tiers()
                    router.refresh_keys()
                    api_manager.refresh_pool_size()
                    clear_rate_limiters()
                    logger.info("[EnvWatch] Keys successfully reloaded dynamically!")
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



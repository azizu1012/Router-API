import time
import asyncio
from collections import defaultdict, deque
from fastapi import Request
from fastapi.responses import JSONResponse
from src.core.config_n_logg.logger import logger_system as logger

# ── Config ──────────────────────────────────────────────────────────
LOGIN_RATE = 5            # /dashboard/login: 5 req/min/IP
DASHBOARD_RATE = 60       # /dashboard/*: 60 req/min/IP

BF_MAX_FAILS = 5          # block IP sau N lần login sai
BF_WINDOW = 900           # đếm trong 15 phút
BF_BLOCK_DURATION = 1800  # block 30 phút

MAX_BODY_BYTES = 10 * 1024 * 1024

# ── State ───────────────────────────────────────────────────────────
_lock = asyncio.Lock()
_login_hits: dict[str, deque] = defaultdict(deque)
_dash_hits: dict[str, deque] = defaultdict(deque)
_bf_fails: dict[str, list] = defaultdict(list)
_bf_blocked: dict[str, float] = {}


def _trim(dq: deque, window: int, now: float):
    while dq and dq[0] < now - window:
        dq.popleft()


def _hit(dq: deque, window: int, rate: int, now: float) -> bool:
    _trim(dq, window, now)
    if len(dq) >= rate:
        return False
    dq.append(now)
    return True


async def check_frontend_rate_limit(request: Request) -> JSONResponse | None:
    """Rate limit only for dashboard/login. API endpoints use their own limiter."""
    ip = request.client.host if request.client else "unknown"
    path = request.url.path
    now = time.time()

    async with _lock:
        if ip in _bf_blocked:
            if now < _bf_blocked[ip]:
                return JSONResponse(status_code=429, content={"detail": "Too many failed logins. Try later."})
            del _bf_blocked[ip]

        if path.startswith("/dashboard/login"):
            if not _hit(_login_hits[ip], 60, LOGIN_RATE, now):
                logger.warning("[Security] Login rate exceeded IP %s", ip)
                return JSONResponse(status_code=429, content={"detail": "Too many login attempts."})

        elif path.startswith("/dashboard/"):
            if not _hit(_dash_hits[ip], 60, DASHBOARD_RATE, now):
                return JSONResponse(status_code=429, content={"detail": "Too many requests."})

    return None


async def record_failed_login(ip: str):
    now = time.time()
    async with _lock:
        _bf_fails[ip] = [t for t in _bf_fails.get(ip, []) if t > now - BF_WINDOW]
        _bf_fails[ip].append(now)
        if len(_bf_fails[ip]) >= BF_MAX_FAILS:
            _bf_blocked[ip] = now + BF_BLOCK_DURATION
            logger.warning("[Security] IP %s blocked for %ss after %d failed logins",
                           ip, BF_BLOCK_DURATION, BF_MAX_FAILS)
            del _bf_fails[ip]


async def clear_login_rate(ip: str):
    async with _lock:
        _login_hits[ip].clear()


def add_security_headers(response):
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["x-frame-options"] = "DENY"
    response.headers["referrer-policy"] = "no-referrer"
    response.headers["x-xss-protection"] = "1; mode=block"
    response.headers["permissions-policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"

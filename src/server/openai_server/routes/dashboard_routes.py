import asyncio
import time
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from src.core.config_n_logg import config
from src.core.limits import account_limiter, token_limiter
from src.core.accounts import account_manager
from src.backend.accounts import (
    find_account_by_key, find_account_by_name, list_accounts_db
)
from src.backend.account_keys import (
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MIN_INTERVAL_SECONDS,
    compose_token,
    create_key_db,
    delete_key_db,
    get_credential_db,
    get_key_db,
    list_keys_db,
    set_password_db,
    verify_login_db,
    verify_password,
)
from src.backend.key_status import (
    get_key_status_db, db_load_active_penalties
)
from src.backend.endpoints import list_endpoints_db
from src.core.usage_logger import get_stats, get_top_keys
from .app_init import app
from .auth_session import _make_session_token, _require_dashboard

def _calculate_financial_savings(summary: list) -> dict:
    total_prompt = 0
    total_completion = 0
    total_standard_cost = 0.0
    total_cached_cost = 0.0
    total_gemini_cost = 0.0

    from src.backend.model_prices import get_model_price

    for row in summary or []:
        alias = str(row.get("model_alias") or "").lower()
        p = row.get("p", 0) or 0
        c = row.get("c", 0) or 0
        total_prompt += p
        total_completion += c

        # Lấy giá từ DB thay vì hardcode
        cfg = get_model_price(alias) or {}
        in_rate = float(cfg.get("input_rate_per_1k", 0.0015))
        out_rate = float(cfg.get("output_rate_per_1k", 0.009))

        # 1. Standard Cost (Claude 3.7 Sonnet pricing)
        std_input = p * 3.0 / 1_000_000.0
        std_output = c * 15.0 / 1_000_000.0
        total_standard_cost += std_input + std_output

        # 2. Cached Cost (Claude 3.7 Sonnet simulated)
        cc_val = row.get("cc", 0) or 0
        cr_val = row.get("cr", 0) or 0

        if cc_val > 0 or cr_val > 0:
            uncached_input = max(0, p - cc_val - cr_val)
            cached_input = (uncached_input * 3.0 + cc_val * 3.75 + cr_val * 0.3) / 1_000_000.0
        else:
            if p > 2000:
                cache_read_tokens = int(p * 0.8)
                new_input_tokens = p - cache_read_tokens
                cached_input = (new_input_tokens * 3.0 + cache_read_tokens * 0.3) / 1_000_000.0
            else:
                cached_input = p * 3.0 / 1_000_000.0

        total_cached_cost += cached_input + std_output

        # 3. Actual Gemini Cost
        gem_uncached = max(0, p - cr_val)
        cache_rate = out_rate * 0.25
        gem_in = (gem_uncached * in_rate * 1000 + cr_val * cache_rate * 1000) / 1_000_000.0
        gem_out = c * out_rate * 1000 / 1_000_000.0
        total_gemini_cost += gem_in + gem_out

    net_savings = total_standard_cost - total_gemini_cost

    return {
        "standard_cost": round(total_standard_cost, 4),
        "cached_cost": round(total_cached_cost, 4),
        "gemini_cost": round(total_gemini_cost, 4),
        "net_savings": round(net_savings, 4),
    }


@app.post("/dashboard/login")
async def dashboard_login(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    key = str(body.get("auth_key", "")).strip()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))

    # Legacy path: a raw auth_key still logs in, so existing clients keep working.
    if key and not username:
        if config.AUTH_TOKEN and key == config.AUTH_TOKEN:
            account = {
                "account_id": "admin",
                "name": "Administrator",
                "auth_key": config.AUTH_TOKEN,
                "tier": "admin",
            }
        else:
            account = await asyncio.to_thread(find_account_by_key, key)
    elif username:
        account = await asyncio.to_thread(verify_login_db, username, password)
    else:
        return JSONResponse(
            status_code=400,
            content={"error": "username + password required"},
        )

    if not account:
        return JSONResponse(
            status_code=401, content={"error": "Invalid username, password, or key"}
        )
    token = _make_session_token(account)
    return {"token": token, "name": account.get("name"), "tier": account.get("tier", "free")}


@app.post("/dashboard/password")
async def dashboard_change_password(request: Request):
    """Change the signed-in account's web password."""
    payload = _require_dashboard(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    current = str(body.get("current_password", ""))
    new = str(body.get("new_password", ""))
    if not new:
        return JSONResponse(status_code=400, content={"error": "new_password required"})
    if len(new) < 4:
        return JSONResponse(
            status_code=400, content={"error": "Password must be at least 4 characters"}
        )

    account = await asyncio.to_thread(find_account_by_name, payload.get("name", ""))
    if not account:
        return JSONResponse(status_code=401, content={"error": "Account not found"})

    cred = get_credential_db(account["account_id"])
    if cred and not verify_password(current, cred["password_hash"], cred["password_salt"]):
        return JSONResponse(status_code=401, content={"error": "Current password is incorrect"})

    set_password_db(account["account_id"], new, must_change=0)
    return {"ok": True, "name": account.get("name")}


@app.get("/dashboard/my/keys")
async def my_keys(request: Request):
    """List the signed-in account's auth tokens, showing full wire tokens."""
    payload = _require_dashboard(request)
    account = await asyncio.to_thread(find_account_by_name, payload.get("name", ""))
    if not account:
        return JSONResponse(status_code=404, content={"error": "Account not found"})
    rows = list_keys_db(account["account_id"])
    return {
        "keys": [
            {
                "key_id": r["key_id"],
                "token": compose_token(r["name"], r["token_code"]),
                "label": r.get("label", ""),
                "enabled": bool(r["enabled"]),
                "tier": r["tier"],
                "rpm": r["rpm"],
                "tpm": r["tpm"],
                "rpd": r["rpd"],
                "max_concurrency": r["max_concurrency"],
                "min_interval_seconds": r["min_interval_seconds"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }


@app.post("/dashboard/my/keys/create")
async def my_create_key(request: Request):
    """Issue a new auth token for the signed-in account.

    Concurrency and interval default to the platform defaults (6 / 3s); a user
    may request lower. An admin account may raise the ceiling.
    """
    payload = _require_dashboard(request)
    account = await asyncio.to_thread(find_account_by_name, payload.get("name", ""))
    if not account:
        return JSONResponse(status_code=404, content={"error": "Account not found"})
    try:
        body = await request.json()
    except Exception:
        body = {}

    is_admin = payload.get("tier") == "admin"
    from src.core.config_n_logg import config as _cfg

    def _num(key: str, default: int, lo: int, hi: int) -> int:
        try:
            v = int(body.get(key, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    # Ceilings for a self-service user. An admin may exceed all of these.
    if is_admin:
        c_max_conc, c_rpm, c_tpm, c_rpd = 64, 100_000, 100_000_000, 100_000
    else:
        c_max_conc = DEFAULT_MAX_CONCURRENCY
        c_rpm = max(1, int(_cfg.DEFAULT_ACCOUNT_RPM))
        c_tpm = int(_cfg.DEFAULT_ACCOUNT_TPM)
        c_rpd = int(_cfg.DEFAULT_ACCOUNT_RPD)

    max_c = _num("max_concurrency", DEFAULT_MAX_CONCURRENCY, 1, c_max_conc)
    rpm = _num("rpm", min(int(account.get("rpm", 30)), c_rpm), 1, c_rpm)
    tpm = _num("tpm", min(int(account.get("tpm", 200000)), c_tpm), 1000, c_tpm)
    rpd = _num("rpd", min(int(account.get("rpd", 1000)), c_rpd), 1, c_rpd)

    try:
        interval = float(body.get("min_interval_seconds", DEFAULT_MIN_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        interval = DEFAULT_MIN_INTERVAL_SECONDS
    if not is_admin:
        interval = max(interval, DEFAULT_MIN_INTERVAL_SECONDS)

    row = create_key_db(
        account["account_id"],
        name=account["name"],
        tier=account.get("tier", "free"),
        rpm=rpm,
        tpm=tpm,
        rpd=rpd,
        max_concurrency=max_c,
        min_interval_seconds=interval,
        label=str(body.get("label", ""))[:64],
    )
    account_manager.invalidate_cache()
    return {
        "key_id": row["key_id"],
        "token": compose_token(row["name"], row["token_code"]),
        "max_concurrency": row["max_concurrency"],
        "rpm": row["rpm"],
        "tpm": row["tpm"],
        "rpd": row["rpd"],
        "min_interval_seconds": row["min_interval_seconds"],
        "interval_effective": row["max_concurrency"] == 1,
    }


@app.post("/dashboard/my/keys/update")
async def my_update_key(request: Request):
    """Adjust one of the signed-in account's tokens.

    Same rule as create: a user may tighten a limit but never widen one. The
    ceilings are recomputed here rather than trusted from the request, so a
    hand-rolled client cannot raise its own budget by sending a large number.
    """
    payload = _require_dashboard(request)
    account = await asyncio.to_thread(find_account_by_name, payload.get("name", ""))
    if not account:
        return JSONResponse(status_code=404, content={"error": "Account not found"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    key_id = str(body.get("key_id", "")).strip()
    if not key_id:
        return JSONResponse(status_code=400, content={"error": "key_id required"})

    row = get_key_db(key_id)
    if not row or row["account_id"] != account["account_id"]:
        return JSONResponse(status_code=404, content={"error": "Key not found"})

    is_admin = payload.get("tier") == "admin"
    from src.core.config_n_logg import config as _cfg

    def _clamp(key: str, floor: int, hi: int) -> Optional[int]:
        if key not in body:
            return None
        try:
            v = int(body[key])
        except (TypeError, ValueError):
            return None
        return max(floor, min(hi, v))

    if is_admin:
        c_max_conc, c_rpm, c_tpm, c_rpd = 64, 100_000, 100_000_000, 100_000
    else:
        c_max_conc = DEFAULT_MAX_CONCURRENCY
        c_rpm = max(1, int(_cfg.DEFAULT_ACCOUNT_RPM))
        c_tpm = int(_cfg.DEFAULT_ACCOUNT_TPM)
        c_rpd = int(_cfg.DEFAULT_ACCOUNT_RPD)

    updates: Dict[str, Any] = {}
    for field, floor, hi in (
        ("max_concurrency", 1, c_max_conc),
        ("rpm", 1, c_rpm),
        ("tpm", 1000, c_tpm),
        ("rpd", 1, c_rpd),
    ):
        clamped = _clamp(field, floor, hi)
        if clamped is not None:
            updates[field] = clamped

    if "label" in body:
        updates["label"] = str(body["label"])[:64]
    if "enabled" in body:
        updates["enabled"] = 1 if body["enabled"] else 0

    if "min_interval_seconds" in body:
        try:
            interval = max(0.0, float(body["min_interval_seconds"]))
        except (TypeError, ValueError):
            interval = DEFAULT_MIN_INTERVAL_SECONDS
        if not is_admin:
            interval = max(interval, DEFAULT_MIN_INTERVAL_SECONDS)
        updates["min_interval_seconds"] = interval

    if not updates:
        return JSONResponse(status_code=400, content={"error": "nothing to update"})

    updated = account_manager.update_key(key_id, **updates)
    account_manager.invalidate_cache()
    return {
        "ok": True,
        "key_id": key_id,
        "max_concurrency": (updated or {}).get("max_concurrency"),
        "rpm": (updated or {}).get("rpm"),
        "tpm": (updated or {}).get("tpm"),
        "rpd": (updated or {}).get("rpd"),
        "interval_effective": (updated or {}).get("max_concurrency") == 1,
    }


@app.post("/dashboard/my/keys/revoke")
async def my_revoke_key(request: Request):
    payload = _require_dashboard(request)
    account = await asyncio.to_thread(find_account_by_name, payload.get("name", ""))
    if not account:
        return JSONResponse(status_code=404, content={"error": "Account not found"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    key_id = str(body.get("key_id", "")).strip()
    if not key_id:
        return JSONResponse(status_code=400, content={"error": "key_id required"})

    row = get_key_db(key_id)
    if not row or row["account_id"] != account["account_id"]:
        return JSONResponse(status_code=404, content={"error": "Key not found"})
    delete_key_db(key_id)
    account_manager.invalidate_cache()
    token_limiter.reset(key_id)
    return {"ok": True, "key_id": key_id}


@app.get("/dashboard/me")
async def dashboard_me(request: Request):
    payload = _require_dashboard(request)
    account = await asyncio.to_thread(find_account_by_name, payload.get("name", ""))
    if not account:
        return payload
    snap = await account_limiter.snapshot(account)
    
    from src.core.limits.account_limiter import get_effective_limits_by_pool, get_active_account_counts
    active_counts = await get_active_account_counts()
    
    user_rpm = account.get("rpm", 0)
    user_tpm = account.get("tpm", 0)
    user_rpd = account.get("rpd", 0)
    
    tier = account.get("tier", "free")
    if tier == "admin":
        user_rpm = 999999
        user_tpm = 999999999
        user_rpd = 999999
    elif tier == "premium":
        user_rpm = int(user_rpm * 1.5)
        user_tpm = int(user_tpm * 1.5)
        user_rpd = int(user_rpd * 1.5)
        
    from src.core.limits.account_limiter import calculate_pool_capacities_for_user
    pool_stats = calculate_pool_capacities_for_user(tier, active_counts, account)
    
    res_pools = {}
    for pkey in ["flash", "lite"]:
        throttled_rpm, throttled_tpm, throttled_rpd = await get_effective_limits_by_pool(account, pkey)
        
        shown_rpm_limit = min(user_rpm, throttled_rpm)
        shown_tpm_limit = min(user_tpm, throttled_tpm)
        shown_rpd_limit = min(user_rpd, throttled_rpd)

        p_snap = snap.get(pkey, {"rpm_used": 0, "tpm_used": 0, "rpd_used": 0})
        p_rpm_used = p_snap["rpm_used"]
        p_tpm_used = p_snap["tpm_used"]
        p_rpd_used = p_snap["rpd_used"]
        
        p_rpm_left = max(0, shown_rpm_limit - p_rpm_used)
        p_tpm_left = max(0, shown_tpm_limit - p_tpm_used)
        p_rpd_left = max(0, shown_rpd_limit - p_rpd_used)

        pool_stats[pkey]["rpm_left"] = min(p_rpm_left, pool_stats[pkey]["rpm_left"])
        pool_stats[pkey]["tpm_left"] = min(p_tpm_left, pool_stats[pkey]["tpm_left"])
        pool_stats[pkey]["rpd_left"] = min(p_rpd_left, pool_stats[pkey]["rpd_left"])

        from src.core.api_config import MODEL_CONTEXT_LENGTH

        for label, mins in [("1h", 60), ("12h", 720), ("24h", 1440)]:
            # Max requests user can make in this period is bounded by their RPD left and RPM over the period
            max_reqs_left = min(p_rpd_left, p_rpm_left + (mins - 1) * shown_rpm_limit)
            # Max tokens user can consume based on their requests limits and model context length
            user_tokens_by_rpd_left = max_reqs_left * MODEL_CONTEXT_LENGTH

            # User's token-per-minute capability over the period
            user_tokens_in_period = p_tpm_left + (mins - 1) * shown_tpm_limit

            effective_user_left = min(user_tokens_in_period, user_tokens_by_rpd_left)
            pool_stats[pkey][f"tokens_{label}_left"] = min(int(effective_user_left), pool_stats[pkey][f"tokens_{label}_left"])

            # For limits (maximum potential capacity)
            max_reqs_limit = min(shown_rpd_limit, mins * shown_rpm_limit)
            user_tokens_by_rpd_limit = max_reqs_limit * MODEL_CONTEXT_LENGTH
            user_tokens_in_period_total = mins * shown_tpm_limit

            effective_user_limit = min(user_tokens_in_period_total, user_tokens_by_rpd_limit)
            pool_stats[pkey][f"tokens_{label}_limit"] = min(int(effective_user_limit), pool_stats[pkey][f"tokens_{label}_limit"])
            
        res_pools[pkey] = {
            "rpm": shown_rpm_limit,
            "tpm": shown_tpm_limit,
            "rpd": shown_rpd_limit,
            "rpm_used": min(p_rpm_used, shown_rpm_limit),
            "tpm_used": min(p_tpm_used, shown_tpm_limit),
            "rpd_used": min(p_rpd_used, shown_rpd_limit),
            "rpm_left": p_rpm_left,
            "tpm_left": p_tpm_left,
            "rpd_left": p_rpd_left,
        }
        
    return {
        **payload,
        "account_id": account.get("account_id"),
        "web_search_enabled": bool(account.get("web_search_enabled", 0)),
        "search_engine": account.get("search_engine", "auto"),
        "flash": res_pools["flash"],
        "lite": res_pools["lite"],
        "flash_pool": pool_stats["flash"],
        "lite_pool": pool_stats["lite"],
    }


@app.get("/dashboard/accounts")
async def dashboard_accounts(request: Request):
    payload = _require_dashboard(request)
    is_admin = payload.get("tier") == "admin"
    accs = await asyncio.to_thread(list_accounts_db, True)
    if is_admin:
        return {"accounts": [dict(a) for a in accs]}
    else:
        safe = [{k: v for k, v in a.items() if k != "auth_key"} for a in accs]
        return {"accounts": safe}


@app.get("/dashboard/keys")
async def dashboard_keys(request: Request):
    payload = _require_dashboard(request)
    is_admin = payload.get("tier") == "admin"
    raw = await asyncio.to_thread(get_key_status_db)
    from src.core.limits.gemini_rate_limiter import _key_usage, _usage_lock
    now = time.time()
    keys = []
    for k, v in raw.items():
        display_name = (k[:6] + "****" + k[-4:]) if len(k) > 10 else "****"
        today_calls = 0
        with _usage_lock:
            usage_entry = _key_usage.get(k)
            if usage_entry:
                today_calls = usage_entry.get("today", 0)
        keys.append({
            "key": k if is_admin else display_name,
            "display": display_name,
            "is_oauth": False,
            "tier": v.get("tier", "free"),
            "enabled": bool(v.get("enabled", 1)),
            "usage": v.get("usage", 0),
            "active_requests": v.get("active_requests", 0),
            "frozen_until": v.get("frozen_until", 0),
            "frozen": v.get("frozen_until", 0) > now,
            "consecutive_failures": v.get("consecutive_failures", 0),
            "last_success": v.get("last_success", 0),
            "today": today_calls,
            "allowed_pools": v.get("allowed_pools", []),
            "expiry_date": 0,
            "per_model": {},
            "google_models": [],
        })
    return {"keys": keys}


@app.get("/dashboard/penalties")
async def dashboard_penalties(request: Request):
    _require_dashboard(request)
    raw = await asyncio.to_thread(db_load_active_penalties)
    ps = []
    for pkey, p in raw.items():
        k = p.get("key", "")
        masked = (k[:6] + "****" + k[-4:]) if len(k) > 10 else "****"
        ps.append({
            "pkey": pkey,
            "key": masked,
            "model_id": p.get("model_id"),
            "reason": p.get("reason"),
            "expires": p.get("expires"),
            "score_reduction": p.get("score_reduction"),
        })
    ps.sort(key=lambda x: x.get("expires", 0))
    return {"penalties": ps}


@app.get("/api/model-pools")
async def api_model_pools(request: Request):
    _require_dashboard(request)
    from src.core.api_config import MODEL_POOLS
    pools = [
        {
            "id": pid,
            "label": "Flash Pool" if "flash-lite" not in pid else "Lite Pool",
            "icon": "⚡" if "flash-lite" not in pid else "💡",
            "short": "flash" if "flash-lite" not in pid else "lite",
            "members": p.get("members", []),
        }
        for pid, p in MODEL_POOLS.items()
    ]
    return {"pools": pools}


@app.get("/dashboard/endpoints")
async def dashboard_endpoints(request: Request):
    _require_dashboard(request)
    eps = await asyncio.to_thread(list_endpoints_db)
    safe = []
    for e in eps:
        ep = {k: v for k, v in e.items() if k != "auth_key"}
        aid = e.get("account_id", "")
        if aid:
            from src.backend.accounts import list_accounts_db
            for a in list_accounts_db():
                if a.get("account_id") == aid:
                    ep["account_name"] = a.get("name", "")
                    break
            if not ep.get("account_name"):
                ep["account_name"] = aid
        else:
            ep["account_name"] = ""
        safe.append(ep)
    return {"endpoints": safe}


@app.get("/dashboard/logs/history")
async def dashboard_logs_history(request: Request, channel: str = "proxy", lines: int = 200):
    _require_dashboard(request)
    from ...log_watcher import log_watcher
    history = log_watcher.get_history(channel, max(1, min(5000, lines)))
    return {"channel": channel, "lines": len(history), "history": history}


@app.get("/dashboard/my-stats")
async def dashboard_my_stats(request: Request, days: int = 30):
    payload = _require_dashboard(request)
    from src.core.usage_logger import get_stats_for_prefix
    account = await asyncio.to_thread(find_account_by_name, payload.get("name", ""))
    if not account:
        return {"summary": [], "daily": [], "total_requests": 0, "savings": {"standard_cost": 0.0, "cached_cost": 0.0, "gemini_cost": 0.0, "net_savings": 0.0}}
    ak = account.get("auth_key", "")
    prefix = ak[-8:] if len(ak) >= 8 else ak
    res = await get_stats_for_prefix(prefix, days)
    savings_data = _calculate_financial_savings(res.get("summary", []))
    return {**res, "savings": savings_data}


@app.get("/api/model-pools-detail")
async def get_model_pools_api(request: Request):
    _require_dashboard(request)
    from src.core.api_config import AVAILABLE_MODELS, MODEL_POOLS, is_sunset_25
    from src.core.router.core.router import router as core_router

    from src.core.limits.gemini_rate_limiter import get_rate_limiter

    pools_data = []
    for pool_name, pool_cfg in MODEL_POOLS.items():
        members = []
        for member in pool_cfg["members"]:
            if is_sunset_25() and member in ("gemini-flash-25", "gemini-flash-25-lite"):
                continue
            cfg = AVAILABLE_MODELS.get(member, {})
            limiter = get_rate_limiter(member)
            backing_id = cfg.get("model_id", member)
            health = core_router._model_health.get(member, {})
            members.append({
                "model_id": backing_id,
                "alias": member,
                "rpm": limiter.rpm_limit,
                "tpm": limiter.tpm_limit,
                "health_score": health.get("score", 100.0) if isinstance(health, dict) else 100.0,
            })

        # Append assigned custom endpoints (avoiding double-counting custom endpoint models when pool stats are calculated)
        try:
            custom_endpoints = core_router.get_pool_custom_models(pool_name)
            for item in custom_endpoints:
                model_id = item["model_id"]
                # Avoid duplicates
                if not any(m["model_id"] == model_id for m in members):
                    ep_name = item["endpoint"].get("name", "?")
                    members.append({
                        "model_id": model_id,
                        "endpoint_name": ep_name,
                        "alias": model_id,
                        "rpm": 10, # Custom endpoints are rate-limited via _CUSTOM_POOL_RPM = 10
                        "tpm": 999999999, # Unlimited placeholder
                        "health_score": 100.0,
                    })
        except Exception as e:
            import logging
            logging.getLogger("uvicorn").error("[dashboard_routes] Failed to get custom models for pool %s: %s", pool_name, e)

        total_rpm = sum(m["rpm"] for m in members if m["tpm"] != 999999999)
        total_tpm = sum(m["tpm"] for m in members if m["tpm"] != 999999999)
        pools_data.append({
            "name": pool_name,
            "display_name": f"{pool_name} (Pool)",
            "members": [
                {
                    "alias": m.get("alias", m["model_id"]),
                    "model_id": m["model_id"],
                    "rpm": m["rpm"],
                    "tpm": m["tpm"],
                    "health_score": m.get("health_score", 100.0),
                }
                for m in members
            ],
            "models": ", ".join(m.get("alias", m["model_id"]) for m in members),
            "rpm": f"{total_rpm} RPM",
            "tpm": f"{total_tpm:,} TPM",
        })

    return {"pools": pools_data}


@app.get("/api/stats")
async def usage_stats(days: int = 30):
    stats = await get_stats(days)
    top_keys = await get_top_keys(days)
    
    try:
        accs = await asyncio.to_thread(list_accounts_db, True)
        prefix_to_acc = {}
        for a in accs:
            ak = a.get("auth_key", "")
            prefix = ak[-8:] if len(ak) >= 8 else ak
            prefix_to_acc[prefix] = {
                "name": a.get("name", "Unknown"),
                "full_key": ak
            }
        
        enriched_top_keys = []
        for tk in top_keys:
            pref = tk.get("key_prefix", "")
            if not pref:
                enriched_top_keys.append({
                    **tk,
                    "account_name": "System / Anonymous",
                    "full_key": "anonymous"
                })
                continue
                
            suffix = pref[-8:] if len(pref) >= 8 else pref
            acc_info = prefix_to_acc.get(suffix, {})
            
            if acc_info:
                name = acc_info.get("name", "Unknown")
                full_key = acc_info.get("full_key", f"sk-...{suffix}")
            else:
                name = "Auto Session"
                full_key = pref if pref.startswith("sk-") else f"sk-...{pref}"
                
            enriched_top_keys.append({
                **tk,
                "account_name": name,
                "full_key": full_key
            })
        top_keys = enriched_top_keys
    except Exception as e:
        import logging
        logging.getLogger("uvicorn").error("[Stats] Failed to enrich top_keys: %s", e)

    savings_data = _calculate_financial_savings(stats.get("summary", []))
    from src.core.usage_logger import get_recent_requests
    recent_reqs = await get_recent_requests(20)
    return {**stats, "top_keys": top_keys, "savings": savings_data, "recent_requests": recent_reqs}


@app.get("/api/help")
async def api_help(request: Request):
    """Hand-written API catalog, grouped by the dialect each endpoint speaks.

    The generated /openapi.json lists all 75 routes but carries no request
    bodies, because the handlers parse `request: Request` instead of declaring
    Pydantic models. This is the shape a reader actually needs: which protocol
    an endpoint speaks, what to send, and a curl that runs.

    Session-gated like the rest of the dashboard — it names routes and the auth
    model, which is not something to hand to an unauthenticated caller.
    """
    payload = _require_dashboard(request)
    if isinstance(payload, JSONResponse):
        return payload
    from src.server.openai_server.api_help import help_payload
    return help_payload()

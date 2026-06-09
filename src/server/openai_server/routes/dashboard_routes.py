import asyncio
import time
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import JSONResponse

from src.core.config_n_logg import config
from src.core.router import router
from src.core.limits import account_limiter
from src.backend.accounts import (
    find_account_by_key, find_account_by_name, list_accounts_db
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
    if not key:
        return JSONResponse(status_code=400, content={"error": "auth_key required"})
        
    if config.AUTH_TOKEN and key == config.AUTH_TOKEN:
        account = {
            "account_id": "admin",
            "name": "Administrator",
            "auth_key": config.AUTH_TOKEN,
            "tier": "admin"
        }
    else:
        account = await asyncio.to_thread(find_account_by_key, key)
        
    if not account:
        return JSONResponse(status_code=401, content={"error": "Invalid key"})
    token = _make_session_token(account)
    return {"token": token, "name": account.get("name"), "tier": account.get("tier", "free")}


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
        pool_stats[pkey]["rpd_left"] = min(p_rpd_left, pool_stats[pkey]["rpd_left"])
        
        for label, mins in [("1h", 60), ("12h", 720), ("24h", 1440)]:
            user_tokens_in_period = p_tpm_left + (mins - 1) * shown_tpm_limit
            pool_stats[pkey][f"tokens_{label}_left"] = min(int(user_tokens_in_period), pool_stats[pkey][f"tokens_{label}_left"])
            
            user_tokens_in_period_total = mins * shown_tpm_limit
            pool_stats[pkey][f"tokens_{label}_limit"] = min(int(user_tokens_in_period_total), pool_stats[pkey][f"tokens_{label}_limit"])
            
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


@app.get("/dashboard/my-stats")
async def dashboard_my_stats(request: Request, days: int = 30):
    payload = _require_dashboard(request)
    from src.core.usage_logger import get_stats_for_prefix
    account = await asyncio.to_thread(find_account_by_name, payload.get("name", ""))
    if not account:
        return {"summary": [], "daily": [], "total_requests": 0, "savings": {"savings": 0.0}}
    ak = account.get("auth_key", "")
    prefix = ak[-8:] if len(ak) >= 8 else ak
    res = await get_stats_for_prefix(prefix, days)
    savings_data = _calculate_financial_savings(res.get("summary", []))
    return {**res, "savings": savings_data}


@app.get("/api/model-pools-detail")
async def get_model_pools_api(request: Request):
    _require_dashboard(request)
    from src.core.api_config import AVAILABLE_MODELS, MODEL_POOLS, is_sunset_25

    pools_data = []
    for pool_name, pool_cfg in MODEL_POOLS.items():
        members = []
        for member in pool_cfg["members"]:
            if is_sunset_25() and member in ("gemini-flash-25", "gemini-flash-25-lite"):
                continue
            cfg = AVAILABLE_MODELS.get(member, {})
            members.append({
                "model_id": cfg.get("model_id", "unknown"),
                "rpm": cfg.get("rpm", 0),
                "tpm": cfg.get("tpm", 0),
            })
        total_rpm = sum(m["rpm"] for m in members)
        total_tpm = sum(m["tpm"] for m in members)
        pools_data.append({
            "name": pool_name,
            "display_name": f"{pool_name} (Pool)",
            "models": ", ".join(m["model_id"] for m in members),
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
    return {**stats, "top_keys": top_keys, "savings": savings_data}

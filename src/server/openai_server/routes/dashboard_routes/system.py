"""Dashboard system, account listing, metrics, pool and help routes."""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from src.core.config_n_logg import config
from src.core.limits import account_limiter
from src.core.accounts import account_manager
from src.backend.accounts.manager import find_account_by_name, list_accounts_db
from src.backend.keys.status import get_key_status_db, db_load_active_penalties
from src.backend.endpoints.manager import list_endpoints_db
from src.core.observability.usage_logger import get_stats, get_top_keys
from ..app_init import app
from ..auth_session import _require_dashboard, _require_admin
from .account import _calculate_financial_savings
@app.get("/dashboard/accounts")
async def dashboard_accounts(request: Request):
    payload = _require_dashboard(request)
    is_admin = payload.get("tier") == "admin"
    accs = await asyncio.to_thread(list_accounts_db, True)
    
    def mask_key(k: str) -> str:
        if not k: return ""
        return f"{k[:3]}...{k[-4:]}" if len(k) > 10 else "***"
        
    if is_admin:
        # Admin only. Whether somebody still owes a password change is not
        # something one account should learn about another's, so the flag is
        # merged in here rather than exposed on list_accounts_db for everyone.
        from src.backend.keys.accounts import list_password_flags_db
        flags = await asyncio.to_thread(list_password_flags_db)
        
        safe_admin = []
        for a in accs:
            d = dict(a)
            if "auth_key" in d:
                d["auth_key_masked"] = mask_key(d.pop("auth_key"))
            d["must_change_password"] = bool(flags.get(a["account_id"], 0))
            safe_admin.append(d)
            
        return {"accounts": safe_admin}
    else:
        safe = [{k: v for k, v in a.items() if k != "auth_key"} for a in accs]
        return {"accounts": safe}


@app.get("/dashboard/keys")
async def dashboard_keys(request: Request):
    payload = _require_dashboard(request)
    is_admin = payload.get("tier") == "admin"
    raw = await asyncio.to_thread(get_key_status_db)
    from src.core.limits.gemini.limiter import _key_usage, _usage_lock
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
    from src.core.config.api_config import MODEL_POOLS
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
            from src.backend.accounts.manager import list_accounts_db
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


ALLOWED_USER_LOG_CHANNELS = {"proxy", "api"}


@app.get("/dashboard/logs/history")
async def dashboard_logs_history(
    request: Request,
    channel: Optional[str] = None,
    file: Optional[str] = None,
    lines: int = 200,
):
    payload = _require_dashboard(request)
    raw_target = channel or file or "proxy"
    from ....realtime.log_watcher import log_watcher, normalize_channel
    logical_name = normalize_channel(raw_target) or "proxy"

    is_admin = payload.get("tier") == "admin"
    if not is_admin and logical_name not in ALLOWED_USER_LOG_CHANNELS:
        raise HTTPException(
            status_code=403,
            detail=f"Kênh log '{logical_name}' chỉ dành cho Quản trị viên (Admin). User thường chỉ có quyền xem 'proxy' và 'api'."
        )

    history = log_watcher.get_history(logical_name, max(1, min(5000, lines)))
    return {
        "channel": logical_name,
        "count": len(history),
        "history": history,
        "lines": history,
    }


@app.get("/dashboard/my-stats")
async def dashboard_my_stats(request: Request, days: int = 30):
    payload = _require_dashboard(request)
    from src.core.observability.usage_logger import get_stats_for_prefix
    from src.backend.keys.accounts import compose_token, list_keys_db
    account = await asyncio.to_thread(find_account_by_name, payload.get("name", ""))
    # Raw admin-key sessions use the synthetic "Administrator" identity and
    # therefore have no row in the accounts table. Keep their usage scoped to
    # the admin key instead of returning an empty My Usage panel.
    if not account and payload.get("tier") == "admin":
        account = {"auth_key": config.AUTH_TOKEN}
    if not account:
        return {"summary": [], "daily": [], "total_requests": 0, "savings": {"standard_cost": 0.0, "cached_cost": 0.0, "gemini_cost": 0.0, "net_savings": 0.0}}
    # Usage belongs to credentials, while the dashboard session identifies an
    # account. Include every credential owned by the account (including revoked
    # ones so historical usage remains visible).
    keys = await asyncio.to_thread(list_keys_db, account.get("account_id", ""), True)
    prefixes = [compose_token(account.get("name", ""), k.get("token_code", ""))[-8:]
                for k in keys if k.get("token_code")]
    legacy_key = str(account.get("auth_key") or "")
    if legacy_key:
        prefixes.append(legacy_key[-8:])
    res = await get_stats_for_prefix(prefixes, days)
    savings_data = _calculate_financial_savings(res.get("summary", []))
    return {**res, "savings": savings_data}


@app.get("/api/model-pools-detail")
async def get_model_pools_api(request: Request):
    _require_dashboard(request)
    from src.core.config.api_config import AVAILABLE_MODELS, MODEL_POOLS, is_sunset_25
    from src.core.router.core.router import router as core_router

    from src.core.limits.gemini.limiter import get_rate_limiter

    pools_data = []
    for pool_name, pool_cfg in MODEL_POOLS.items():
        members = []
        for member in pool_cfg["members"]:
            if is_sunset_25() and member in ("gemini-flash-25", "gemini-flash-25-lite"):
                continue
            cfg = AVAILABLE_MODELS.get(member, {})
            if not cfg.get("enabled", True):
                # Keep it in the payload, flagged off: the admin needs to see what
                # exists in order to turn it back on. Dropping it silently would
                # make a disabled model look deleted.
                members.append({
                    "model_id": cfg.get("model_id", member),
                    "alias": member,
                    "rpm": 0,
                    "tpm": 0,
                    "health_score": 0.0,
                    "enabled": False,
                })
                continue
            limiter = get_rate_limiter(member)
            backing_id = cfg.get("model_id", member)
            health = core_router._model_health.get(member, {})
            members.append({
                "model_id": backing_id,
                "alias": member,
                "rpm": limiter.rpm_limit,
                "tpm": limiter.tpm_limit,
                "health_score": health.get("score", 100.0) if isinstance(health, dict) else 100.0,
                "enabled": True,
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
async def usage_stats(request: Request, days: int = 30):
    _require_admin(request)
    stats = await get_stats(days)
    top_keys = await get_top_keys(days)
    
    try:
        accs = await asyncio.to_thread(list_accounts_db, True)
        prefix_to_name = {}
        for a in accs:
            ak = a.get("auth_key", "")
            prefix = ak[-8:] if len(ak) >= 8 else ak
            # Only the name is kept. This endpoint used to stash the whole
            # master key here and ship it as "full_key"; that is a credential
            # in a payload the dashboard polls every few seconds and holds in
            # browser state, so one screenshot of the analysis tab was a list
            # of master keys. Master keys are also quota-exempt, so holding one
            # is a full privilege escalation — not just a disclosure.
            # An operator who genuinely needs a key asks for it by account name
            # through GET /dashboard/admin/accounts/recovery, which is admin
            # only and takes two deliberate clicks in the UI.
            prefix_to_name[prefix] = a.get("name", "Unknown")
        
        enriched_top_keys = []
        for tk in top_keys:
            pref = tk.get("key_prefix", "")
            if not pref:
                enriched_top_keys.append({
                    **tk,
                    "account_name": "System / Anonymous",
                    "key_masked": "anonymous"
                })
                continue
                
            suffix = pref[-8:] if len(pref) >= 8 else pref
            name = prefix_to_name.get(suffix)
            if name is None:
                name = "Auto Session"
            
            enriched_top_keys.append({
                **tk,
                "account_name": name,
                "key_masked": f"sk-...{suffix}",
            })
        top_keys = enriched_top_keys
    except Exception as e:
        import logging
        logging.getLogger("uvicorn").error("[Stats] Failed to enrich top_keys: %s", e)

    savings_data = _calculate_financial_savings(stats.get("summary", []))
    from src.core.observability.usage_logger import get_recent_requests
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



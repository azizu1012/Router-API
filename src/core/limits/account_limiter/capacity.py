import time
import asyncio
from typing import Any, Dict

_active_counts_cache = {}
_active_counts_cache_ts = 0.0

async def get_active_account_counts() -> dict:
    global _active_counts_cache, _active_counts_cache_ts
    now = time.time()
    if not _active_counts_cache or now - _active_counts_cache_ts > 5.0:
        try:
            from src.backend.accounts import list_accounts_db
            accs = await asyncio.to_thread(list_accounts_db, False)
            counts = {"free": 0, "premium": 0, "admin": 0}
            for a in accs:
                tier = a.get("tier", "free")
                if tier in counts:
                    counts[tier] += 1
                else:
                    counts["free"] += 1
            _active_counts_cache = counts
            _active_counts_cache_ts = now
        except Exception:
            return {"free": 1, "premium": 0, "admin": 0}
    return _active_counts_cache


_key_capacities_cache = None
_key_capacities_cache_ts = 0.0

_key_capacities_pool_cache = {}
_key_capacities_pool_cache_ts = {}

def calculate_key_capacities_by_pool(pool_type: str = "flash") -> dict:
    global _key_capacities_pool_cache, _key_capacities_pool_cache_ts
    now = time.time()
    last_ts = _key_capacities_pool_cache_ts.get(pool_type, 0.0)
    if pool_type not in _key_capacities_pool_cache or now - last_ts > 1.0:
        try:
            from src.core.router.core import router
            from src.core.api_config import AVAILABLE_MODELS
            from src.core.limits.gemini_rate_limiter import _key_usage, _usage_lock, _key_model_requests, _key_model_tokens
            
            Cap_free_rpm = 0
            Cap_free_tpm = 0
            Cap_free_rpd = 0
            
            Cap_prem_rpm = 0
            Cap_prem_tpm = 0
            Cap_prem_rpd = 0
            
            Cap_admin_rpm = 0
            Cap_admin_tpm = 0
            Cap_admin_rpd = 0
            
            flash_models = {"gemini-3.5-flash", "gemini-3-flash-preview", "gemini-2.5-flash"}
            lite_models = {"gemini-3.1-flash-lite", "gemini-2.5-flash-lite"}
            
            with router._key_lock:
                key_statuses = list(router._key_status.items())
                
            for api_key, status in key_statuses:
                if not status.get("enabled", 1) or status.get("frozen_until", 0.0) > now:
                    continue
                key_tier = status.get("tier", "free")
                
                for alias, cfg in AVAILABLE_MODELS.items():
                    mid = cfg.get("model_id")
                    if not mid or mid == "gemini-flash-pool":
                        continue
                        
                    if pool_type == "flash" and mid not in flash_models:
                        continue
                    if pool_type == "lite" and mid not in lite_models:
                        continue
                        
                    pm_status = status.get("per_model", {}).get(mid, {})
                    if pm_status.get("frozen_until", 0.0) > now:
                        continue
                        
                    k_m = f"{api_key}::{mid}"
                    with _usage_lock:
                        reqs = _key_model_requests.get(k_m)
                        toks = _key_model_tokens.get(k_m)
                        rpm_used = sum(1 for ts in reqs if now - ts < 60) if reqs else 0
                        tpm_used = sum(t for ts, t in toks if now - ts < 60) if toks else 0
                        
                        entry = _key_usage.get(api_key, {})
                        rpd_used = entry.get("per_model", {}).get(mid, {}).get("today", 0)
                        
                    m_rpm = cfg.get("rpm", 5)
                    m_tpm = cfg.get("tpm", 250000)
                    m_rpd = cfg.get("rpd", 20)
                    
                    rpd_left = max(0, m_rpd - rpd_used)
                    rpm_left = max(0, m_rpm - rpm_used) if rpd_left > 0 else 0
                    tpm_left = max(0, m_tpm - tpm_used) if rpd_left > 0 else 0
                    
                    if key_tier == "free":
                        Cap_free_rpm += rpm_left
                        Cap_free_tpm += tpm_left
                        Cap_free_rpd += rpd_left
                    elif key_tier == "premium":
                        Cap_prem_rpm += rpm_left
                        Cap_prem_tpm += tpm_left
                        Cap_prem_rpd += rpd_left
                    elif key_tier == "admin":
                        Cap_admin_rpm += rpm_left
                        Cap_admin_tpm += tpm_left
                        Cap_admin_rpd += rpd_left
                        
            _key_capacities_pool_cache[pool_type] = {
                "free": {"rpm": Cap_free_rpm, "tpm": Cap_free_tpm, "rpd": Cap_free_rpd},
                "premium": {"rpm": Cap_prem_rpm, "tpm": Cap_prem_tpm, "rpd": Cap_prem_rpd},
                "admin": {"rpm": Cap_admin_rpm, "tpm": Cap_admin_tpm, "rpd": Cap_admin_rpd},
            }
            _key_capacities_pool_cache_ts[pool_type] = now
        except Exception:
            return {
                "free": {"rpm": 1, "tpm": 1, "rpd": 1},
                "premium": {"rpm": 1, "tpm": 1, "rpd": 1},
                "admin": {"rpm": 1, "tpm": 1, "rpd": 1},
            }
            
    return _key_capacities_pool_cache[pool_type]


def calculate_key_capacities() -> dict:
    global _key_capacities_cache, _key_capacities_cache_ts
    now = time.time()
    if _key_capacities_cache is None or now - _key_capacities_cache_ts > 1.0:
        try:
            from src.core.router.core import router
            from src.core.api_config import AVAILABLE_MODELS
            from src.core.limits.gemini_rate_limiter import _key_usage, _usage_lock, _key_model_requests, _key_model_tokens
            
            Cap_free_rpm = 0
            Cap_free_tpm = 0
            Cap_free_rpd = 0
            
            Cap_prem_rpm = 0
            Cap_prem_tpm = 0
            Cap_prem_rpd = 0
            
            Cap_admin_rpm = 0
            Cap_admin_tpm = 0
            Cap_admin_rpd = 0
            
            with router._key_lock:
                key_statuses = list(router._key_status.items())
                
            for api_key, status in key_statuses:
                if not status.get("enabled", 1) or status.get("frozen_until", 0.0) > now:
                    continue
                key_tier = status.get("tier", "free")
                
                for alias, cfg in AVAILABLE_MODELS.items():
                    mid = cfg.get("model_id")
                    if not mid or mid == "gemini-flash-pool":
                        continue
                        
                    pm_status = status.get("per_model", {}).get(mid, {})
                    if pm_status.get("frozen_until", 0.0) > now:
                        continue
                        
                    k_m = f"{api_key}::{mid}"
                    with _usage_lock:
                        reqs = _key_model_requests.get(k_m)
                        toks = _key_model_tokens.get(k_m)
                        rpm_used = sum(1 for ts in reqs if now - ts < 60) if reqs else 0
                        tpm_used = sum(t for ts, t in toks if now - ts < 60) if toks else 0
                        
                        entry = _key_usage.get(api_key, {})
                        rpd_used = entry.get("per_model", {}).get(mid, {}).get("today", 0)
                        
                    m_rpm = cfg.get("rpm", 5)
                    m_tpm = cfg.get("tpm", 250000)
                    m_rpd = cfg.get("rpd", 20)
                    
                    rpd_left = max(0, m_rpd - rpd_used)
                    rpm_left = max(0, m_rpm - rpm_used) if rpd_left > 0 else 0
                    tpm_left = max(0, m_tpm - tpm_used) if rpd_left > 0 else 0
                    
                    if key_tier == "free":
                        Cap_free_rpm += rpm_left
                        Cap_free_tpm += tpm_left
                        Cap_free_rpd += rpd_left
                    elif key_tier == "premium":
                        Cap_prem_rpm += rpm_left
                        Cap_prem_tpm += tpm_left
                        Cap_prem_rpd += rpd_left
                    elif key_tier == "admin":
                        Cap_admin_rpm += rpm_left
                        Cap_admin_tpm += tpm_left
                        Cap_admin_rpd += rpd_left
                        
            _key_capacities_cache = {
                "free": {"rpm": Cap_free_rpm, "tpm": Cap_free_tpm, "rpd": Cap_free_rpd},
                "premium": {"rpm": Cap_prem_rpm, "tpm": Cap_prem_tpm, "rpd": Cap_prem_rpd},
                "admin": {"rpm": Cap_admin_rpm, "tpm": Cap_admin_tpm, "rpd": Cap_admin_rpd},
            }
            _key_capacities_cache_ts = now
        except Exception:
            return {
                "free": {"rpm": 1, "tpm": 1, "rpd": 1},
                "premium": {"rpm": 1, "tpm": 1, "rpd": 1},
                "admin": {"rpm": 1, "tpm": 1, "rpd": 1},
            }
            
    return _key_capacities_cache

def calculate_pool_capacities_for_user(user_tier: str, active_counts: Dict[str, int], account: Dict[str, Any]) -> dict:
    from src.core.router.core import router
    from src.core.api_config import AVAILABLE_MODELS
    from src.core.limits.gemini_rate_limiter import _key_usage, _usage_lock, _key_model_requests, _key_model_tokens
    
    if user_tier == "admin":
        allowed_tiers = {"free", "premium", "admin"}
    elif user_tier == "premium":
        allowed_tiers = {"free", "premium"}
    else:
        allowed_tiers = {"free"}
        
    now = time.time()
    with router._key_lock:
        key_statuses = list(router._key_status.items())
        
    flash_models = {"gemini-3.5-flash", "gemini-3-flash-preview", "gemini-2.5-flash"}
    lite_models = {"gemini-3.1-flash-lite", "gemini-2.5-flash-lite"}
    
    pool_stats = {
        "flash": {
            "rpm_left": 0, "tpm_left": 0, "rpd_left": 0,
            "tokens_1h_left": 0, "tokens_12h_left": 0, "tokens_24h_left": 0,
            "tokens_1h_limit": 0, "tokens_12h_limit": 0, "tokens_24h_limit": 0,
            "rpm_limit": 0, "tpm_limit": 0, "rpd_limit": 0
        },
        "lite": {
            "rpm_left": 0, "tpm_left": 0, "rpd_left": 0,
            "tokens_1h_left": 0, "tokens_12h_left": 0, "tokens_24h_left": 0,
            "tokens_1h_limit": 0, "tokens_12h_limit": 0, "tokens_24h_limit": 0,
            "rpm_limit": 0, "tpm_limit": 0, "rpd_limit": 0
        }
    }
    
    for api_key, status in key_statuses:
        if not status.get("enabled", 1) or status.get("frozen_until", 0.0) > now:
            continue
        if status.get("tier", "free") not in allowed_tiers:
            continue
            
        for alias, cfg in AVAILABLE_MODELS.items():
            mid = cfg.get("model_id")
            if not mid or mid == "gemini-flash-pool":
                continue
                
            pool_key = None
            if mid in flash_models:
                pool_key = "flash"
            elif mid in lite_models:
                pool_key = "lite"
                
            if not pool_key:
                continue
                
            pm_status = status.get("per_model", {}).get(mid, {})
            if pm_status.get("frozen_until", 0.0) > now:
                continue
                
            k_m = f"{api_key}::{mid}"
            with _usage_lock:
                reqs = _key_model_requests.get(k_m)
                toks = _key_model_tokens.get(k_m)
                rpm_used = sum(1 for ts in reqs if now - ts < 60) if reqs else 0
                tpm_used = sum(t for ts, t in toks if now - ts < 60) if toks else 0
                
                entry = _key_usage.get(api_key, {})
                rpd_used = entry.get("per_model", {}).get(mid, {}).get("today", 0)
                
            m_rpm = cfg.get("rpm", 5)
            m_tpm = cfg.get("tpm", 250000)
            m_rpd = cfg.get("rpd", 20)
            
            rpd_left = max(0, m_rpd - rpd_used)
            rpm_left = max(0, m_rpm - rpm_used) if rpd_left > 0 else 0
            tpm_left = max(0, m_tpm - tpm_used) if rpd_left > 0 else 0
            
            pool_stats[pool_key]["rpm_limit"] += m_rpm
            pool_stats[pool_key]["tpm_limit"] += m_tpm
            pool_stats[pool_key]["rpd_limit"] += m_rpd
            
            pool_stats[pool_key]["rpm_left"] += rpm_left
            pool_stats[pool_key]["tpm_left"] += tpm_left
            pool_stats[pool_key]["rpd_left"] += rpd_left
            
            for label, mins in [("1h", 60), ("12h", 720), ("24h", 1440)]:
                tokens_capacity = tpm_left + (mins - 1) * m_tpm
                pool_stats[pool_key][f"tokens_{label}_left"] += int(tokens_capacity)
                
                tokens_capacity_total = mins * m_tpm
                pool_stats[pool_key][f"tokens_{label}_limit"] += int(tokens_capacity_total)
                
    return pool_stats

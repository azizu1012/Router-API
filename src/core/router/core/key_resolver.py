import time
import random
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from src.core.api_config import AVAILABLE_MODELS, MODEL_POOLS, is_sunset_25
from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_keys as logger
from src.backend.key_status import (
    get_key_tiers_db,
    atomic_reserve_key,
    atomic_freeze_key,
)
from src.core.limits.gemini_rate_limiter import (
    get_key_priority,
    check_key_model_limits,
    record_key_model_usage,
)

class KeyResolverMixin:

    def _key_is_circuit_open(self, key: str) -> bool:
        if not config.CIRCUIT_ENABLED:
            return False
        ks = self._key_status.get(key)
        if not ks:
            return False
        # If the key is no longer in its freeze period, the circuit is half-open (we can retry it)
        if time.time() >= ks.get("frozen_until", 0.0):
            return False
        return ks.get("consecutive_failures", 0) >= config.CIRCUIT_FAILURE_THRESHOLD

    @staticmethod
    def _adaptive_cooldown(reason: str, consecutive_failures: int) -> int:
        if reason == "rate_limit_rpd":
            tomorrow = datetime.now().date() + timedelta(days=1)
            reset_at = datetime.combine(tomorrow, datetime.min.time())
            return max(300, int((reset_at - datetime.now()).total_seconds()))
        if reason == "rate_limit" or reason == "429":
            base = config.KEY_429_COOLDOWN_SECONDS
            return min(base * (3 ** (consecutive_failures - 1)), 600)
        if reason in ("invalid", "invalid_key", "403", "401", "permission_denied"):
            return config.KEY_INVALID_COOLDOWN_SECONDS
        if reason == "timeout":
            return 60 * (2 ** min(consecutive_failures - 1, 3))
        if reason == "billing_error":
            return 300
        return config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS

    def _load_key_tiers(self) -> Dict[str, str]:
        try:
            db_tiers = get_key_tiers_db()
            tiers = {}
            for key in config.GEMINI_API_KEYS:
                tiers[key] = db_tiers.get(key, "free")
            return tiers
        except Exception:
            return {key: "free" for key in config.GEMINI_API_KEYS}

    @staticmethod
    def _get_allowed_tiers(account: Optional[Dict[str, Any]] = None) -> set:
        if not account:
            return {"free", "premium", "admin"}
        tier = account.get("tier", "free")
        if tier == "admin":
            return {"free", "premium", "admin"}
        if tier == "premium":
            return {"free", "premium"}
        return {"free"}

    def reserve_key(self, model_alias: str, model_id: Optional[str] = None, account: Optional[Dict[str, Any]] = None, estimated_tokens: int = 0, retry_attempt: int = 0, exclude_models: Optional[list] = None) -> Optional[Dict[str, Any]]:
        if self.is_global_cooldown_active():
            logger.warning("[Circuit] Global IP cooldown active. Blocking key reservation.")
            return None
        if is_sunset_25() and model_alias in ("gemini-flash-25", "gemini-flash-25-lite"):
            logger.warning("Model %s has been sunsetted. Blocking reservation.", model_alias)
            return None
        try:
            allowed_tiers = self._get_allowed_tiers(account)
            concrete_model_id = model_id or self.get_model_id(model_alias)

            pool_cfg = MODEL_POOLS.get(model_alias)
            if pool_cfg:
                members = [m for m in pool_cfg["members"] if not (is_sunset_25() and m in ("gemini-flash-25", "gemini-flash-25-lite"))]
                if exclude_models:
                    members = [m for m in members if m not in exclude_models and self.get_model_id(m) not in exclude_models]
                search_phases = ["standard"]

                for phase in search_phases:
                    for member in members:
                        mid = self.get_model_id(member)
                        with self._key_lock:
                            now = time.time()
                            candidates = []
                            cfg = AVAILABLE_MODELS.get(member, {})
                            
                            rpm_limit = int(cfg.get("rpm", 5))
                            tpm_limit = int(cfg.get("tpm", 250000))
 
                            selected_key = None
                            selected_mid = None
                            for k, s in self._key_status.items():
                                if s.get("enabled", 1) == 0:
                                    continue
                                if s.get("tier", "free") not in allowed_tiers:
                                    continue
 
                                # Enforce Key pool mappings
                                current_mid = mid
                                allowed_pools = s.get("allowed_pools")
                                if allowed_pools:
                                    p_set = set(str(p).strip().lower() for p in allowed_pools)
                                    ma = model_alias.strip().lower()
                                    cm = concrete_model_id.strip().lower()
                                    mb = member.strip().lower()
                                    if ma not in p_set and cm not in p_set and mb not in p_set:
                                        match_found = False
                                        for allowed in p_set:
                                            if allowed in ma or allowed in cm or allowed in mb or ma in allowed or cm in allowed or mb in allowed:
                                                match_found = True
                                                break
                                        if not match_found:
                                            continue
                                
                                # Apply extreme checking logic if retry_attempt is 10 or greater to avoid 429 cascades
                                if retry_attempt < 10:
                                    if s["frozen_until"] >= now:
                                        continue
                                    if self._key_is_circuit_open(k):
                                        continue
                                    # Auto-release stale active_requests: if key shows busy but last_success
                                    # was > 120s ago (request likely died), treat as idle.
                                    active_reqs = s.get("active_requests", 0)
                                    if active_reqs > 0:
                                        last_ok = s.get("last_success", 0.0)
                                        if now - last_ok > 120:
                                            s["active_requests"] = 0
                                        else:
                                            continue
                                else:
                                    # Extreme: do NOT bypass. Check strictly to prevent picking frozen/busy keys.
                                    if s["frozen_until"] >= now:
                                        continue
                                    if self._key_is_circuit_open(k):
                                        continue
                                    if s.get("active_requests", 0) > 0:
                                        continue
 
                                pm = s.get("per_model", {})
                                pm_entry = pm.get(current_mid, {})
                                
                                if retry_attempt < 10:
                                    if isinstance(pm_entry, dict) and pm_entry.get("frozen_until", 0) >= now:
                                        continue
                                    if not check_key_model_limits(k, current_mid, estimated_tokens, rpm_limit, tpm_limit):
                                        continue
                                else:
                                    if isinstance(pm_entry, dict) and pm_entry.get("frozen_until", 0) >= now:
                                        continue
                                    
                                    # Extra strict RPM and TPM checking for attempt >= 10
                                    from src.core.limits.gemini_rate_limiter import _key_model_requests, _key_model_tokens, _usage_lock
                                    k_m = f"{k}::{current_mid}"
                                    with _usage_lock:
                                        reqs = _key_model_requests.get(k_m)
                                        toks = _key_model_tokens.get(k_m)
                                        req_count = sum(1 for ts in reqs if now - ts < 60) if reqs else 0
                                        used_tokens = sum(t for ts, t in toks if now - ts < 60) if toks else 0
                                    
                                    # Check RPM with scaled limit (70% capacity)
                                    max_rpm = max(1, int(rpm_limit * 0.7))
                                    if req_count >= max_rpm:
                                        continue
                                    
                                    # Check TPM: if massive prompt exceeds normal tpm_limit, only allow if 100% idle
                                    max_tpm = int(tpm_limit * 0.7)
                                    if estimated_tokens <= tpm_limit:
                                        if used_tokens + estimated_tokens > max_tpm:
                                            continue
                                    else:
                                        if req_count > 0 or used_tokens > 0:
                                            continue
 
                                candidates.append((k, s, current_mid))
                            if candidates:
                                candidates_with_priority = []
                                for k, s, key_mid in candidates:
                                    pri = get_key_priority(k, key_mid)
                                    if pri < 0:
                                        continue
                                    candidates_with_priority.append((pri, k, s, key_mid))
                                
                                if not candidates_with_priority:
                                    continue
 
                                candidates_with_priority.sort(key=lambda x: (x[2].get("active_requests", 0), -x[0], x[2].get("consecutive_failures", 0)))
                                if not candidates_with_priority:
                                    continue
                                pool_top = min(10, len(candidates_with_priority))
                                chosen_cand = random.choice(candidates_with_priority[:pool_top])
                                selected_key = chosen_cand[1]
                                selected_mid = chosen_cand[3]
                                
                                self._key_status[selected_key]["usage"] += 1
                                self._key_status[selected_key]["active_requests"] += 1
                        if candidates and selected_key:
                            try:
                                atomic_reserve_key(selected_key)
                            except Exception:
                                with self._key_lock:
                                    if selected_key in self._key_status:
                                        self._key_status[selected_key]["usage"] = max(0, self._key_status[selected_key]["usage"] - 1)
                                        self._key_status[selected_key]["active_requests"] = max(0, self._key_status[selected_key]["active_requests"] - 1)
                                continue
                            record_key_model_usage(selected_key, selected_mid, estimated_tokens)
                            return {
                                "key": selected_key,
                                "model_alias": member,
                                "model_id": selected_mid,
                                "provider": "gemini",
                            }
                logger.warning("All keys frozen or rate limited for pool %s members=%s", model_alias, pool_cfg["members"])
                return None

            search_phases = ["standard"]

            for phase in search_phases:
                with self._key_lock:
                    now = time.time()
                    candidates = []
                    cfg = AVAILABLE_MODELS.get(model_alias, {})
                    if not cfg:
                        for alias, val in AVAILABLE_MODELS.items():
                            if val.get("model_id") == concrete_model_id:
                                cfg = val
                                break
                    rpm_limit = int(cfg.get("rpm", 5)) if cfg else 5
                    tpm_limit = int(cfg.get("tpm", 250000)) if cfg else 250000

                    selected_key = None
                    selected_mid = None
                    for k, s in self._key_status.items():
                        if s.get("enabled", 1) == 0:
                            continue
                        if s.get("tier", "free") not in allowed_tiers:
                            continue

                        # Enforce Key pool mappings
                        current_mid = concrete_model_id
                        allowed_pools = s.get("allowed_pools")
                        if allowed_pools:
                            p_set = set(str(p).strip().lower() for p in allowed_pools)
                            ma = model_alias.strip().lower()
                            cm = concrete_model_id.strip().lower()
                            if ma not in p_set and cm not in p_set:
                                match_found = False
                                for allowed in p_set:
                                    if allowed in ma or allowed in cm or ma in allowed or cm in allowed:
                                        match_found = True
                                        break
                                if not match_found:
                                    continue
                        
                        # Apply extreme checking logic if retry_attempt is 10 or greater to avoid 429 cascades
                        if retry_attempt < 10:
                            if s["frozen_until"] >= now:
                                continue
                            if self._key_is_circuit_open(k):
                                continue
                            # Auto-release stale active_requests
                            active_reqs = s.get("active_requests", 0)
                            if active_reqs > 0:
                                last_ok = s.get("last_success", 0.0)
                                if now - last_ok > 120:
                                    s["active_requests"] = 0
                                else:
                                    continue
                        else:
                            # Extreme: do NOT bypass. Check strictly to prevent picking frozen/busy keys.
                            if s["frozen_until"] >= now:
                                continue
                            if self._key_is_circuit_open(k):
                                continue
                            if s.get("active_requests", 0) > 0:
                                continue

                        pm = s.get("per_model", {})
                        pm_entry = pm.get(current_mid, {})
                        
                        if retry_attempt < 10:
                            if isinstance(pm_entry, dict) and pm_entry.get("frozen_until", 0) >= now:
                                continue
                            if not check_key_model_limits(k, current_mid, estimated_tokens, rpm_limit, tpm_limit):
                                continue
                        else:
                            if isinstance(pm_entry, dict) and pm_entry.get("frozen_until", 0) >= now:
                                continue
                            
                            # Extra strict RPM and TPM checking for attempt >= 10
                            from src.core.limits.gemini_rate_limiter import _key_model_requests, _key_model_tokens, _usage_lock
                            k_m = f"{k}::{current_mid}"
                            with _usage_lock:
                                reqs = _key_model_requests.get(k_m)
                                toks = _key_model_tokens.get(k_m)
                                req_count = sum(1 for ts in reqs if now - ts < 60) if reqs else 0
                                used_tokens = sum(t for ts, t in toks if now - ts < 60) if toks else 0
                            
                            # Check RPM with scaled limit (70% capacity)
                            max_rpm = max(1, int(rpm_limit * 0.7))
                            if req_count >= max_rpm:
                                continue
                            
                            # Check TPM: if massive prompt exceeds normal tpm_limit, only allow if 100% idle
                            max_tpm = int(tpm_limit * 0.7)
                            if estimated_tokens <= tpm_limit:
                                if used_tokens + estimated_tokens > max_tpm:
                                    continue
                            else:
                                if req_count > 0 or used_tokens > 0:
                                    continue

                        candidates.append((k, s, current_mid))

                    if candidates:
                        candidates_with_priority = []
                        for k, s, key_mid in candidates:
                            pri = get_key_priority(k, key_mid)
                            if pri < 0:
                                continue
                            candidates_with_priority.append((pri, k, s, key_mid))
                        
                        if not candidates_with_priority:
                            continue

                        random.shuffle(candidates_with_priority)
                        candidates_with_priority.sort(key=lambda x: (x[2].get("active_requests", 0), -x[0], x[2].get("consecutive_failures", 0)))
                        if not candidates_with_priority:
                            continue
                        pool_top = min(10, len(candidates_with_priority))
                        chosen_cand = random.choice(candidates_with_priority[:pool_top])
                        selected_key = chosen_cand[1]
                        selected_mid = chosen_cand[3]
                            
                        self._key_status[selected_key]["usage"] += 1
                        self._key_status[selected_key]["active_requests"] += 1
                if candidates and selected_key:
                    try:
                        atomic_reserve_key(selected_key)
                    except Exception:
                        with self._key_lock:
                            if selected_key in self._key_status:
                                self._key_status[selected_key]["usage"] = max(0, self._key_status[selected_key]["usage"] - 1)
                                self._key_status[selected_key]["active_requests"] = max(0, self._key_status[selected_key]["active_requests"] - 1)
                        continue
                    record_key_model_usage(selected_key, selected_mid, estimated_tokens)
                    return {
                        "key": selected_key,
                        "model_alias": model_alias,
                        "model_id": selected_mid,
                        "provider": "gemini",
                    }

            logger.warning("All keys are frozen or rate limited for model %s", concrete_model_id)
            return None
        except Exception as exc:
            logger.error("reserve_key error: %s", exc)
            return None

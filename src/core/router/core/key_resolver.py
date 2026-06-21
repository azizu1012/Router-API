# pyright: reportAttributeAccessIssue=false

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
)
from src.core.limits.gemini_rate_limiter import (
    get_key_priority,
    check_key_model_limits,
    record_key_model_usage,
)

# No global cache variables needed

class KeyResolverMixin:
    """
    Mixin class providing key resolution logic, including circuit breaker patterns,
    adaptive cooldown strategies, and priority-based key selection.
    This mixin is intended to be used by classes like APIRouter to manage API keys effectively.
    """
    def _key_is_circuit_open(self, key: str) -> bool:
        """
        Determines if the circuit breaker is open for a given key.
        A key's circuit is considered open if it has accumulated enough consecutive failures
        and is currently within its frozen period.
        """        if not config.CIRCUIT_ENABLED:
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
        """
        Calculates an adaptive cooldown duration based on the failure reason and
        the number of consecutive failures. This helps to prevent overwhelming
        APIs with retries after repeated failures.
        """        if reason in ("rate_limit_rpd", "project_quota_429"):
            from src.core.limits.gemini_rate_limiter import get_seconds_until_pacific_midnight
            return get_seconds_until_pacific_midnight()
        if reason == "rate_limit" or reason == "429":
            base = config.KEY_429_COOLDOWN_SECONDS
            return min(base * (3 ** (consecutive_failures - 1)), config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS * 10)
        if reason in ("invalid", "invalid_key", "403", "401", "permission_denied"):
            return config.KEY_INVALID_COOLDOWN_SECONDS
        if reason == "timeout" or reason == "unavailable":
            return config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS * (2 ** min(consecutive_failures - 1, 3))
        if reason == "billing_error":
            return config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS * 10
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

    def _get_model_limits(self, model_alias: str, concrete_model_id: str) -> tuple:
        cfg = AVAILABLE_MODELS.get(model_alias, {})
        if not cfg:
            for alias, val in AVAILABLE_MODELS.items():
                if val.get("model_id") == concrete_model_id:
                    cfg = val
                    break
        rpm_limit = int(cfg.get("rpm", 5)) if cfg else 5
        tpm_limit = int(cfg.get("tpm", 250000)) if cfg else 250000
        return rpm_limit, tpm_limit

    def _is_key_eligible(
        self,
        key: str,
        status: Dict[str, Any],
        model_id: str,
        model_alias: str,
        estimated_tokens: int,
        rpm_limit: int,
        tpm_limit: int,
        retry_attempt: int,
        allowed_tiers: set,
        now: float,
        pool_member_alias: Optional[str] = None
    ) -> bool:
        """
        Checks if a given API key is eligible for use based on various criteria.
        This includes checking key status, tier, pool assignments, cooldown, circuit breaker state,
        active requests, and rate limits (RPM/TPM).

        Args:
            key: The API key string.
            status: Current status dictionary of the key.
            model_id: The concrete model ID being requested.
            model_alias: The alias of the model being requested.
            estimated_tokens: Estimated number of tokens for the current request.
            rpm_limit: Requests per minute limit for the model.
            tpm_limit: Tokens per minute limit for the model.
            retry_attempt: Current retry attempt count.
            allowed_tiers: Set of tiers allowed for the current account.
            now: Current timestamp.
            pool_member_alias: Alias of the specific pool member if in pool mode.

        Returns:
            True if the key is eligible, False otherwise.
        """
        if status.get("enabled", 1) == 0:
            return False
        if status.get("tier", "free") not in allowed_tiers:
            return False

        # Checks the `allowed_pools` for the key
        allowed_pools = status.get("allowed_pools")
        if allowed_pools:
            p_set = set(str(p).strip().lower() for p in allowed_pools)
            ma = model_alias.strip().lower()
            cm = model_id.strip().lower()
            mb = pool_member_alias.strip().lower() if pool_member_alias else None
            
            is_in_set = (ma in p_set) or (cm in p_set) or (mb and mb in p_set)
            if not is_in_set:
                match_found = False
                for allowed in p_set:
                    if (allowed in ma) or (allowed in cm) or (mb and allowed in mb) or \
                       (ma in allowed) or (cm in allowed) or (mb and mb in allowed):
                        match_found = True
                        break
                if not match_found:
                    return False

        # Applies cooldown and active requests check logic
        if retry_attempt < 10:
            if status["frozen_until"] >= now:
                return False
            if self._key_is_circuit_open(key):
                return False
            
            # Emergency auto-release of stale active_requests
            auto_release_after = config.KEY_429_COOLDOWN_SECONDS * 5
            active_reqs = status.get("active_requests", 0)
            if active_reqs > 0:
                last_ok = status.get("last_success", 0.0)
                if now - last_ok > auto_release_after:
                    status["active_requests"] = 0
                else:
                    return False
        else:
            # Extreme Checking (high load, attempt >= 10)
            if status["frozen_until"] >= now:
                return False
            if self._key_is_circuit_open(key):
                return False
            if status.get("active_requests", 0) > 0:
                return False

        pm = status.get("per_model", {})
        pm_entry = pm.get(model_id, {})
        if isinstance(pm_entry, dict) and pm_entry.get("frozen_until", 0) >= now:
            return False

        # Checks RPM/TPM limits for the key
        if retry_attempt < 10:
            if not check_key_model_limits(key, model_id, estimated_tokens, rpm_limit, tpm_limit):
                return False
        else:
            # Tightens limits to 70% of actual capacity to prevent 429 cascades
            from src.core.limits.gemini_rate_limiter import _key_model_requests, _key_model_tokens, _usage_lock
            k_m = f"{key}::{model_id}"
            with _usage_lock:
                reqs = _key_model_requests.get(k_m)
                toks = _key_model_tokens.get(k_m)
                req_count = sum(1 for ts in reqs if now - ts < 60) if reqs else 0
                used_tokens = sum(t for ts, t in toks if now - ts < 60) if toks else 0

            max_rpm = max(1, int(rpm_limit * 0.7))
            if req_count >= max_rpm:
                return False

            max_tpm = int(tpm_limit * 0.7)
            if estimated_tokens <= tpm_limit:
                if used_tokens + estimated_tokens > max_tpm:
                    return False
            else:
                if req_count > 0 or used_tokens > 0:
                    return False

        return True

    def _select_key_double_random(self, candidates: list) -> Optional[tuple]:
        """
        Selects an API key from a list of eligible candidates using a "Double Random" strategy.
        This strategy aims to distribute load more evenly across healthy keys and prevent
        "Thundering Herd" issues where many requests target the single "best" key,
        leading to cascading 429 errors.

        The selection process involves:
        1. Calculating a priority score for each candidate key.
        2. Sorting candidates based on active requests, priority score, and consecutive failures.
        3. Randomly selecting a key from the top 50% of the sorted, healthiest candidates.

        Args:
            candidates: A list of (key, status, model_id) tuples that are eligible.

        Returns:
            A tuple of (selected_key, selected_model_id) or None if no key can be selected.
        """
        candidates_with_priority = []
        for k, s, key_mid in candidates:
            pri = get_key_priority(k, key_mid)
            if pri < 0:
                continue
            candidates_with_priority.append((pri, k, s, key_mid))
        
        if not candidates_with_priority:
            return None

        # Sorts by priority: fewest active_requests -> highest priority score -> fewest failures
        candidates_with_priority.sort(key=lambda x: (x[2].get("active_requests", 0), -x[0], x[2].get("consecutive_failures", 0)))
        
        # Randomly selects from the top 50% healthiest keys (Double Random)
        top_50_percent = int(len(candidates_with_priority) * 0.5)
        chosen_cand = random.choice(candidates_with_priority[:max(1, top_50_percent)])
        return chosen_cand[1], chosen_cand[3]

    def reserve_key(
        self,
        model_alias: str,
        model_id: Optional[str] = None,
        account: Optional[Dict[str, Any]] = None,
        estimated_tokens: int = 0,
        retry_attempt: int = 0,
        exclude_models: Optional[list] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Reserves an API key for a given model and request, applying all eligibility checks.
        This is the core method for key selection, incorporating model pools, key tiers,
        rate limits, circuit breakers, and the Double Random selection algorithm.

        Args:
            model_alias: The alias of the model being requested.
            model_id: The concrete model ID being requested (optional, if known).
            account: The account making the request (optional).
            estimated_tokens: Estimated number of tokens for the current request.
            retry_attempt: Current retry attempt count, influences extreme checking logic.
            exclude_models: List of model IDs to exclude from consideration (optional).

        Returns:
            A dictionary containing the selected key, model alias, model ID, and provider,
            or None if no suitable key can be reserved after all attempts.

        Design Decision (Coupling & Complexity):
        This method exhibits a degree of coupling with `APIRouter` (which calls it)
        and `src.logical_HQ_translator` (via `_resolve_model` implicitly or explicitly).
        This design choice prioritizes practical, fast integration of complex model resolution
        and key management logic into a central flow, reducing boilerplate and accelerating
        feature development, even if it deviates from strict layered architecture principles.
        The nested loops and explicit DB transactions are a trade-off for resilience and
        fine-grained control in a high-concurrency, high-failure-rate environment.
        """
        Dự trữ và phân bổ API Key tối ưu dựa trên Model Pool, độ ưu tiên (Priority),
        hạn mức RPM/TPM, trạng thái Circuit Breaker, và thuật toán Double Random.
        """
        # --- BƯỚC 1: KIỂM TRA COOLDOWN TOÀN CỤC ---
        if self.is_global_cooldown_active():
            logger.warning("[Circuit] Global IP cooldown active. Blocking key reservation.")
            return None

        # Bảo vệ chống các model đã bị Google khai tử (Sunsetted)
        if is_sunset_25() and model_alias in ("gemini-flash-25", "gemini-flash-25-lite"):
            logger.warning("Model %s has been sunsetted. Blocking reservation.", model_alias)
            return None

        try:
            # --- BƯỚC 2: PHÂN QUYỀN TRUY CẬP TIER ---
            allowed_tiers = self._get_allowed_tiers(account)
            concrete_model_id = model_id or self.get_model_id(model_alias)

            pool_cfg = MODEL_POOLS.get(model_alias)
            if pool_cfg:
                members = [m for m in pool_cfg["members"] if not (is_sunset_25() and m in ("gemini-flash-25", "gemini-flash-25-lite"))]
                if exclude_models:
                    members = [m for m in members if m not in exclude_models and self.get_model_id(m) not in exclude_models]
                members = self.get_healthy_pool_members(members)
            else:
                members = [model_alias]

            search_phases = ["standard"]
            for phase in search_phases:
                for member in members:
                    mid = self.get_model_id(member)
                    rpm_limit, tpm_limit = self._get_model_limits(member, mid)
                    
                    with self._key_lock:
                        now = time.time()
                        candidates = []
                        for k, s in self._key_status.items():
                            if self._is_key_eligible(
                                key=k,
                                status=s,
                                model_id=mid,
                                model_alias=model_alias,
                                estimated_tokens=estimated_tokens,
                                rpm_limit=rpm_limit,
                                tpm_limit=tpm_limit,
                                retry_attempt=retry_attempt,
                                allowed_tiers=allowed_tiers,
                                now=now,
                                pool_member_alias=member if pool_cfg else None
                            ):
                                candidates.append((k, s, mid))
                        
                        if not candidates:
                            continue

                        chosen = self._select_key_double_random(candidates)
                        if not chosen:
                            continue

                        selected_key, selected_mid = chosen
                        self._key_status[selected_key]["usage"] += 1
                        self._key_status[selected_key]["active_requests"] += 1

                    # Commits outside the lock to avoid holding the thread lock during DB communication
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

            if pool_cfg:
                logger.warning("All keys frozen or rate limited for pool %s members=%s", model_alias, pool_cfg["members"])
            else:
                logger.warning("All keys are frozen or rate limited for model %s", concrete_model_id)
            return None
        except Exception as exc:
            logger.error("reserve_key error: %s", exc)
            return None

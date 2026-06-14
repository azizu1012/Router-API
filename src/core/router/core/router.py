import random
import threading
import time
from typing import Any, Dict, List, Optional

from src.core.api_config import AVAILABLE_MODELS, MODEL_POOLS, MODEL_PRIORITY, is_sunset_25
from src.core.router.pool import ModelPool
from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_keys as logger
from src.core.limits.gemini_rate_limiter import (
    get_rate_limiter, record_key_usage,
)
from src.backend.key_status import (
    get_key_status_db,
    atomic_release_key,
    atomic_freeze_key,
    atomic_record_success,
)
from .key_resolver import KeyResolverMixin

class APIRouter(KeyResolverMixin):
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if APIRouter._initialized:
            return

        self.model_priority = [alias for alias in MODEL_PRIORITY if alias in AVAILABLE_MODELS]
        self.current_model = config.DEFAULT_MODEL_ALIAS if config.DEFAULT_MODEL_ALIAS in AVAILABLE_MODELS else self.model_priority[0]
        self._model_lock = threading.Lock()

        all_model_ids = list(dict.fromkeys(
            str(cfg["model_id"]) for alias, cfg in AVAILABLE_MODELS.items()
            if cfg["model_id"] != "gemini-flash-pool"
        ))

        self._key_lock = threading.RLock()

        all_keys = list(config.GEMINI_API_KEYS)
        loaded = get_key_status_db()

        default_status = {
            "enabled": 1,
            "usage": 0,
            "active_requests": 0,
            "frozen_until": 0.0,
            "consecutive_failures": 0,
            "last_success": 0.0,
            "per_model": {mid: {"failures": 0, "frozen_until": 0.0} for mid in all_model_ids},
        }
        self._key_status = {}
        for key in all_keys:
            entry = loaded.get(key)
            if entry and isinstance(entry, dict):
                entry.setdefault("per_model", {})
                for mid in all_model_ids:
                    entry["per_model"].setdefault(mid, {"failures": 0, "frozen_until": 0.0})
                for field in default_status:
                    entry.setdefault(field, default_status[field])
                if "allowed_pools" in entry:
                    entry["allowed_pools"] = entry["allowed_pools"]
                self._key_status[key] = entry
            else:
                status_dict = dict(default_status)
                self._key_status[key] = status_dict

        self.total_requests = 0
        self.total_errors = 0
        self._circuit_state = "closed"
        self.global_cooldown_until: float = 0.0
        self._consecutive_429_count: int = 0
        self._circuit_open_until = 0.0

        logger.info(
            "APIRouter initialized models=%s keys=%d (API: %d)",
            ", ".join(self.model_priority),
            len(self._key_status),
            len(config.GEMINI_API_KEYS),
        )
        APIRouter._initialized = True

    def refresh_keys(self):
        with self._key_lock:
            all_model_ids = list(dict.fromkeys(
                str(cfg["model_id"]) for alias, cfg in AVAILABLE_MODELS.items()
                if cfg["model_id"] != "gemini-flash-pool"
            ))

            all_keys = list(config.GEMINI_API_KEYS)
            loaded = get_key_status_db()

            default_status = {
                "enabled": 1,
                "usage": 0,
                "active_requests": 0,
                "frozen_until": 0.0,
                "consecutive_failures": 0,
                "last_success": 0.0,
                "per_model": {mid: {"failures": 0, "frozen_until": 0.0} for mid in all_model_ids},
            }

            new_key_status = {}
            for key in all_keys:
                entry = loaded.get(key)
                if not (entry and isinstance(entry, dict)):
                    status_dict = dict(default_status)
                    new_key_status[key] = status_dict
                    continue

                if key in self._key_status:
                    existing = self._key_status[key]
                    existing["enabled"] = entry.get("enabled", existing.get("enabled", 1))
                    existing["frozen_until"] = entry.get("frozen_until", existing["frozen_until"])
                    existing["consecutive_failures"] = entry.get("consecutive_failures", existing["consecutive_failures"])
                    existing["last_success"] = entry.get("last_success", existing["last_success"])
                    existing["tier"] = entry.get("tier", existing.get("tier", "free"))
                    
                    if "allowed_pools" in entry:
                        existing["allowed_pools"] = entry["allowed_pools"]
                        
                    db_pm = entry.get("per_model", {})
                    if isinstance(db_pm, dict):
                        existing.setdefault("per_model", {})
                        for mid, m_entry in db_pm.items():
                            if isinstance(m_entry, dict):
                                existing["per_model"].setdefault(mid, {}).update(m_entry)
                    for mid in all_model_ids:
                        existing["per_model"].setdefault(mid, {"failures": 0, "frozen_until": 0.0})
                    new_key_status[key] = existing
                else:
                    entry.setdefault("per_model", {})
                    for mid in all_model_ids:
                        entry["per_model"].setdefault(mid, {"failures": 0, "frozen_until": 0.0})
                    for field in default_status:
                        entry.setdefault(field, default_status[field])
                    if "allowed_pools" in entry:
                        entry["allowed_pools"] = entry["allowed_pools"]
                    new_key_status[key] = entry

            self._key_status = new_key_status
            logger.info("APIRouter keys refreshed in-memory. Total keys: %d (API: %d)", len(self._key_status), len(config.GEMINI_API_KEYS))

    def list_models(self) -> List[Dict[str, Any]]:
        models = []
        for alias, cfg in AVAILABLE_MODELS.items():
            if cfg.get("hidden"):
                continue
            if is_sunset_25() and alias in ("gemini-flash-25", "gemini-flash-25-lite"):
                continue
            m = {
                "id": alias,
                "object": "model",
                "created": 0,
                "owned_by": "router_api",
                "root": cfg["model_id"],
                "display": cfg["display"],
            }
            cl = cfg.get("context_length")
            if cl:
                m["context_length"] = cl
            models.append(m)
        return models

    def resolve_model_alias(self, model: Optional[str]) -> str:
        raw = (model or "").strip().lower()
        if raw in AVAILABLE_MODELS:
            return raw
        for alias, cfg in AVAILABLE_MODELS.items():
            if raw == cfg.get("model_id"):
                return alias
        from src.core.providers import _custom_endpoint_manager
        for ep in _custom_endpoint_manager.list_endpoints():
            if raw in ep.get("models", []):
                return raw
        
        if "haiku" in raw:
            return "gemini-flash-lite"
        if "sonnet" in raw or "opus" in raw:
            return "gemini-flash"
            
        return self.current_model

    def get_model_id(self, alias: str) -> str:
        cfg = AVAILABLE_MODELS.get(alias)
        if cfg:
            return str(cfg.get("model_id", alias))
        from src.core.providers import _custom_endpoint_manager
        for ep in _custom_endpoint_manager.list_endpoints():
            if alias in ep.get("models", []):
                return alias
        return alias

    def resolve_pool(self, alias: str) -> Optional[ModelPool]:
        pool_cfg = MODEL_POOLS.get(alias)
        if pool_cfg:
            members = [m for m in pool_cfg["members"] if not (is_sunset_25() and m in ("gemini-flash-25", "gemini-flash-25-lite"))]
            # Account-dedicated endpoints are resolved per-account, not per-pool
            if not members:
                return None
            pool_cfg = {
                **pool_cfg,
                "members": members,
                "swap_failures": config.POOL_SWAP_FAILURES,
                "max_retry_seconds": config.POOL_RETRY_SECONDS,
            }
            return ModelPool(pool_cfg)
        return None

    def is_global_cooldown_active(self) -> bool:
        return time.time() < self.global_cooldown_until

    def record_429(self) -> bool:
        with self._key_lock:
            if self.is_global_cooldown_active():
                return False
            self._consecutive_429_count += 1
            if self._consecutive_429_count >= 15:
                self.global_cooldown_until = time.time() + random.uniform(10, 20)
                self._consecutive_429_count = 0
                return True
            return False

    def reset_429_counter(self) -> None:
        with self._key_lock:
            self._consecutive_429_count = 0

    def record_success(self, key: Optional[str] = None, model_id: Optional[str] = None, input_tokens: int = 0, output_tokens: int = 0) -> None:
        try:
            self.total_requests += 1
            self.reset_429_counter()
            if key:
                record_key_usage(key, model_id)
                atomic_record_success(key, model_id)
            with self._key_lock:
                if key and key in self._key_status:
                    self._key_status[key]["consecutive_failures"] = 0
                    self._key_status[key]["last_success"] = time.time()
                    # Cập nhật usage tokens
                    self._key_status[key]["input_tokens"] = self._key_status[key].get("input_tokens", 0) + input_tokens
                    self._key_status[key]["output_tokens"] = self._key_status[key].get("output_tokens", 0) + output_tokens
                    
                    if model_id and model_id in self._key_status[key]["per_model"]:
                        self._key_status[key]["per_model"][model_id]["failures"] = 0
        except Exception as exc:
            logger.error("record_success error: %s", exc)

    def record_failure(self, reason: str = "error") -> None:
        try:
            self.total_errors += 1
        except Exception as exc:
            logger.error("record_failure error: %s", exc)

    async def acquire_quota(self, reserved_tokens: int, model_alias: str, **kwargs) -> bool:
        return await get_rate_limiter(model_alias).acquire_quota(reserved_tokens)

    def freeze_key(self, key: str, duration: int, model_id: Optional[str] = None, reason: str = "rate_limit") -> None:
        try:
            with self._key_lock:
                if key not in self._key_status:
                    return
                
                if reason in ("invalid_key", "permission_denied", "billing_error"):
                    logger.error("[APIRouter] Permanent failure detected on key ...%s (Reason: %s). Disabling key vĩnh viễn.", key[-8:], reason)
                    self._key_status[key]["enabled"] = 0
                    from src.backend.key_status import atomic_disable_key
                    atomic_disable_key(key)
                    from src.core.config_n_logg.config import remove_banned_key_from_env
                    remove_banned_key_from_env(key)
                    return

                cf = self._key_status[key].get("consecutive_failures", 0)
                if reason != "bad_request_spam_prevent":
                    cf += 1
                adj_duration = self._adaptive_cooldown(reason, cf)
                jitter = random.uniform(0, adj_duration * 0.15) + abs(random.gauss(0, adj_duration * 0.05))
                adj_duration = int(adj_duration + jitter)
                until_ts = time.time() + adj_duration
                self._key_status[key]["consecutive_failures"] = cf
                ks = self._key_status[key]
                if model_id:
                    pm = ks.setdefault("per_model", {})
                    pm_entry = pm.setdefault(model_id, {"failures": 0, "frozen_until": 0.0})
                    pm_entry["frozen_until"] = until_ts
                    pm_entry["failures"] = pm_entry.get("failures", 0) + 1
                else:
                    ks["frozen_until"] = until_ts
            atomic_freeze_key(key, until_ts, model_id, cf)
        except Exception as exc:
            logger.error("freeze_key error: %s", exc)


    def release_key(self, key: str) -> None:
        try:
            with self._key_lock:
                if key in self._key_status:
                    self._key_status[key]["active_requests"] = max(0, self._key_status[key].get("active_requests", 1) - 1)
            atomic_release_key(key)
        except Exception as exc:
            logger.error("release_key error: %s", exc)

    def get_pool_custom_models(self, pool_name: str) -> List[Dict[str, Any]]:
        from src.core.providers import _custom_endpoint_manager
        results = []
        for ep in _custom_endpoint_manager.list_endpoints():
            if not ep.get("enabled", True):
                continue
            pool_assignments = ep.get("pool_assignments", {})
            if pool_name in pool_assignments:
                model_id = pool_assignments[pool_name]
                enabled_models = ep.get("enabled_models", [])
                if model_id in enabled_models:
                    results.append({"endpoint": ep, "model_id": model_id})
        return results

    def freeze_all_keys(self, duration: int) -> None:
        try:
            now = time.time()
            with self._key_lock:
                for key in self._key_status:
                    jitter = random.uniform(0, duration * 0.15) + abs(random.gauss(0, duration * 0.05))
                    effective = now + int(duration + jitter)
                    self._key_status[key]["frozen_until"] = effective
                    atomic_freeze_key(key, effective)
        except Exception as exc:
            logger.error("freeze_all_keys error: %s", exc)

router = APIRouter()

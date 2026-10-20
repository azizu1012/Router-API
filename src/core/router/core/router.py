import random
import threading
import uuid
import time
from typing import Any, Dict, List, Optional, Set

from src.core.config.api_config import (
    AVAILABLE_MODELS,
    MODEL_POOLS,
    MODEL_PRIORITY,
    active_pool_members,
    is_sunset_25,
)
from src.core.router.pool import ModelPool
from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_keys as logger
from src.core.limits.gemini.limiter import (
    get_rate_limiter, record_key_usage,
)
from src.backend.keys.status import (
    get_key_status_db,
    record_key_transition,
    atomic_release_key,
    atomic_freeze_key,
    atomic_record_success,
)
from .key_resolver import KeyResolverMixin
from .model_resolution import ModelResolutionMixin
from contextvars import ContextVar

is_sub_agent_context: ContextVar[bool] = ContextVar("is_sub_agent_context", default=False)

class APIRouter(KeyResolverMixin, ModelResolutionMixin):
    """
    The central API Router for the entire system, implemented as a Singleton.
    This class is responsible for managing the lifecycle and status of API keys,
    orchestrating model selection, and handling various aspects of request routing.
    It integrates key resolution, rate limiting, and model health tracking.

    As a Singleton, `APIRouter` ensures that all parts of the application
    access a single, consistent state for API key management and model availability.
    It inherits key resolution capabilities from `KeyResolverMixin`.

    Design Decision (Singleton & Centralization):
    The `APIRouter` is designed as a Singleton to centralize critical state management
    for API keys and model health. This decision prioritizes global consistency and
    simplified access to shared resources, which is crucial for a high-concurrency
    routing system that needs to make real-time decisions about key and model usage.
    While it increases coupling to this central component, it reduces the complexity
    of managing distributed state across different parts of the application.
    """
    _instance = None
    _initialized = False

    def _log_model_error(self, message: str, *args: Any) -> None:
        """Keep model-catalog diagnostics on the router's logger seam."""
        logger.error(message, *args)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """
        Initializes the APIRouter instance.
        This method sets up model priorities, loads and merges API key statuses from the database,
        and initializes model health scoring. It ensures that initialization occurs only once
        due to the Singleton pattern.

        Key initialization steps include:
        - Loading `AVAILABLE_MODELS`, `MODEL_POOLS`, `MODEL_PRIORITY` from configuration.
        - Initializing `_key_status` by loading existing statuses from `src/backend/keys/status.py`
          and merging with default status for new or incomplete keys.
        - Setting up `_model_health` to track performance of each model alias, with mean reversion towards 50.
        - Establishing internal locks (`_model_lock`, `_key_lock`) for thread safety.

        Design Decision (Complex Initialization):
        The `__init__` method is comprehensive, handling multiple initialization concerns
        (model configuration, key status loading/merging, model health). This centralization
        ensures that all necessary components are properly set up and synchronized at startup.
        While it contributes to the method's length, it consolidates critical setup logic,
        making the router immediately operational and consistent.
        """
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
        self._reservations: Dict[str, Dict[str, Any]] = {}

        # Model health scoring — shared across all pool instances
        # Each pool member alias has: score (0-100), last_updated timestamp
        # score=100 perfect, score=0 dead, mean reversion toward 50
        self._model_health: Dict[str, Dict[str, float]] = {}
        for _alias, _cfg in AVAILABLE_MODELS.items():
            _mid = str(_cfg.get("model_id", ""))
            if not _mid or _mid == "gemini-flash-pool":
                continue
            self._model_health[_alias] = {"score": 100.0, "updated": time.time()}

        logger.info(
            "APIRouter initialized models=%s keys=%d (API: %d)",
            ", ".join(self.model_priority),
            len(self._key_status),
            len(config.GEMINI_API_KEYS),
        )
        APIRouter._initialized = True

    def refresh_keys(self):
        """
        Refreshes the in-memory API key status by reloading from the persistent database.
        This method ensures that the router's internal `_key_status` dictionary is up-to-date
        with any changes made externally (e.g., via admin console) or to the `.env` configuration.

        The refresh process involves:
        1. Acquiring a lock (`_key_lock`) to ensure thread-safe access to `_key_status`.
        2. Re-identifying all active model IDs and API keys from `AVAILABLE_MODELS` and `config.GEMINI_API_KEYS`.
        3. Loading the latest key statuses from `get_key_status_db()`.
        4. Merging the loaded statuses with existing in-memory data, preserving active request counts
           and other transient states while updating persistent attributes like `enabled`, `frozen_until`,
           `consecutive_failures`, and `tier`.
        5. Ensuring that `per_model` entries are correctly initialized for all known model IDs.
        """
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

    def is_global_cooldown_active(self) -> bool:
        """Compatibility hook; key availability is never global.

        Gemini failures are scoped to the concrete model alias/API key pair.
        Account credentials, custom endpoints, and unrelated aliases must not
        be blocked by another key's 429s.
        """
        return False

    def record_429(self, model_alias: Optional[str] = None, key: Optional[str] = None) -> bool:
        """Record a 429 without opening a process-wide circuit.

        The return value is retained for callers, but global cooldown is no
        longer a routing decision. ``freeze_key`` owns the model/key state.
        """
        with self._key_lock:
            self._consecutive_429_count += 1
        return False

    def reset_429_counter(self) -> None:
        """
        Resets the internal counter for consecutive 429 (Too Many Requests) errors.
        This is typically called after a successful request or when a key has recovered,
        to indicate that the system is no longer experiencing a sustained period of rate limits.
        """
        with self._key_lock:
            self._consecutive_429_count = 0

    def reset_active_requests(self) -> None:
        """
        Resets the `active_requests` count for all API keys in both in-memory state and the database.
        This function is crucial for recovering from potential inconsistencies where keys might
        appear to have active requests that are no longer valid (e.g., due to client disconnects).
        It ensures that keys are correctly freed up for new requests.
        """
        try:
            from src.backend.keys.status import reset_active_requests_db
            reset_active_requests_db()
        except Exception as e:
            logger.error("Failed to reset active requests in DB: %s", e)
        with self._key_lock:
            for key in self._key_status:
                self._key_status[key]["active_requests"] = 0
            logger.info("APIRouter active requests reset to 0 in-memory.")

    # ── Model health scoring ─────────────────────────────────────────

    def _apply_mean_reversion(self, alias: str) -> None:
        """
        Applies a mean reversion decay to a model's health score over time.
        This mechanism ensures that model scores, even after being penalized for failures,
        gradually drift back towards a neutral score (e.g., 50) if no further issues occur.
        This prevents models from being permanently penalized and allows for eventual recovery.

        Args:
            alias: The alias of the model whose health score is to be reverted.
        """
        h = self._model_health.get(alias)
        if not h:
            return
        now = time.time()
        elapsed = now - h["updated"]
        if elapsed < 10:
            return
        # Drift 1 point per 30s toward 50
        delta = (50 - h["score"]) * min(1.0, elapsed / 30.0)
        new_score = h["score"] + delta
        self._model_health[alias] = {"score": max(0.0, min(100.0, new_score)), "updated": now}

    def update_model_health(self, alias: str, success: bool, reason: str = "") -> None:
        """
        Updates the health score for a specific model alias based on the outcome of an API call.
        A successful call increases the score, while a failure decreases it.
        This score is used by the router to prioritize healthier models.

        Args:
            alias: The alias of the model to update.
            success: True if the API call was successful, False otherwise.
            reason: Optional reason for failure, used for specific score adjustments.

        success=True: +5 (cap 100)
        success=False, transient (503/429/unavailable): -15
        success=False, hard (invalid_key/billing): -40
        """
        if alias not in self._model_health:
            return
        with self._key_lock:
            self._apply_mean_reversion(alias)
            h = self._model_health[alias]
            if success:
                h["score"] = min(100.0, h["score"] + 5)
            elif reason in ("invalid_key", "billing_error", "permission_denied"):
                h["score"] = max(0.0, h["score"] - 40)
            else:
                h["score"] = max(0.0, h["score"] - 15)
            h["updated"] = time.time()

    def get_healthy_pool_members(self, members: list) -> list:
        """Sort pool members by health score descending (healthiest first).
        Members with score < 20 are deprioritized to the end.
        """
        now = time.time()
        def _key(m: str) -> tuple:
            h = self._model_health.get(m)
            if not h:
                return (1, 0.0)
            score = h["score"]
            # time-decay: if not updated recently, drift toward 50
            elapsed = now - h["updated"]
            if elapsed > 30:
                score += (50 - score) * min(1.0, elapsed / 30.0)
            # Deprioritize low scores
            if score < 20:
                return (1, score)
            return (0, -score)
        return sorted(members, key=_key)

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
            # Update model health if model_id maps to a member alias
            if model_id:
                for _alias, _cfg in AVAILABLE_MODELS.items():
                    if str(_cfg.get("model_id", "")) == model_id:
                        self.update_model_health(_alias, success=True)
                        break
        except Exception as exc:
            logger.error("record_success error: %s", exc)

    def record_failure(self, reason: str = "error") -> None:
        try:
            self.total_errors += 1
        except Exception as exc:
            logger.error("record_failure error: %s", exc)

    def record_attempt_failure(
        self,
        *,
        key: Optional[str],
        model_id: Optional[str],
        reason: str,
        duration: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Apply one key/model failure transition and retain trace metadata.

        APIRouter owns the provider-key state machine. Callers provide only
        attempt context; they must not independently mutate key counters,
        cooldowns, or global circuits.
        """
        transition_id = uuid.uuid4().hex
        metadata = dict(metadata or {})
        from src.core.runtime.request_context import get_request_id
        req_id = (metadata.get("request_id") or "").strip() or get_request_id() or uuid.uuid4().hex
        metadata["request_id"] = req_id
        event = {
            "ts": time.time(),
            "transition_id": transition_id,
            "request_id": req_id,
            "key": (key or "")[-8:] if key else None,
            "model_id": model_id,
            "reason": reason,
            "duration": duration,
            **metadata,
        }
        try:
            with self._key_lock:
                transitions = getattr(self, "_key_transitions", None)
                if transitions is None:
                    from collections import deque
                    transitions = self._key_transitions = deque(maxlen=512)
                transitions.append(event)
            if key:
                self.freeze_key(key, duration, model_id, reason)
                record_key_transition(
                    transition_id=transition_id,
                    request_id=event["request_id"],
                    key=key,
                    model_id=model_id,
                    reason=reason,
                    metadata=event,
                    created_at=event["ts"],
                )
            self.record_failure(reason)
            logger.warning(
                "[KeyTransition] key=...%s model=%s reason=%s metadata=%s",
                event["key"] or "", model_id or "*", reason, metadata or {},
            )
        except Exception as exc:
            logger.error("record_attempt_failure error: %s", exc)

    async def acquire_quota(self, reserved_tokens: int, model_alias: str, **kwargs) -> bool:
        """
        Acquires quota for a given request, checking against model-specific rate limits.
        This method interacts with the `gemini_rate_limiter` to ensure that the request
        does not exceed the allocated RPM/TPM for the specified model.

        Args:
            reserved_tokens: The number of tokens to reserve for the current request.
            model_alias: The alias of the model for which quota is being acquired.
            **kwargs: Additional keyword arguments for future extensibility.

        Returns:
            True if quota is successfully acquired, False otherwise.
        """
        return await get_rate_limiter(model_alias).acquire_quota(reserved_tokens)

    def freeze_key(self, key: str, duration: int, model_id: Optional[str] = None, reason: str = "rate_limit") -> None:
        """
        Freezes an API key for a specified duration, either globally or per-model, based on the reason for failure.
        This mechanism prevents further requests from being routed to a problematic key, allowing it to recover.
        It updates both the in-memory key status and the persistent database.

        Args:
            key: The API key to freeze.
            duration: The base duration in seconds for which to freeze the key.
            model_id: Optional concrete model ID if the freeze is specific to a model.
            reason: The reason for freezing the key (e.g., "rate_limit", "invalid_key").

        Sub-agent requests still share these keys with the main session, so
        transient failures must freeze and penalize the failing key for everyone.
        Only global cooldown decisions remain isolated in sub-agent context.
        For severe reasons like `invalid_key`, `permission_denied`, or
        `billing_error`, the key is permanently disabled (`enabled = 0`).
        The adaptive cooldown logic (`_adaptive_cooldown`) and jitter are applied to prevent synchronized retries.
        """
        try:
            with self._key_lock:
                if key not in self._key_status:
                    return
                
                if reason in ("invalid_key", "permission_denied", "billing_error"):
                    logger.error("[APIRouter] Permanent failure detected on key ...%s (Reason: %s). Disabling key vĩnh viễn.", key[-8:], reason)
                    self._key_status[key]["enabled"] = 0
                    from src.backend.keys.status import atomic_disable_key
                    atomic_disable_key(key)
                    from src.core.config_n_logg.config import remove_banned_key_from_env
                    remove_banned_key_from_env(key)
                    return

                cf = self._key_status[key].get("consecutive_failures", 0)
                if reason != "bad_request_spam_prevent":
                    cf += 1
                adj_duration = self._adaptive_cooldown(reason, cf)
                jitter = random.uniform(adj_duration * -0.3, adj_duration * 0.3)
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

            # Update model health on failure only for model/provider-level failures, not key-local errors
            KEY_LOCAL_REASONS = {"invalid_key", "permission_denied", "unregistered_key"}
            if model_id and reason not in KEY_LOCAL_REASONS:
                for _alias, _cfg in AVAILABLE_MODELS.items():
                    if str(_cfg.get("model_id", "")) == model_id:
                        self.update_model_health(_alias, success=False, reason=reason)
                        break
        except Exception as exc:
            logger.error("freeze_key error: %s", exc)


    def _cleanup_reservations_locked(self, now: float) -> None:
        """Prune old released or timed-out reservations to bound memory usage."""
        if len(self._reservations) > 1000:
            cutoff = now - 300.0
            stale = [
                rid for rid, r in self._reservations.items()
                if r.get("released") or r.get("reserved_at", 0) < cutoff
            ]
            for rid in stale[:500]:
                self._reservations.pop(rid, None)

    def release_key(self, key: str, reservation_id: Optional[str] = None,
                    metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Release a reservation exactly once and emit lifecycle metadata."""
        try:
            with self._key_lock:
                now = time.time()
                self._cleanup_reservations_locked(now)
                if reservation_id:
                    reservation = self._reservations.get(reservation_id)
                    if reservation is None or reservation.get("released"):
                        logger.warning("[KeyTransition] duplicate/unknown release reservation=%s key=...%s metadata=%s",
                                       reservation_id, (key or "")[-8:], metadata or {})
                        return False
                    reservation["released"] = True
                if key not in self._key_status:
                    return False
                active = self._key_status[key].get("active_requests", 0)
                if active <= 0:
                    logger.warning("[KeyTransition] release without active reservation key=...%s metadata=%s",
                                   (key or "")[-8:], metadata or {})
                    return False
                self._key_status[key]["active_requests"] = active - 1
            atomic_release_key(key)
            return True
        except Exception as exc:
            logger.error("release_key error: %s", exc)
            return False

    def register_reservation(self, reservation: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> None:
        reservation_id = reservation.get("reservation_id")
        key = reservation.get("key")
        if not reservation_id or not key:
            return
        with self._key_lock:
            now = time.time()
            self._cleanup_reservations_locked(now)
            self._reservations[reservation_id] = {
                "key": key, "reserved_at": reservation.get("reserved_at", now),
                "released": False, **(metadata or {}),
            }

    def freeze_all_keys(self, duration: int, *, actor: str = "", reason: str = "", request_id: str = "") -> None:
        """Emergency-only administrative freeze; never use for normal routing."""
        if not actor or not reason:
            logger.error("freeze_all_keys refused: actor and reason are required")
            return
        try:
            now = time.time()
            with self._key_lock:
                for key in self._key_status:
                    jitter = random.uniform(0, duration * 0.15) + abs(random.gauss(0, duration * 0.05))
                    effective = now + int(duration + jitter)
                    self._key_status[key]["frozen_until"] = effective
                    atomic_freeze_key(key, effective)
            logger.warning(
                "[EmergencyFreeze] all keys frozen duration=%ds actor=%s reason=%s request_id=%s",
                duration, actor, reason, request_id,
            )
        except Exception as exc:
            logger.error("freeze_all_keys error: %s", exc)

router = APIRouter()


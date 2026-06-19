# ruff: noqa: E402
import asyncio
import copy
import datetime
import time
from collections import deque
from typing import Any, Dict, Optional, Tuple

from src.core.api_config import AVAILABLE_MODELS
from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_keys as logger
from src.backend.key_status import get_key_usage_db, update_key_usage_batch_db

# Google Cloud RPD reset is at midnight Pacific Time.
# PDT (summer): UTC-7, PST (winter): UTC-8.
# We use a fixed UTC-7 offset as a reasonable approximation; the
# exact DST transition creates at most a 1-hour error.
import datetime as _dt
_PACIFIC_OFFSET = _dt.timedelta(hours=-7)

def _today_pacific() -> _dt.date:
    return (_dt.datetime.utcnow() + _PACIFIC_OFFSET).date()


# ── Per-model rate limiter (RPM / TPM / RPD) ──────────────────

class GeminiRateLimiter:
    def __init__(self, rpm: int = 15, tpm: int = 250000, rpd: int = 500, model_alias: str = ""):
        self.model_alias = model_alias
        self.rpm_limit = rpm
        self.tpm_limit = tpm
        self.rpd_limit = rpd
        self._minute_req_ts = deque()
        self._minute_tokens = deque()
        self._rpd_date = _today_pacific()
        self._rpd_count = 0
        self._lock = None

    async def acquire_quota(self, reserved_tokens: int) -> bool:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            now = time.time()
            today = _today_pacific()
            if today != self._rpd_date:
                logger.info("[RateLimit] %s RPD counter reset: %s -> %s (old count=%d)",
                            self.model_alias, self._rpd_date, today, self._rpd_count)
                self._rpd_date = today
                self._rpd_count = 0
            while self._minute_req_ts and now - self._minute_req_ts[0] >= 60:
                self._minute_req_ts.popleft()
            while self._minute_tokens and now - self._minute_tokens[0][0] >= 60:
                self._minute_tokens.popleft()
            if self.rpd_limit > 0 and self._rpd_count >= self.rpd_limit:
                return False
            used_tokens = sum(item[1] for item in self._minute_tokens)
            if len(self._minute_req_ts) >= self.rpm_limit or (used_tokens + reserved_tokens) > self.tpm_limit:
                return False
            self._minute_req_ts.append(now)
            self._minute_tokens.append((now, reserved_tokens))
            self._rpd_count += 1
            return True

    def get_counters_snapshot(self) -> dict:
        now = time.time()
        rpm_used = sum(1 for ts in self._minute_req_ts if now - ts < 60)
        tpm_used = sum(t for ts, t in self._minute_tokens if now - ts < 60)
        rpd_used = self._rpd_count if _today_pacific() == self._rpd_date else 0
        return {
            "model": self.model_alias,
            "rpm_used": rpm_used, "rpm_limit": self.rpm_limit,
            "tpm_used": tpm_used, "tpm_limit": self.tpm_limit,
            "rpd_used": rpd_used, "rpd_limit": self.rpd_limit,
        }


# ── Singleton registry ─────────────────────────────────────────

_rate_limiters: Dict[str, GeminiRateLimiter] = {}

def get_rate_limiter(model_alias: str) -> GeminiRateLimiter:
    if model_alias not in _rate_limiters:
        cfg = AVAILABLE_MODELS.get(model_alias, {})
        key_count = max(1, len(config.GEMINI_API_KEYS))
        rpm = int(cfg.get("rpm", 15)) * key_count
        tpm = int(cfg.get("tpm", 250000)) * key_count
        rpd = int(cfg.get("rpd", 0)) * key_count
        _rate_limiters[model_alias] = GeminiRateLimiter(
            rpm=rpm, tpm=tpm, rpd=rpd, model_alias=model_alias,
        )
        logger.info("[RateLimit] Created limiter for %s: RPM=%d TPM=%d RPD=%d (keys=%d)",
                    model_alias, rpm, tpm, rpd, key_count)
    return _rate_limiters[model_alias]


def clear_rate_limiters():
    _rate_limiters.clear()
    logger.info("Gemini rate limiters cleared (will be re-created with new key count)")


# ── Per-key daily usage tracker ────────────────────────────────

import threading

_key_usage: Dict[str, Dict[str, Any]] = {}
_usage_date: datetime.date = _today_pacific()
_usage_lock = threading.Lock()


# ── Per-key/per-model sliding window rate limiter (RPM / TPM) ──

_key_model_requests: Dict[str, deque] = {}
_key_model_tokens: Dict[str, deque] = {}

def get_model_limits(model_id: str) -> Tuple[int, int, int]:
    """Returns (rpm, tpm, rpd) for the model_id."""
    for alias, cfg in AVAILABLE_MODELS.items():
        if cfg.get("model_id") == model_id and cfg.get("model_id") != "gemini-flash-pool":
            return int(cfg.get("rpm", 5)), int(cfg.get("tpm", 250000)), int(cfg.get("rpd", 20))
    if model_id in AVAILABLE_MODELS:
        cfg = AVAILABLE_MODELS[model_id]
        if cfg.get("model_id") != "gemini-flash-pool":
            return int(cfg.get("rpm", 5)), int(cfg.get("tpm", 250000)), int(cfg.get("rpd", 20))
    return 5, 250000, 20

def check_key_model_limits(api_key: str, model_id: str, estimated_tokens: int, rpm_limit: int, tpm_limit: int) -> bool:
    """Checks if the api_key has enough RPM/TPM remaining for model_id."""
    now = time.time()
    k_m = f"{api_key}::{model_id}"
    with _usage_lock:
        reqs = _key_model_requests.setdefault(k_m, deque())
        toks = _key_model_tokens.setdefault(k_m, deque())
        
        # Clean older than 60s
        while reqs and now - reqs[0] >= 60:
            reqs.popleft()
        while toks and now - toks[0][0] >= 60:
            toks.popleft()
            
        if len(reqs) >= rpm_limit:
            return False
            
        used_tokens = sum(item[1] for item in toks)
        if used_tokens + estimated_tokens > tpm_limit:
            return False
            
    return True

def record_key_model_usage(api_key: str, model_id: str, tokens: int):
    """Records a request start for the api_key and model_id."""
    now = time.time()
    k_m = f"{api_key}::{model_id}"
    with _usage_lock:
        reqs = _key_model_requests.setdefault(k_m, deque())
        toks = _key_model_tokens.setdefault(k_m, deque())
        
        reqs.append(now)
        toks.append((now, tokens))



# ── Per-key/per-model penalty system (temporary score reduction after errors) ──

_score_penalties: Dict[str, Dict[str, Any]] = {}
_penalty_cleanup_ts: float = 0.0
_transient_429_count: int = 0
_transient_503_count: int = 0


def count_transient_error(reason: str) -> None:
    global _transient_429_count, _transient_503_count
    if reason == "rate_limit":
        _transient_429_count += 1
    elif reason == "unavailable":
        _transient_503_count += 1


def get_seconds_until_pacific_midnight() -> int:
    now_utc = _dt.datetime.utcnow()
    now_pacific = now_utc + _PACIFIC_OFFSET
    tomorrow_pacific = now_pacific.date() + _dt.timedelta(days=1)
    midnight_pacific = _dt.datetime.combine(tomorrow_pacific, _dt.time.min)
    utc_midnight = midnight_pacific - _PACIFIC_OFFSET
    seconds = int((utc_midnight - now_utc).total_seconds())
    return max(300, seconds)


def get_penalty_config(reason: str) -> Optional[Dict[str, Any]]:
    """Get dynamic penalty configuration based on current config values."""
    if reason in ("rate_limit_rpd", "project_quota_429"):
        return {
            "duration": get_seconds_until_pacific_midnight(),
            "score_reduction": -60
        }
    elif reason in ("rate_limit", "rate_limit_rpm_tpm"):
        return {
            "duration": config.KEY_429_COOLDOWN_SECONDS * 10,
            "score_reduction": -86
        }
    elif reason in ("permission_denied", "project_denied"):
        return {
            "duration": config.KEY_INVALID_COOLDOWN_SECONDS,
            "score_reduction": -30
        }
    elif reason == "unavailable":
        return {
            "duration": config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS * 4,
            "score_reduction": -20
        }
    elif reason in ("server_error", "timeout", "grounding_fallback", "unknown"):
        return {
            "duration": config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS * 3,
            "score_reduction": -20
        }
    elif reason == "billing_error":
        return {
            "duration": config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS * 10,
            "score_reduction": -40
        }
    return None


class DynamicPenaltyMap(dict):
    def get(self, key, default=None):
        cfg = get_penalty_config(key)
        return cfg if cfg is not None else default

    def __getitem__(self, key):
        cfg = get_penalty_config(key)
        if cfg is None:
            raise KeyError(key)
        return cfg

    def __contains__(self, key):
        return get_penalty_config(key) is not None


PENALTY_MAP = DynamicPenaltyMap()



def _penalty_key(key: str, actual_model_id: Optional[str] = None) -> str:
    return f"{key}::{actual_model_id or '__global__'}"


def apply_error_penalty(key: str, reason: str, actual_model_id: Optional[str] = None) -> None:
    from src.core.router.core.router import is_sub_agent_context
    if is_sub_agent_context.get():
        logger.info("[Sub-Agent] Bypassing apply_error_penalty for key ...%s (Reason: %s)", key[-8:], reason)
        return
    cfg = PENALTY_MAP.get(reason)
    if not cfg:
        return
    expires = time.time() + cfg["duration"]
    score_red = cfg["score_reduction"]
    pkey = _penalty_key(key, actual_model_id)
    with _usage_lock:
        old = _score_penalties.get(pkey)
        if not old or expires > old["expires"] or score_red < old["score_reduction"]:
            _score_penalties[pkey] = {
                "expires": expires,
                "score_reduction": score_red,
                "key": key,
                "model_id": actual_model_id,
                "reason": reason,
            }
            logger.info("[Penalty] key=...%s reason=%s model=%s duration=%ds score=%d", key[-8:], reason, actual_model_id or "*", cfg["duration"], score_red)
            try:
                from src.backend.key_status import db_save_penalty
                db_save_penalty(pkey, key, actual_model_id, reason, expires, score_red)
            except Exception as e:
                logger.warning("[RateLimit] Failed to write penalty to SQLite: %s", e)


# ── Per-key daily usage tracker ────────────────────────────────

def _load_key_usage() -> None:
    global _key_usage, _usage_date
    _usage_date = _today_pacific()
    try:
        saved = get_key_usage_db()
        with _usage_lock:
            _key_usage = {}
            for k, v in saved.items():
                if isinstance(v, dict):
                    entry = dict(v)
                    if entry.get("date") != str(_usage_date):
                        entry["today"] = 0
                        entry["date"] = str(_usage_date)
                        for m_entry in entry.get("per_model", {}).values():
                            if isinstance(m_entry, dict):
                                m_entry["today"] = 0
                    _key_usage[k] = entry
    except Exception as exc:
        logger.warning("Failed to load key usage: %s", exc)
        with _usage_lock:
            _key_usage = {}


def _save_key_usage() -> None:
    with _usage_lock:
        usage_copy = copy.deepcopy(_key_usage)
    update_key_usage_batch_db(usage_copy)


def record_key_usage(key: str, actual_model_id: Optional[str] = None) -> None:
    global _usage_date
    today = _today_pacific()
    if today != _usage_date:
        _load_key_usage()

    should_save = False
    with _usage_lock:
        entry = _key_usage.setdefault(key, {"total": 0, "today": 0, "date": str(today), "per_model": {}})
        per_model = entry.setdefault("per_model", {})

        entry["total"] = entry.get("total", 0) + 1
        entry["today"] = entry.get("today", 0) + 1
        entry["date"] = str(today)

        if actual_model_id and actual_model_id != "gemini-flash-pool":
            m_usage = per_model.setdefault(actual_model_id, {"today": 0, "total": 0})
            m_usage["today"] += 1
            m_usage["total"] += 1

        if entry["today"] % 3 == 0:
            should_save = True

    if should_save:
        _save_key_usage()


def get_key_priority(key: str, actual_model_id: Optional[str] = None) -> int:
    global _usage_date
    today = _today_pacific()
    if today != _usage_date:
        return 50

    with _usage_lock:
        entry = _key_usage.get(key)
        if not entry:
            return 100

        per_model = entry.get("per_model", {})
        model_today_count = per_model.get(actual_model_id, {}).get("today", 0) if actual_model_id else entry.get("today", 0)

        # ── Clean expired penalties (tối đa 1 lần/60s) ──
        now = time.time()
        global _penalty_cleanup_ts
        if now - _penalty_cleanup_ts > 60:
            _penalty_cleanup_ts = now
            expired = [k for k, p in list(_score_penalties.items()) if p["expires"] <= now]
            if expired:
                for k in expired:
                    del _score_penalties[k]
                try:
                    from src.backend.key_status import db_clean_expired_penalties
                    db_clean_expired_penalties()
                except Exception:
                    pass

        # ── Check active penalty. Prefer model-specific state; global is only
        # used for callers that truly cannot identify a concrete model.
        pen = _score_penalties.get(_penalty_key(key, actual_model_id))
        if not pen:
            pen = _score_penalties.get(_penalty_key(key))

    target_rpd = 0
    if actual_model_id:
        for cfg in AVAILABLE_MODELS.values():
            if cfg.get("model_id") == actual_model_id and cfg.get("model_id") != "gemini-flash-pool":
                target_rpd = int(cfg.get("rpd", 0))
                break

    if target_rpd <= 0:
        base = max(1, 100 - model_today_count * 2)
    else:
        remaining = target_rpd - model_today_count
        if remaining <= 0:
            return -1
        base = int((remaining / target_rpd) * 100)

    if pen:
        scored = base + pen["score_reduction"]
        if scored <= 0:
            return 1
        return scored
    return base


def get_key_rpd_status(key: str, actual_model_id: Optional[str] = None) -> Tuple[int, int, bool]:
    """Return (today_count, target_rpd, is_exhausted) for a key and model."""
    if not actual_model_id:
        return 0, 0, False

    target_rpd = 0
    for cfg in AVAILABLE_MODELS.values():
        if cfg.get("model_id") == actual_model_id and cfg.get("model_id") != "gemini-flash-pool":
            target_rpd = int(cfg.get("rpd", 0))
            break

    if target_rpd <= 0:
        return 0, 0, False

    with _usage_lock:
        entry = _key_usage.get(key)
        if not entry:
            return 0, target_rpd, False
        per_model = entry.get("per_model", {})
        model_today_count = per_model.get(actual_model_id, {}).get("today", 0) if actual_model_id else entry.get("today", 0)

    return model_today_count, target_rpd, (model_today_count >= target_rpd)


def get_usage_summary() -> Dict[str, Any]:
    today = _today_pacific()
    if today != _usage_date:
        _load_key_usage()
    with _usage_lock:
        total_today = sum(e.get("today", 0) for e in _key_usage.values())
        total_all = sum(e.get("total", 0) for e in _key_usage.values())
        active = sum(1 for e in _key_usage.values() if e.get("today", 0) > 0)
        keys_count = len(_key_usage)
    return {"date": str(today), "total_calls_today": total_today,
            "total_calls_all": total_all, "active_keys_today": active,
            "keys_count": keys_count}


def load_penalties_from_db() -> None:
    try:
        from src.backend.key_status import db_load_active_penalties
        active = db_load_active_penalties()
        with _usage_lock:
            _score_penalties.clear()
            _score_penalties.update(active)
        logger.info("[RateLimit] Loaded %d active key penalties from SQLite", len(active))
    except Exception as e:
        logger.warning("[RateLimit] Failed to load key penalties from SQLite: %s", e)


# Load key usage immediately on module import (startup)
_load_key_usage()
load_penalties_from_db()

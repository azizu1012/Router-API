"""Module for managing API key status in a persistent SQLite database.

This module provides atomic, asynchronous operations for tracking key usage,
freezes, failures, and other status attributes. It utilizes a `ThreadPoolExecutor`
for non-blocking database writes, ensuring that core routing logic is not
delayed by I/O operations. All critical updates are designed to be atomic
to maintain data consistency in a high-concurrency environment.
"""
import json
import time
from typing import Any, Dict, List, Optional
import concurrent.futures

from src.backend._db import _LOCK, conn as _conn

_db_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)


def get_key_status_db() -> Dict[str, Dict[str, Any]]:
    """
    Retrieves the current status of all API keys from the persistent database.
    This function reads key status, parses JSON fields (`per_model`, `data`), and
    applies default values for any missing attributes, ensuring a consistent
    structure for in-memory key management.

    Returns:
        A dictionary where keys are API key strings and values are dictionaries
        containing the detailed status of each key.
    """
    with _LOCK:
        c = _conn()
        try:
            result = {}
            for r in c.execute("SELECT * FROM key_status").fetchall():
                d = dict(r)
                pm = d.pop("data", None)
                if d.get("per_model"):
                    try:
                        d["per_model"] = json.loads(d["per_model"])
                    except Exception:
                        d["per_model"] = {}
                else:
                    d.setdefault("per_model", {})

                if pm:
                    try:
                        old = json.loads(pm)
                        if isinstance(old, dict):
                            for k_old, v_old in old.items():
                                if k_old not in d or d[k_old] is None:
                                    d[k_old] = v_old
                    except Exception:
                        pass

                d.setdefault("enabled", 1)
                d.setdefault("usage", 0)
                d.setdefault("active_requests", 0)
                d.setdefault("frozen_until", 0.0)
                d.setdefault("consecutive_failures", 0)
                d.setdefault("last_success", 0.0)
                d.setdefault("date", "")
                d.setdefault("today", 0)
                d.setdefault("tier", "free")
                result[d.pop("key")] = d
            return result
        finally:
            c.close()


def set_key_status_batch_db(status: Dict[str, Dict[str, Any]]) -> None:
    """
    Asynchronously updates the status of multiple API keys in a batch operation.
    This function is used to persist the current in-memory state of `_key_status`
    to the database, ensuring that changes (e.g., from `APIRouter.refresh_keys`)
    are saved. The write operation is submitted to a `ThreadPoolExecutor` to avoid
    blocking the main application thread.

    Args:
        status: A dictionary containing the key statuses to update. Keys are API key strings,
                and values are dictionaries of key attributes.
    """
    def _write():
        with _LOCK:
            c = _conn()
            try:
                for k, v in status.items():
                    pm_json = json.dumps(v.get("per_model", {}))
                    c.execute(
                        """INSERT OR REPLACE INTO key_status
                           (key, enabled, usage, active_requests, frozen_until, consecutive_failures, last_success, date, today, per_model)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (k, v.get("enabled", 1), v.get("usage", 0), v.get("active_requests", 0),
                         v.get("frozen_until", 0.0), v.get("consecutive_failures", 0),
                         v.get("last_success", 0.0), v.get("date", ""),
                         v.get("today", 0), pm_json),
                    )
                c.commit()
            finally:
                c.close()
    _db_executor.submit(_write)


def atomic_reserve_key(key: str) -> None:
    """
    Atomically reserves an API key, incrementing its usage count and active requests count.
    This operation is crucial for ensuring that key usage is accurately tracked and
    concurrency limits are respected. It handles conflicts by updating existing entries.
    The write operation is asynchronous via `_db_executor`.

    Args:
        key: The API key to reserve.
    """
    def _write():
        with _LOCK:
            c = _conn()
            try:
                c.execute(
                    """INSERT INTO key_status (key, usage, active_requests)
                       VALUES (?, 1, 1)
                       ON CONFLICT(key) DO UPDATE SET
                           usage = usage + 1,
                           active_requests = active_requests + 1""",
                    (key,),
                )
                c.commit()
            finally:
                c.close()
    _db_executor.submit(_write)


def atomic_release_key(key: str) -> None:
    """
    Atomically releases a previously reserved API key, decrementing its active requests count.
    This ensures that keys are properly freed up after a request is completed or fails,
    allowing them to be used by other concurrent requests. The write operation is asynchronous.

    Args:
        key: The API key to release.
    """
    def _write():
        with _LOCK:
            c = _conn()
            try:
                c.execute(
                    "UPDATE key_status SET active_requests = MAX(0, active_requests - 1) WHERE key = ?",
                    (key,),
                )
                c.commit()
            finally:
                c.close()
    _db_executor.submit(_write)


def atomic_freeze_key(key: str, until_ts: float, model_id: Optional[str] = None, cf: Optional[int] = None) -> None:
    """
    Atomically freezes an API key until a specified timestamp.
    This function updates the `frozen_until` and `consecutive_failures` attributes
    for a key, either globally or for a specific model. It prevents the key from
    being used during its frozen period. If the key does not exist, it will be inserted.
    The write operation is asynchronous.

    Args:
        key: The API key to freeze.
        until_ts: The timestamp (epoch seconds) until which the key should be frozen.
        model_id: Optional concrete model ID if the freeze is specific to a model.
        cf: Optional consecutive failures count to set. Defaults to 1 if not provided.
    """
    def _write():
        with _LOCK:
            c = _conn()
            try:
                row = c.execute("SELECT * FROM key_status WHERE key = ?", (key,)).fetchone()
                if not row:
                    pm = {}
                    if model_id:
                        pm[model_id] = {"failures": 1, "frozen_until": until_ts}
                    c.execute(
                        "INSERT INTO key_status (key, frozen_until, consecutive_failures, per_model) VALUES (?,?,?,?)",
                        (key, until_ts if not model_id else 0, cf if cf is not None else 1, json.dumps(pm)),
                    )
                else:
                    d = dict(row)
                    pm = json.loads(d.get("per_model") or "{}")
                    db_cf = cf if cf is not None else d.get("consecutive_failures", 0) + 1
                    if model_id:
                        pm_entry = pm.setdefault(model_id, {"failures": 0, "frozen_until": 0.0})
                        pm_entry["frozen_until"] = until_ts
                        pm_entry["failures"] = pm_entry.get("failures", 0) + 1
                        c.execute(
                            "UPDATE key_status SET per_model=?, consecutive_failures=?, active_requests = MAX(0, active_requests - 1) WHERE key=?",
                            (json.dumps(pm), db_cf, key),
                        )
                    else:
                        c.execute(
                            "UPDATE key_status SET frozen_until=?, consecutive_failures=?, active_requests = MAX(0, active_requests - 1) WHERE key=?",
                            (until_ts, db_cf, key),
                        )
                c.commit()
            finally:
                c.close()
    _db_executor.submit(_write)


def atomic_record_success(key: str, model_id: Optional[str] = None) -> None:
    """
    Atomically records a successful API call for a specific key and model.
    This resets the `consecutive_failures` count and updates the `last_success` timestamp
    for the key (globally and per-model). The write operation is asynchronous via `_db_executor`.

    Args:
        key: The API key that was used successfully.
        model_id: Optional concrete model ID that was used successfully. If None, global status is updated.
    """
    def _write():
        now_ts = time.time()
        with _LOCK:
            c = _conn()
            try:
                if model_id:
                    row = c.execute("SELECT per_model FROM key_status WHERE key = ?", (key,)).fetchone()
                    raw = dict(row) if row else {}
                    pm = json.loads(raw.get("per_model", "{}")) if raw.get("per_model") else {}
                    pm_entry = pm.setdefault(model_id, {"failures": 0, "frozen_until": 0.0})
                    pm_entry["failures"] = 0
                    pm_entry["frozen_until"] = 0.0
                    c.execute(
                        "UPDATE key_status SET frozen_until=0.0, consecutive_failures=0, last_success=?, per_model=? WHERE key=?",
                        (now_ts, json.dumps(pm), key),
                    )
                else:
                    c.execute(
                        "UPDATE key_status SET frozen_until=0.0, consecutive_failures=0, last_success=? WHERE key=?",
                        (now_ts, key),
                    )
                c.commit()
            finally:
                c.close()
    _db_executor.submit(_write)


def register_keys_in_db(keys: List[str]) -> None:
    def _write():
        with _LOCK:
            c = _conn()
            try:
                c.execute("DELETE FROM key_status WHERE key LIKE 'oauth:%'")
                if keys:
                    for key in keys:
                        c.execute(
                            "INSERT OR IGNORE INTO key_status (key) VALUES (?)",
                            (key,),
                        )
                    placeholders = ",".join("?" for _ in keys)
                    c.execute(f"DELETE FROM key_status WHERE key NOT IN ({placeholders})", keys)
                c.commit()
            finally:
                c.close()
    _db_executor.submit(_write)


def set_key_tier_batch_db(tiers: Dict[str, str]) -> None:
    def _write():
        with _LOCK:
            c = _conn()
            try:
                for key, tier in tiers.items():
                    c.execute("UPDATE key_status SET tier = ? WHERE key = ?", (tier, key))
                c.commit()
            finally:
                c.close()
    _db_executor.submit(_write)


def get_key_tiers_db() -> Dict[str, str]:
    """
    Retrieves the tier assignments for all API keys from the persistent database.
    This function provides information about which access tier (e.g., "free", "premium", "admin")
    each key belongs to, used for permission and quota enforcement.

    Returns:
        A dictionary where keys are API key strings and values are their assigned tier strings.
    """
    with _LOCK:
        c = _conn()
        try:
            return {r["key"]: str(r["tier"] or "free") for r in c.execute("SELECT key, tier FROM key_status").fetchall()}
        finally:
            c.close()


# ── Key usage (merged from key_usage.py) ───────────────────────

def get_key_usage_db() -> Dict[str, Any]:
    with _LOCK:
        c = _conn()
        try:
            result = {}
            for r in c.execute("SELECT key, data FROM key_usage").fetchall():
                try:
                    result[r["key"]] = json.loads(r["data"])
                except Exception:
                    result[r["key"]] = {}
            return result
        finally:
            c.close()


def update_key_usage_batch_db(usage: Dict[str, Any]) -> None:
    def _write():
        with _LOCK:
            c = _conn()
            try:
                for k, v in usage.items():
                    c.execute(
                        "INSERT OR REPLACE INTO key_usage (key, data) VALUES (?,?)",
                        (k, json.dumps(v)),
                    )
                c.commit()
            finally:
                c.close()
    _db_executor.submit(_write)


def db_save_penalty(pkey: str, api_key: str, model_id: Optional[str], reason: str, expires: float, score_reduction: int) -> None:
    def _write():
        with _LOCK:
            c = _conn()
            try:
                c.execute(
                    """INSERT OR REPLACE INTO key_penalties (pkey, api_key, model_id, reason, expires, score_reduction)
                       VALUES (?,?,?,?,?,?)""",
                    (pkey, api_key, model_id or "__global__", reason, expires, score_reduction),
                )
                c.commit()
            finally:
                c.close()
    _db_executor.submit(_write)


def db_load_active_penalties() -> Dict[str, Dict[str, Any]]:
    """
    Loads all currently active penalties from the database.
    Penalties are used to dynamically reduce the priority score of API keys
    that have recently experienced failures or rate limits, guiding the router
    to select healthier keys. Only penalties that have not yet expired are loaded.

    Returns:
        A dictionary where keys are penalty IDs (pkey) and values are dictionaries
        containing penalty details (expires, score_reduction, api_key, model_id, reason).
    """
    now = time.time()
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute("SELECT * FROM key_penalties WHERE expires > ?", (now,)).fetchall()
            result = {}
            for r in rows:
                result[r["pkey"]] = {
                    "expires": r["expires"],
                    "score_reduction": r["score_reduction"],
                    "key": r["api_key"],
                    "model_id": None if r["model_id"] == "__global__" else r["model_id"],
                    "reason": r["reason"],
                }
            return result
        finally:
            c.close()


def db_clean_expired_penalties() -> None:
    """
    Asynchronously cleans up expired penalties from the database.
    This maintenance task removes old penalty entries that are no longer relevant,
    keeping the database lean and improving performance of penalty lookups.
    The write operation is asynchronous via `_db_executor`.
    """
    def _write():
        now = time.time()
        with _LOCK:
            c = _conn()
            try:
                c.execute("DELETE FROM key_penalties WHERE expires <= ?", (now,))
                c.commit()
            finally:
                c.close()
    _db_executor.submit(_write)


def atomic_disable_key(key: str) -> None:
    """
    Atomically disables an API key by setting its `enabled` status to 0 in the database.
    This is used for permanent key failures (e.g., invalid key, billing error) to prevent
    the key from being used again. The write operation is asynchronous.

    Args:
        key: The API key to disable.
    """
    def _write():
        with _LOCK:
            c = _conn()
            try:
                c.execute(
                    "UPDATE key_status SET enabled = 0 WHERE key = ?",
                    (key,),
                )
                c.commit()
            finally:
                c.close()
    _db_executor.submit(_write)


def reset_active_requests_db() -> None:
    """
    Resets the `active_requests` count to 0 for all API keys in the database.
    This is a global reset operation, typically used for system-wide recovery or
    maintenance to clear any stale active request counts.
    """
    with _LOCK:
        c = _conn()
        try:
            c.execute("UPDATE key_status SET active_requests = 0")
            c.commit()
        finally:
            c.close()


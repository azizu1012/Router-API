"""Module for managing API key status in a persistent SQLite database.

This module provides atomic, asynchronous operations for tracking key usage,
freezes, failures, and other status attributes. It utilizes a `ThreadPoolExecutor`
for non-blocking database writes, ensuring that core routing logic is not
delayed by I/O operations. All critical updates are designed to be atomic
to maintain data consistency in a high-concurrency environment.
"""
import json
import time
import threading
from typing import Any, Dict, List, Optional
import concurrent.futures

from src.backend._db import _LOCK, conn as _conn

_db_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_pending_lock = threading.Lock()
_pending_status: Dict[str, Dict[str, Any]] = {}
_pending_models: Dict[str, Dict[str, Dict[str, Any]]] = {}
_pending_deltas: Dict[str, Dict[str, int]] = {}
_pending_events: List[tuple] = []
_pending_penalties: Dict[str, tuple] = {}
_pending_usage: Dict[str, str] = {}
_worker_scheduled = False
_last_flush_error = ""


def _schedule_key_db_flush() -> None:
    """Coalesce hot key-state writes into one bounded, single-worker flush."""
    global _worker_scheduled
    with _pending_lock:
        if _worker_scheduled:
            return
        _worker_scheduled = True
    _db_executor.submit(_flush_key_db_writes)


def _flush_key_db_writes() -> None:
    global _worker_scheduled, _last_flush_error
    global _pending_status, _pending_models, _pending_deltas
    global _pending_events, _pending_penalties, _pending_usage
    failures = 0
    while True:
        with _pending_lock:
            statuses = _pending_status
            models = _pending_models
            deltas = _pending_deltas
            events = _pending_events
            penalties = _pending_penalties
            usage = _pending_usage
            _pending_status = {}
            _pending_models = {}
            _pending_deltas = {}
            _pending_events = []
            _pending_penalties = {}
            _pending_usage = {}
        try:
            if statuses or models or deltas or events or penalties or usage:
                with _LOCK:
                    c = _conn()
                    try:
                        for key, fields in deltas.items():
                            usage_delta = fields.get("usage", 0)
                            active_delta = fields.get("active_requests", 0)
                            c.execute(
                                """INSERT INTO key_status (key, usage, active_requests)
                                   VALUES (?, ?, MAX(0, ?))
                                   ON CONFLICT(key) DO UPDATE SET
                                     usage = usage + excluded.usage,
                                     active_requests = MAX(0, active_requests + ?)
                                """,
                                (key, usage_delta, max(0, active_delta), active_delta),
                            )
                        for key, fields in statuses.items():
                            assignments = ", ".join(f"{name} = ?" for name in fields)
                            c.execute(
                                f"UPDATE key_status SET {assignments} WHERE key = ?",
                                (*fields.values(), key),
                            )
                        for key, model_updates in models.items():
                            row = c.execute("SELECT per_model FROM key_status WHERE key = ?", (key,)).fetchone()
                            per_model = json.loads((row["per_model"] if row else "{}") or "{}")
                            for model_id, updates in model_updates.items():
                                entry = per_model.setdefault(model_id, {"failures": 0, "frozen_until": 0.0})
                                entry.update(updates)
                            c.execute("UPDATE key_status SET per_model = ? WHERE key = ?",
                                      (json.dumps(per_model), key))
                        for event in events:
                            request_id, transition_id, key_suffix, model_id, reason, metadata, created_at, key = event
                            c.execute(
                                """INSERT OR IGNORE INTO key_transitions
                                   (request_id, transition_id, key_suffix, model_id, reason, metadata, created_at)
                                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (request_id, transition_id, key_suffix, model_id, reason, metadata, created_at),
                            )
                            c.execute(
                                "UPDATE key_status SET last_transition_id=?, last_transition_at=?, last_transition_json=? WHERE key=?",
                                (transition_id, created_at, metadata, key),
                            )
                        # Cap audit tables: dashboard only reads the latest ~500
                        # rows per query, but nothing ever deleted. Keep 5000.
                        c.execute(
                            "DELETE FROM key_transitions WHERE id NOT IN "
                            "(SELECT id FROM key_transitions ORDER BY id DESC LIMIT 5000)"
                        )
                        for pkey, penalty in penalties.items():
                            c.execute(
                                """INSERT OR REPLACE INTO key_penalties
                                   (pkey, api_key, model_id, reason, expires, score_reduction)
                                   VALUES (?, ?, ?, ?, ?, ?)""", penalty,
                            )
                        if usage:
                            c.executemany(
                                "INSERT OR REPLACE INTO key_usage (key, data) VALUES (?, ?)",
                                usage.items(),
                            )
                        c.commit()
                    finally:
                        c.close()
            failures = 0
        except Exception as exc:
            failures += 1
            _last_flush_error = repr(exc)
            # Keep the latest state and counters for a bounded retry. Per-key
            # dictionaries cap memory at the number of known keys; transitions
            # are independently bounded below.
            with _pending_lock:
                for key, fields in statuses.items():
                    _pending_status.setdefault(key, {}).update(fields)
                for key, model_updates in models.items():
                    target = _pending_models.setdefault(key, {})
                    for model_id, updates in model_updates.items():
                        target.setdefault(model_id, {}).update(updates)
                for key, fields in deltas.items():
                    target = _pending_deltas.setdefault(key, {})
                    for name, value in fields.items():
                        target[name] = target.get(name, 0) + value
                _pending_events = (events + _pending_events)[-512:]
                _pending_penalties.update(penalties)
                _pending_usage.update(usage)
                if failures >= 3:
                    _worker_scheduled = False
                    return
            time.sleep(0.1)
        with _pending_lock:
            if not (_pending_status or _pending_models or _pending_deltas or _pending_events or _pending_penalties or _pending_usage):
                _worker_scheduled = False
                return


def flush_pending_writes(timeout: float = 5.0) -> None:
    """Wait until queued asynchronous key-status writes have completed."""
    deadline = time.monotonic() + timeout
    while True:
        with _pending_lock:
            scheduled = _worker_scheduled
            has_pending = bool(_pending_status or _pending_models or _pending_deltas
                               or _pending_events or _pending_penalties or _pending_usage)
        if has_pending and not scheduled:
            _schedule_key_db_flush()
        _db_executor.submit(lambda: None).result(timeout=max(0.01, deadline - time.monotonic()))
        with _pending_lock:
            empty = not (_pending_status or _pending_models or _pending_deltas
                         or _pending_events or _pending_penalties or _pending_usage)
            scheduled = _worker_scheduled
            last_error = _last_flush_error
        if empty and not scheduled:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out flushing pending key DB writes: {last_error}")
        time.sleep(0.005)


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
            now = time.time()
            dirty_rows = []
            for r in c.execute("SELECT * FROM key_status").fetchall():
                d = dict(r)
                
                # Check auto-reset if idle and unfrozen for more than 5 minutes (300s)
                dirty = False
                cf = d.get("consecutive_failures", 0) or 0
                frozen_until = d.get("frozen_until", 0.0) or 0.0
                if cf > 0 and now >= frozen_until + 300:
                    d["consecutive_failures"] = 0
                    d["frozen_until"] = 0.0
                    dirty = True

                pm = d.pop("data", None)
                pm_dict = {}
                if d.get("per_model"):
                    try:
                        pm_dict = json.loads(d["per_model"])
                    except (ValueError, TypeError):
                        # Corrupt or non-text per_model: an empty dict is the right
                        # fallback and the defaults below still apply. Narrow on
                        # purpose — a bare catch here hid real bugs in this loop.
                        pm_dict = {}
                    if not isinstance(pm_dict, dict):
                        # Valid JSON of the wrong shape, e.g. a list. This reached
                        # pm_dict.items() below and raised AttributeError out of an
                        # unprotected line, taking down every key read at startup.
                        pm_dict = {}
                    
                    pm_dirty = False
                    for mid, pm_entry in pm_dict.items():
                        if isinstance(pm_entry, dict) and pm_entry.get("failures", 0) > 0:
                            if now >= pm_entry.get("frozen_until", 0.0) + 300:
                                pm_entry["failures"] = 0
                                pm_entry["frozen_until"] = 0.0
                                pm_dirty = True
                    if pm_dirty:
                        d["per_model"] = json.dumps(pm_dict)
                        dirty = True
                    else:
                        d["per_model"] = pm_dict
                else:
                    d["per_model"] = {}

                if dirty:
                    pm_str = d["per_model"] if isinstance(d["per_model"], str) else json.dumps(d["per_model"])
                    dirty_rows.append((d.get("consecutive_failures", 0), d.get("frozen_until", 0.0), pm_str, d["key"]))
                    if not isinstance(d["per_model"], dict):
                        d["per_model"] = pm_dict

                if pm:
                    try:
                        old = json.loads(pm)
                        if isinstance(old, dict):
                            for k_old, v_old in old.items():
                                if k_old not in d or d[k_old] is None:
                                    d[k_old] = v_old
                    except (ValueError, TypeError):
                        # json.loads raises JSONDecodeError (a ValueError) on a
                        # corrupt per_model blob, and TypeError if the column is
                        # not text. Both are recoverable: skipping the merge is
                        # correct and the defaults below still apply. Anything
                        # else raised here is a real bug and should surface.
                        pass

                d.setdefault("enabled", 1)
                d.setdefault("project_id", "")
                d.setdefault("usage", 0)
                d.setdefault("active_requests", 0)
                d.setdefault("frozen_until", 0.0)
                d.setdefault("consecutive_failures", 0)
                d.setdefault("last_success", 0.0)
                d.setdefault("date", "")
                d.setdefault("today", 0)
                d.setdefault("tier", "free")
                result[d.pop("key")] = d
            if dirty_rows:
                # Reads never mutate persistent state. Queue the reset for the
                # same coalescing writer after releasing the shared DB lock.
                with _pending_lock:
                    for failures, frozen_until, per_model, key in dirty_rows:
                        _pending_status.setdefault(key, {}).update({
                            "consecutive_failures": failures,
                            "frozen_until": frozen_until,
                            "per_model": per_model,
                        })
                _schedule_key_db_flush()
            with _pending_lock:
                for key, delta in _pending_deltas.items():
                    if key in result:
                        result[key]["usage"] = int(result[key].get("usage", 0) or 0) + delta.get("usage", 0)
                        result[key]["active_requests"] = max(
                            0, int(result[key].get("active_requests", 0) or 0)
                            + delta.get("active_requests", 0),
                        )
                for key, fields in _pending_status.items():
                    if key in result:
                        result[key].update(fields)
                for key, model_updates in _pending_models.items():
                    if key in result:
                        per_model = result[key].setdefault("per_model", {})
                        for model_id, updates in model_updates.items():
                            per_model.setdefault(model_id, {"failures": 0, "frozen_until": 0.0}).update(updates)
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
    columns = ("enabled", "usage", "active_requests", "frozen_until",
               "consecutive_failures", "last_success", "date", "today", "per_model")
    with _pending_lock:
        for key, value in status.items():
            fields = _pending_status.setdefault(key, {})
            for column in columns:
                fields[column] = json.dumps(value.get("per_model", {})) if column == "per_model" else value.get(column, 0 if column in ("usage", "active_requests", "consecutive_failures", "last_success", "today") else "" if column in ("date",) else 1 if column == "enabled" else 0.0)
    _schedule_key_db_flush()


def atomic_reserve_key(key: str) -> None:
    """
    Atomically reserves an API key, incrementing its usage count and active requests count.
    This operation is crucial for ensuring that key usage is accurately tracked and
    concurrency limits are respected. It handles conflicts by updating existing entries.
    The write operation is asynchronous via `_db_executor`.

    Args:
        key: The API key to reserve.
    """
    with _pending_lock:
        delta = _pending_deltas.setdefault(key, {})
        delta["usage"] = delta.get("usage", 0) + 1
        delta["active_requests"] = delta.get("active_requests", 0) + 1
    _schedule_key_db_flush()


def atomic_release_key(key: str) -> None:
    """
    Atomically releases a previously reserved API key, decrementing its active requests count.
    This ensures that keys are properly freed up after a request is completed or fails,
    allowing them to be used by other concurrent requests. The write operation is asynchronous.

    Args:
        key: The API key to release.
    """
    with _pending_lock:
        delta = _pending_deltas.setdefault(key, {})
        delta["active_requests"] = delta.get("active_requests", 0) - 1
    _schedule_key_db_flush()


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
    if model_id:
        fields = {"consecutive_failures": cf or 1}
        model_updates = {model_id: {"frozen_until": until_ts, "failures": cf or 1}}
    else:
        fields = {"frozen_until": until_ts, "consecutive_failures": cf or 1}
        model_updates = {}
    with _pending_lock:
        _pending_status.setdefault(key, {}).update(fields)
        if model_updates:
            _pending_models.setdefault(key, {}).update(model_updates)
        delta = _pending_deltas.setdefault(key, {})
        delta["active_requests"] = delta.get("active_requests", 0) - 1
    _schedule_key_db_flush()


def atomic_record_success(key: str, model_id: Optional[str] = None) -> None:
    """
    Atomically records a successful API call for a specific key and model.
    This resets the `consecutive_failures` count and updates the `last_success` timestamp
    for the key (globally and per-model). The write operation is asynchronous via `_db_executor`.

    Args:
        key: The API key that was used successfully.
        model_id: Optional concrete model ID that was used successfully. If None, global status is updated.
    """
    fields = {"frozen_until": 0.0, "consecutive_failures": 0, "last_success": time.time()}
    with _pending_lock:
        _pending_status.setdefault(key, {}).update(fields)
        if model_id:
            _pending_models.setdefault(key, {})[model_id] = {"failures": 0, "frozen_until": 0.0}
    _schedule_key_db_flush()


def register_keys_in_db(keys: List[str]) -> None:
    def _write():
        with _LOCK:
            c = _conn()
            try:
                c.execute("DELETE FROM key_status WHERE key LIKE 'oauth:%'")
                if keys:
                    c.executemany("INSERT OR IGNORE INTO key_status (key) VALUES (?)", ((key,) for key in keys))
                    placeholders = ",".join("?" for _ in keys)
                    c.execute(f"DELETE FROM key_status WHERE key NOT IN ({placeholders})", keys)
                c.commit()
            finally:
                c.close()
    _db_executor.submit(_write)


def update_key_project_db(key: str, project_id: str) -> None:
    """Persist discovered Google Cloud project_id for an API key."""
    if not key or not project_id:
        return
    with _pending_lock:
        _pending_status.setdefault(key, {})["project_id"] = str(project_id).strip()
    _schedule_key_db_flush()


def set_key_tier_batch_db(tiers: Dict[str, str]) -> None:
    with _pending_lock:
        for key, tier in tiers.items():
            _pending_status.setdefault(key, {})["tier"] = tier
    _schedule_key_db_flush()


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
    with _pending_lock:
        _pending_usage.update({key: json.dumps(value) for key, value in usage.items()})
    _schedule_key_db_flush()


def db_save_penalty(pkey: str, api_key: str, model_id: Optional[str], reason: str, expires: float, score_reduction: int) -> None:
    with _pending_lock:
        _pending_penalties[pkey] = (pkey, api_key, model_id or "__global__", reason, expires, score_reduction)
    _schedule_key_db_flush()


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
    with _pending_lock:
        _pending_status.setdefault(key, {})["enabled"] = 0
    _schedule_key_db_flush()


def record_key_transition(
    *,
    transition_id: str,
    request_id: str = "",
    key: str = "",
    model_id: Optional[str] = None,
    reason: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    created_at: Optional[float] = None,
) -> None:
    """Persist an auditable provider-key state transition.

    The key itself is never stored in the event table; only its suffix is kept
    for correlation without leaking credentials.
    """
    payload = json.dumps(metadata or {}, ensure_ascii=False, default=str)
    ts = created_at or time.time()

    event = (request_id, transition_id, (key or "")[-8:], model_id or "", reason, payload, ts, key)
    with _pending_lock:
        _pending_events.append(event)
        if len(_pending_events) > 512:
            del _pending_events[:-512]
        _pending_status.setdefault(key, {}).update({
            "last_transition_id": transition_id,
            "last_transition_at": ts,
            "last_transition_json": payload,
        })
    _schedule_key_db_flush()


def query_key_transitions_db(
    *,
    request_id: Optional[str] = None,
    key_suffix: Optional[str] = None,
    model_id: Optional[str] = None,
    reason: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Query recent key transitions with optional filtering."""
    with _LOCK:
        c = _conn()
        try:
            clauses = []
            params: list = []
            if request_id:
                clauses.append("request_id = ?")
                params.append(request_id)
            if key_suffix:
                clauses.append("key_suffix = ?")
                params.append(key_suffix)
            if model_id:
                clauses.append("model_id = ?")
                params.append(model_id)
            if reason:
                clauses.append("reason = ?")
                params.append(reason)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            query = f"SELECT * FROM key_transitions {where} ORDER BY id DESC LIMIT ?"
            params.append(min(max(1, limit), 500))
            rows = c.execute(query, params).fetchall()
            results = []
            for r in rows:
                row_dict = dict(r)
                if row_dict.get("metadata"):
                    try:
                        row_dict["metadata"] = json.loads(row_dict["metadata"])
                    except Exception:
                        pass
                results.append(row_dict)
            return results
        finally:
            c.close()


def query_endpoint_transitions_db(
    *,
    request_id: Optional[str] = None,
    endpoint: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Query recent endpoint transitions with optional filtering."""
    with _LOCK:
        c = _conn()
        try:
            clauses = []
            params: list = []
            if request_id:
                clauses.append("request_id = ?")
                params.append(request_id)
            if endpoint:
                clauses.append("endpoint = ?")
                params.append(endpoint)
            if state:
                clauses.append("state = ?")
                params.append(state)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            query = f"SELECT * FROM endpoint_transitions {where} ORDER BY id DESC LIMIT ?"
            params.append(min(max(1, limit), 500))
            rows = c.execute(query, params).fetchall()
            results = []
            for r in rows:
                row_dict = dict(r)
                if row_dict.get("metadata"):
                    try:
                        row_dict["metadata"] = json.loads(row_dict["metadata"])
                    except Exception:
                        pass
                results.append(row_dict)
            return results
        finally:
            c.close()


def reset_active_requests_db() -> None:
    """
    Resets the `active_requests` count to 0 for all API keys in the database.
    This is a global reset operation, typically used for system-wide recovery or
    maintenance to clear any stale active request counts.
    """
    flush_pending_writes(timeout=5.0)
    _db_executor.submit(_reset_active_requests_sync).result(timeout=5.0)


def _reset_active_requests_sync() -> None:
    with _LOCK:
        c = _conn()
        try:
            c.execute("UPDATE key_status SET active_requests = 0")
            c.commit()
        finally:
            c.close()


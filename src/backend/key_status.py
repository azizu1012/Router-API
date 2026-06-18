import json
import time
from typing import Any, Dict, List, Optional
import concurrent.futures

from src.backend._db import _LOCK, conn as _conn

_db_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)


def get_key_status_db() -> Dict[str, Dict[str, Any]]:
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
    with _LOCK:
        c = _conn()
        try:
            c.execute("UPDATE key_status SET active_requests = 0")
            c.commit()
        finally:
            c.close()


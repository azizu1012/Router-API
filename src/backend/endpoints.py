import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.backend._db import _LOCK, conn as _conn


def _endpoint_row(r) -> Dict[str, Any]:
    d = dict(r)
    d["models"] = json.loads(d.get("models", "[]"))
    pa = json.loads(d.get("pool_assignments", "{}"))
    for mid, pools in pa.items():
        if isinstance(pools, str):
            pa[mid] = [pools]
    d["pool_assignments"] = pa
    d["enabled"] = bool(d["enabled"])
    d["fallback"] = bool(d["fallback"])
    return d


def list_endpoints_db() -> List[Dict[str, Any]]:
    with _LOCK:
        c = _conn()
        try:
            return [_endpoint_row(r) for r in c.execute("SELECT * FROM custom_endpoints").fetchall()]
        finally:
            c.close()


def get_endpoint_db(name: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        c = _conn()
        try:
            r = c.execute("SELECT * FROM custom_endpoints WHERE name = ?", (name,)).fetchone()
            return _endpoint_row(r) if r else None
        finally:
            c.close()


def add_endpoint_db(name: str, base_url: str, auth_key: str) -> Dict[str, Any]:
    name = name.strip().lower().replace(" ", "-")
    if not name:
        raise ValueError("Name is required")
    if get_endpoint_db(name):
        raise ValueError(f"Endpoint '{name}' already exists")
    ep = {
        "name": name,
        "base_url": base_url.rstrip("/"),
        "auth_key": auth_key,
        "enabled": True,
        "models": [],
        "pool_assignments": {},
        "fallback": False,
        "updated_at": datetime.utcnow().isoformat(),
    }
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT INTO custom_endpoints
                   (name, base_url, auth_key, enabled, models, pool_assignments, fallback, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (ep["name"], ep["base_url"], ep["auth_key"],
                 1, "[]", "{}", 0, ep["updated_at"]),
            )
            c.commit()
        finally:
            c.close()
    return ep


def remove_endpoint_db(name: str) -> Optional[Dict[str, Any]]:
    ep = get_endpoint_db(name)
    if not ep:
        return None
    with _LOCK:
        c = _conn()
        try:
            c.execute("DELETE FROM custom_endpoints WHERE name = ?", (name,))
            c.commit()
        finally:
            c.close()
    return ep


def enable_endpoint_db(name: str) -> Optional[Dict[str, Any]]:
    ep = get_endpoint_db(name)
    if not ep:
        return None
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "UPDATE custom_endpoints SET enabled = 1, updated_at = ? WHERE name = ?",
                (datetime.utcnow().isoformat(), name),
            )
            c.commit()
        finally:
            c.close()
    ep["enabled"] = True
    return ep


def disable_endpoint_db(name: str) -> Optional[Dict[str, Any]]:
    ep = get_endpoint_db(name)
    if not ep:
        return None
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "UPDATE custom_endpoints SET enabled = 0, updated_at = ? WHERE name = ?",
                (datetime.utcnow().isoformat(), name),
            )
            c.commit()
        finally:
            c.close()
    ep["enabled"] = False
    return ep


def update_endpoint_db(name: str, **updates: Any) -> Optional[Dict[str, Any]]:
    ep = get_endpoint_db(name)
    if not ep:
        return None
    ep.update(updates)
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """UPDATE custom_endpoints SET base_url=?, auth_key=?, enabled=?, models=?,
                   pool_assignments=?, fallback=?, updated_at=? WHERE name=?""",
                (ep["base_url"], ep["auth_key"],
                 1 if ep.get("enabled", True) else 0,
                 json.dumps(ep.get("models", [])),
                 json.dumps(ep.get("pool_assignments", {})),
                 1 if ep.get("fallback", False) else 0,
                 datetime.utcnow().isoformat(), name),
            )
            c.commit()
        finally:
            c.close()
    return ep


def set_fallback_db(name: str, enabled: bool) -> Optional[Dict[str, Any]]:
    ep = get_endpoint_db(name)
    if not ep:
        return None
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "UPDATE custom_endpoints SET fallback = ?, updated_at = ? WHERE name = ?",
                (1 if enabled else 0, datetime.utcnow().isoformat(), name),
            )
            c.commit()
        finally:
            c.close()
    ep["fallback"] = enabled
    return ep


def assign_to_pool_db(name: str, model_id: str, pool_name: str) -> None:
    ep = get_endpoint_db(name)
    if not ep:
        raise ValueError(f"Endpoint '{name}' not found")
    if model_id not in ep.get("models", []):
        raise ValueError(f"Model '{model_id}' not found in endpoint '{name}'")
    if pool_name not in ("gemini-flash", "gemini-flash-lite"):
        raise ValueError(f"Pool must be 'gemini-flash' or 'gemini-flash-lite'")
    pa = ep.get("pool_assignments", {})
    pools = pa.get(model_id, [])
    if isinstance(pools, str):
        pools = [pools]
    if pool_name not in pools:
        pools.append(pool_name)
    pa[model_id] = pools
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "UPDATE custom_endpoints SET pool_assignments = ?, updated_at = ? WHERE name = ?",
                (json.dumps(pa), datetime.utcnow().isoformat(), name),
            )
            c.commit()
        finally:
            c.close()


def remove_from_pool_db(name: str, model_id: str) -> None:
    ep = get_endpoint_db(name)
    if not ep:
        raise ValueError(f"Endpoint '{name}' not found")
    pa = ep.get("pool_assignments", {})
    pa.pop(model_id, None)
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "UPDATE custom_endpoints SET pool_assignments = ?, updated_at = ? WHERE name = ?",
                (json.dumps(pa), datetime.utcnow().isoformat(), name),
            )
            c.commit()
        finally:
            c.close()

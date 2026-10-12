import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.backend._db import _LOCK, conn as _conn


def _endpoint_row(r) -> Dict[str, Any]:
    d = dict(r)
    d["models"] = json.loads(d.get("models") or "[]")
    d["disabled_models"] = json.loads(d.get("disabled_models") or "[]")
    d["enabled_models"] = json.loads(d.get("enabled_models") or "[]")
    d["enabled"] = bool(d["enabled"])
    d["fallback"] = bool(d.get("fallback", 0))
    d["account_id"] = d.get("account_id") or ""
    d["account_key_id"] = d.get("account_key_id") or ""
    # An endpoint belongs to whoever paid for it; openai unless told otherwise.
    d["api_format"] = (d.get("api_format") or "openai").strip().lower()
    d["pool_assignments"] = json.loads(d.get("pool_assignments") or "{}")
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


def get_endpoint_by_account_db(account_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        c = _conn()
        try:
            r = c.execute(
                "SELECT * FROM custom_endpoints WHERE account_id = ? AND enabled = 1",
                (account_id,),
            ).fetchone()
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
        "disabled_models": [],
        "enabled_models": [],
        "account_id": "",
        "account_key_id": "",
        "api_format": "openai",
        "fallback": False,
        "pool_assignments": {},
        "updated_at": datetime.utcnow().isoformat(),
    }
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT INTO custom_endpoints
                   (name, base_url, auth_key, enabled, models, disabled_models, enabled_models, account_id, account_key_id, fallback, pool_assignments, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ep["name"], ep["base_url"], ep["auth_key"],
                 1, "[]", "[]", "[]", "", "", 0, "{}", ep["updated_at"]),
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
    allowed = {"base_url", "auth_key", "enabled", "models", "disabled_models", "enabled_models", "account_id", "account_key_id", "fallback", "pool_assignments", "api_format"}
    merged = {k: v for k, v in updates.items() if k in allowed}
    ep.update(merged)
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """UPDATE custom_endpoints SET base_url=?, auth_key=?, enabled=?, models=?, disabled_models=?, enabled_models=?,
                      account_id=?, account_key_id=?, fallback=?, pool_assignments=?, api_format=?, updated_at=? WHERE name=?""",
                (ep["base_url"], ep["auth_key"],
                 1 if ep.get("enabled", True) else 0,
                 json.dumps(ep.get("models") or []),
                 json.dumps(ep.get("disabled_models") or []),
                 json.dumps(ep.get("enabled_models") or []),
                 ep.get("account_id", ""),
                 ep.get("account_key_id", ""),
                 1 if ep.get("fallback", False) else 0,
                 json.dumps(ep.get("pool_assignments") or {}),
                 ep.get("api_format") or "openai",
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


def get_endpoints_by_account_db(account_id: str, include_disabled: bool = False) -> List[Dict[str, Any]]:
    """Get custom endpoints owned by account.

    Args:
        account_id: Owner account ID
        include_disabled: Include disabled endpoints

    Returns:
        List of endpoint dicts
    """
    with _LOCK:
        c = _conn()
        try:
            if include_disabled:
                return [_endpoint_row(r) for r in c.execute(
                    "SELECT * FROM custom_endpoints WHERE account_id = ?",
                    (account_id,),
                ).fetchall()]
            else:
                return [_endpoint_row(r) for r in c.execute(
                    "SELECT * FROM custom_endpoints WHERE account_id = ? AND enabled = 1",
                    (account_id,),
                ).fetchall()]
        finally:
            c.close()


def assign_endpoint_to_account_db(name: str, account_id: str) -> Optional[Dict[str, Any]]:
    """Assign endpoint to account (admin only)."""
    return update_endpoint_db(name, account_id=account_id)


def add_member_endpoint_db(
    name: str,
    base_url: str,
    auth_key: str,
    account_id: str,
    api_format: str = "openai",
    enabled: bool = True,
) -> Dict[str, Any]:
    """Add custom endpoint owned by member.

    Args:
        name: Endpoint name (will be sanitized)
        base_url: API base URL
        auth_key: API key
        account_id: Owner account ID
        api_format: API format (openai, anthropic, etc.)
        enabled: Initial enabled state

    Returns:
        Created endpoint dict

    Raises:
        ValueError: If name already exists
    """
    name = name.strip().lower().replace(" ", "-")
    if not name:
        raise ValueError("Name is required")
    if get_endpoint_db(name):
        raise ValueError(f"Endpoint '{name}' already exists")

    ep = {
        "name": name,
        "base_url": base_url.rstrip("/"),
        "auth_key": auth_key,
        "enabled": enabled,
        "models": [],
        "disabled_models": [],
        "enabled_models": [],
        "account_id": account_id,
        "account_key_id": "",
        "api_format": api_format,
        "fallback": False,
        "pool_assignments": {},
        "updated_at": datetime.utcnow().isoformat(),
    }
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT INTO custom_endpoints
                   (name, base_url, auth_key, enabled, models, disabled_models, enabled_models, account_id, account_key_id, fallback, api_format, pool_assignments, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ep["name"],
                    ep["base_url"],
                    ep["auth_key"],
                    1 if enabled else 0,
                    "[]",
                    "[]",
                    "[]",
                    account_id,
                    "",
                    0,
                    api_format,
                    "{}",
                    ep["updated_at"],
                ),
            )
            c.commit()
        finally:
            c.close()
    return ep

    ep = get_endpoint_db(name)
    if not ep:
        return None
    return update_endpoint_db(name, account_id=account_id)


def assign_endpoint_to_key_db(name: str, account_key_id: str) -> Optional[Dict[str, Any]]:
    """Assign endpoint to a specific account key instead of whole account.

    This allows admin to have fine-grained control: endpoint only works when
    that specific key_id is used. Useful for separating prod/dev keys or
    different teams within same account.
    """
    ep = get_endpoint_db(name)
    if not ep:
        return None
    return update_endpoint_db(name, account_key_id=account_key_id, account_id="")


def get_endpoint_by_key_db(account_key_id: str) -> Optional[Dict[str, Any]]:
    """Get custom endpoint assigned to a specific key."""
    with _LOCK:
        c = _conn()
        try:
            r = c.execute(
                "SELECT * FROM custom_endpoints WHERE account_key_id = ? AND enabled = 1",
                (account_key_id,),
            ).fetchone()
            return _endpoint_row(r) if r else None
        finally:
            c.close()

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.backend._db import _LOCK, conn as _conn


def _normalize_model_contexts(raw: Any) -> Dict[str, int]:
    """Normalize per-model context windows to lowercase-stripped keys.

    Lookups happen by model id, which arrives in mixed case from clients
    (``Muse-1.3`` vs ``muse-1.3``). Normalizing once at the boundary —
    both when reading a row and when saving one — means readers do a
    single dict hit instead of trying spellings.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    normalized: Dict[str, int] = {}
    for k, v in raw.items():
        key = str(k or "").strip().lower()
        if key and key not in normalized and _positive_int(v):
            normalized[key] = int(v)
    return normalized


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
    d["rpm"] = int(d.get("rpm") or 0) if str(d.get("rpm") or 0).lstrip("-").isdigit() else 0
    d["timeout"] = int(d.get("timeout") or 0) if str(d.get("timeout") or 0).lstrip("-").isdigit() else 0
    d["pool_assignments"] = json.loads(d.get("pool_assignments") or "{}")
    d["model_contexts"] = _normalize_model_contexts(d.get("model_contexts"))
    try:
        d["context_length"] = int(d.get("context_length") or 0)
    except (TypeError, ValueError):
        d["context_length"] = 0
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


def add_endpoint_db(name: str, base_url: str, auth_key: str, rpm: int = 0, timeout: int = 0) -> Dict[str, Any]:
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
        "rpm": int(rpm or 0) if str(rpm or 0).lstrip("-").isdigit() else 0,
        "timeout": int(timeout or 0) if str(timeout or 0).lstrip("-").isdigit() else 0,
        "fallback": False,
        "pool_assignments": {},
        "model_contexts": {},
        "context_length": 0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT INTO custom_endpoints
                   (name, base_url, auth_key, enabled, models, disabled_models, enabled_models, account_id, account_key_id, rpm, timeout, fallback, pool_assignments, model_contexts, context_length, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ep["name"], ep["base_url"], ep["auth_key"],
                 1, "[]", "[]", "[]", "", "", ep["rpm"], ep["timeout"], 0, "{}", "{}", 0, ep["updated_at"]),
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
                (datetime.now(timezone.utc).isoformat(), name),
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
                (datetime.now(timezone.utc).isoformat(), name),
            )
            c.commit()
        finally:
            c.close()
    ep["enabled"] = False
    return ep


def _positive_int(value: Any) -> bool:
    """True when value parses to a positive int (a usable context window)."""
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def update_endpoint_db(name: str, **updates: Any) -> Optional[Dict[str, Any]]:
    ep = get_endpoint_db(name)
    if not ep:
        return None
    allowed = {"base_url", "auth_key", "enabled", "models", "disabled_models", "enabled_models", "account_id", "account_key_id", "fallback", "pool_assignments", "api_format", "rpm", "timeout", "model_contexts", "context_length"}
    merged = {k: v for k, v in updates.items() if k in allowed}
    ep.update(merged)
    ep["rpm"] = int(ep.get("rpm") or 0) if str(ep.get("rpm") or 0).lstrip("-").isdigit() else 0
    ep["timeout"] = int(ep.get("timeout") or 0) if str(ep.get("timeout") or 0).lstrip("-").isdigit() else 0
    ep["model_contexts"] = _normalize_model_contexts(ep.get("model_contexts"))
    try:
        ep["context_length"] = int(ep.get("context_length") or 0)
    except (TypeError, ValueError):
        ep["context_length"] = 0
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """UPDATE custom_endpoints SET base_url=?, auth_key=?, enabled=?, models=?, disabled_models=?, enabled_models=?,
                      account_id=?, account_key_id=?, rpm=?, timeout=?, fallback=?, pool_assignments=?, api_format=?, model_contexts=?, context_length=?, updated_at=? WHERE name=?""",
                (ep["base_url"], ep["auth_key"],
                 1 if ep.get("enabled", True) else 0,
                 json.dumps(ep.get("models") or []),
                 json.dumps(ep.get("disabled_models") or []),
                 json.dumps(ep.get("enabled_models") or []),
                 ep.get("account_id", ""),
                 ep.get("account_key_id", ""),
                 ep.get("rpm", 0),
                 ep.get("timeout", 0),
                 1 if ep.get("fallback", False) else 0,
                 json.dumps(ep.get("pool_assignments") or {}),
                 ep.get("api_format") or "openai",
                 json.dumps(ep.get("model_contexts") or {}),
                 int(ep.get("context_length") or 0),
                 datetime.now(timezone.utc).isoformat(), name),
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
                (1 if enabled else 0, datetime.now(timezone.utc).isoformat(), name),
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
        "model_contexts": {},
        "context_length": 0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT INTO custom_endpoints
                   (name, base_url, auth_key, enabled, models, disabled_models, enabled_models, account_id, account_key_id, fallback, api_format, pool_assignments, model_contexts, context_length, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    "{}",
                    0,
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

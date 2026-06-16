import secrets
import time
from typing import Any, Dict, List, Optional

from src.core.config_n_logg import config
from src.backend._db import _LOCK, conn as _conn


def list_accounts_db(include_disabled: bool = True) -> List[Dict[str, Any]]:
    with _LOCK:
        c = _conn()
        try:
            if include_disabled:
                cur = c.execute("SELECT * FROM accounts")
            else:
                cur = c.execute("SELECT * FROM accounts WHERE enabled = 1")
            return [dict(r) for r in cur.fetchall()]
        finally:
            c.close()


def find_account_by_name(name: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        c = _conn()
        try:
            cur = c.execute("SELECT * FROM accounts WHERE LOWER(name) = LOWER(?)", (name,))
            r = cur.fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def find_account_by_key(auth_key: str) -> Optional[Dict[str, Any]]:
    raw = str(auth_key or "").strip()
    if not raw:
        return None
    for acc in list_accounts_db(include_disabled=False):
        auth_key_db = acc.get("auth_key") or ""
        if secrets.compare_digest(auth_key_db.encode("utf-8"), raw.encode("utf-8")):
            return acc
    return None


def create_account_db(
    name: str,
    rpm: Optional[int] = None,
    tpm: Optional[int] = None,
    rpd: Optional[int] = None,
    tier: str = "free",
    search_engine: str = "auto",
) -> Dict[str, Any]:
    clean = str(name or "").strip()
    if not clean:
        raise ValueError("Account name is required.")
    if find_account_by_name(clean):
        raise ValueError(f"Account already exists: {clean}")
    now = int(time.time())
    account = {
        "account_id": secrets.token_hex(8),
        "name": clean,
        "auth_key": "sk-" + secrets.token_urlsafe(32),
        "enabled": True,
        "tier": tier,
        "rpm": int(config.DEFAULT_ACCOUNT_RPM if rpm is None else rpm),
        "tpm": int(config.DEFAULT_ACCOUNT_TPM if tpm is None else tpm),
        "rpd": int(config.DEFAULT_ACCOUNT_RPD if rpd is None else rpd),
        "web_search_enabled": False,
        "search_engine": search_engine or "auto",
        "created_at": now,
        "updated_at": now,
    }
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT INTO accounts
                   (account_id, name, auth_key, enabled, tier, rpm, tpm, rpd, web_search_enabled, search_engine, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (account["account_id"], account["name"], account["auth_key"],
                 1, account["tier"], account["rpm"], account["tpm"], account["rpd"],
                 0, account["search_engine"], account["created_at"], account["updated_at"]),
            )
            c.commit()
        finally:
            c.close()
    return account


def update_account_db(name: str, **updates: Any) -> Dict[str, Any]:
    clean = str(name or "").strip()
    existing = find_account_by_name(clean)
    if not existing:
        raise ValueError(f"Account not found: {clean}")
    updates["updated_at"] = int(time.time())
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """UPDATE accounts SET auth_key=?, enabled=?, tier=?, rpm=?, tpm=?, rpd=?, web_search_enabled=?, search_engine=?, updated_at=?
                   WHERE name=?""",
                (updates.get("auth_key", existing["auth_key"]),
                 1 if updates.get("enabled", existing["enabled"]) else 0,
                 updates.get("tier", existing.get("tier", "free")),
                 updates.get("rpm", existing["rpm"]),
                 updates.get("tpm", existing["tpm"]),
                 updates.get("rpd", existing["rpd"]),
                 1 if updates.get("web_search_enabled", existing.get("web_search_enabled", 0)) else 0,
                 updates.get("search_engine", existing.get("search_engine", "auto")),
                 updates["updated_at"], clean),
            )
            c.commit()
        finally:
            c.close()
    result = dict(existing)
    result.update(updates)
    return result


def delete_account_db(name: str) -> Dict[str, Any]:
    clean = str(name or "").strip()
    existing = find_account_by_name(clean)
    if not existing:
        raise ValueError(f"Account not found: {clean}")
    with _LOCK:
        c = _conn()
        try:
            c.execute("DELETE FROM accounts WHERE LOWER(name) = LOWER(?)", (clean,))
            c.commit()
        finally:
            c.close()
    return existing

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.backend._db import _LOCK, conn as _conn


def _row_to_dict(r) -> Dict[str, Any]:
    d = dict(r)
    d["hidden"] = bool(d.get("hidden", 0))
    d["enabled"] = bool(d.get("enabled", 1))
    d["rpd_enabled"] = bool(d.get("rpd_enabled", 0))
    d["account_id"] = d.get("account_id") or ""
    return d


def list_model_configs(account_id: Optional[str] = None) -> List[Dict[str, Any]]:
    with _LOCK:
        c = _conn()
        try:
            if account_id:
                return [_row_to_dict(r) for r in c.execute(
                    "SELECT * FROM model_config WHERE account_id = ? ORDER BY alias", (account_id,)
                ).fetchall()]
            return [_row_to_dict(r) for r in c.execute("SELECT * FROM model_config ORDER BY alias").fetchall()]
        finally:
            c.close()


def get_model_config(alias: str, account_id: str = "") -> Optional[Dict[str, Any]]:
    with _LOCK:
        c = _conn()
        try:
            r = c.execute(
                "SELECT * FROM model_config WHERE alias = ? AND account_id = ?",
                (alias, account_id),
            ).fetchone()
            return _row_to_dict(r) if r else None
        finally:
            c.close()


def save_model_config(alias: str, **updates: Any) -> Dict[str, Any]:
    alias = alias.strip().lower()
    if not alias:
        raise ValueError("alias is required")

    account_id = updates.pop("account_id", "") or ""
    existing = get_model_config(alias, account_id)
    now = datetime.now(timezone.utc).isoformat()

    allowed = {"display", "model_id", "rpm", "tpm", "rpd", "rpd_enabled",
               "hidden", "priority", "context_length", "pool_name", "enabled"}

    merged = {
        "alias": alias,
        "account_id": account_id,
        "display": "",
        "model_id": "",
        "rpm": 10,
        "tpm": 1000000,
        "rpd": 1000,
        "rpd_enabled": False,
        "hidden": False,
        "priority": 1,
        "context_length": 220000,
        "pool_name": "",
        "enabled": True,
        "updated_at": now,
    }
    if existing:
        merged.update({k: v for k, v in existing.items() if k in merged})
    merged.update({k: v for k, v in updates.items() if k in allowed})
    merged["updated_at"] = now

    with _LOCK:
        c = _conn()
        try:
            cur = c.execute(
                """UPDATE model_config SET display=?, model_id=?, rpm=?, tpm=?, rpd=?, rpd_enabled=?,
                   hidden=?, priority=?, context_length=?, pool_name=?, enabled=?, updated_at=?
                   WHERE alias=? AND account_id=?""",
                (merged["display"], merged["model_id"],
                 int(merged["rpm"]), int(merged["tpm"]), int(merged["rpd"]),
                 1 if merged["rpd_enabled"] else 0,
                 1 if merged["hidden"] else 0,
                 int(merged["priority"]), int(merged["context_length"]),
                 merged["pool_name"] or "",
                 1 if merged["enabled"] else 0,
                 merged["updated_at"],
                 merged["alias"], merged["account_id"]),
            )
            if cur.rowcount == 0:
                c.execute(
                    """INSERT INTO model_config
                       (alias, account_id, display, model_id, rpm, tpm, rpd, rpd_enabled,
                        hidden, priority, context_length, pool_name, enabled, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (merged["alias"], merged["account_id"],
                     merged["display"], merged["model_id"],
                     int(merged["rpm"]), int(merged["tpm"]), int(merged["rpd"]),
                     1 if merged["rpd_enabled"] else 0,
                     1 if merged["hidden"] else 0,
                     int(merged["priority"]), int(merged["context_length"]),
                     merged["pool_name"] or "",
                     1 if merged["enabled"] else 0,
                     merged["updated_at"]),
                )
            c.commit()
        finally:
            c.close()
    return merged


def delete_model_config(alias: str, account_id: str = "") -> Optional[Dict[str, Any]]:
    existing = get_model_config(alias, account_id)
    if not existing:
        return None
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "DELETE FROM model_config WHERE alias = ? AND account_id = ?",
                (alias, account_id),
            )
            c.commit()
        finally:
            c.close()
    return existing


def load_all_model_configs() -> Dict[str, Dict[str, Any]]:
    result = {}
    for row in list_model_configs():
        if row.get("account_id"):
            continue
        alias = row["alias"]
        result[alias] = {
            "display": row.get("display", ""),
            "priority": row.get("priority", 1),
            "model_id": row.get("model_id", alias),
            "rpm": row.get("rpm", 10),
            "tpm": row.get("tpm", 1000000),
            "rpd": row.get("rpd", 1000),
            "rpd_enabled": row.get("rpd_enabled", False),
            "context_length": row.get("context_length", 220000),
            "hidden": row.get("hidden", False),
            "pool_name": row.get("pool_name", ""),
            "enabled": row.get("enabled", True),
        }
    return result


def sync_default_models_to_db(force: bool = False) -> int:
    """Đồng bộ các model cấu hình chuẩn (6 Flash, 3 Lite, 2 Pools) vào bảng model_config trong SQLite.
    Nếu model chưa có trong DB, sẽ tự động chèn mới.
    Nếu force=True hoặc là pool mang tên hiển thị cũ (outdated display), sẽ cập nhật lại.
    """
    from src.core.api_config import AVAILABLE_MODELS, MODEL_POOLS

    alias_to_pool = {}
    for pn, pc in MODEL_POOLS.items():
        for m in pc.get("members", []):
            alias_to_pool[m] = pn

    now = datetime.now(timezone.utc).isoformat()
    synced_count = 0

    with _LOCK:
        c = _conn()
        try:
            for alias, cfg in AVAILABLE_MODELS.items():
                cur = c.execute(
                    "SELECT display, rpm, tpm, rpd FROM model_config WHERE alias = ? AND account_id = ''",
                    (alias,),
                )
                existing = cur.fetchone()
                pool_name = "" if alias in MODEL_POOLS else alias_to_pool.get(alias, "")
                display = cfg.get("display", alias)
                model_id = cfg.get("model_id", alias)
                rpm = int(cfg.get("rpm", 10))
                tpm = int(cfg.get("tpm", 1000000))
                rpd = int(cfg.get("rpd", 1000))
                hidden = 1 if cfg.get("hidden", False) else 0
                priority = int(cfg.get("priority", 1))
                context_length = int(cfg.get("context_length", 220000))

                # Cập nhật nếu chưa có, hoặc nếu force=True, hoặc nếu display trong DB không khớp với env
                needs_update = force or (existing and existing["display"] != display)

                if not existing:
                    c.execute(
                        """INSERT INTO model_config
                           (alias, account_id, display, model_id, rpm, tpm, rpd, rpd_enabled,
                            hidden, priority, context_length, pool_name, enabled, updated_at)
                           VALUES (?, '', ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 1, ?)""",
                        (alias, display, model_id, rpm, tpm, rpd, hidden, priority, context_length, pool_name, now),
                    )
                    synced_count += 1
                elif needs_update:
                    c.execute(
                        """UPDATE model_config SET
                           display = ?, model_id = ?, rpm = ?, tpm = ?, rpd = ?,
                           hidden = ?, priority = ?, context_length = ?, pool_name = ?, updated_at = ?
                           WHERE alias = ? AND account_id = ''""",
                        (display, model_id, rpm, tpm, rpd, hidden, priority, context_length, pool_name, now, alias),
                    )
                    synced_count += 1
            c.commit()
        finally:
            c.close()
    return synced_count


def sync_env_to_db() -> int:
    """Đồng bộ toàn bộ cấu hình model từ ENV vào database SQLite (model_config).
    Đảm bảo khi .env thay đổi thì database được cập nhật ngay lập tức.
    """
    return sync_default_models_to_db(force=True)



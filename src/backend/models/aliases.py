"""Backend CRUD operations for model_aliases table with Combo Model Mix and Spoofing support."""

import json
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, Union

from src.backend._db import _LOCK, conn as _conn

_ALIAS_COLS = (
    "alias_id, account_id, account_key_id, alias_name, target_model, target_endpoint, "
    "enabled, label, created_at, updated_at, targets, strategy, context_length"
)
_ALIAS_LEGACY_COLS = (
    "alias_id, account_id, account_key_id, alias_name, target_model, target_endpoint, "
    "enabled, label, created_at, updated_at"
)


def _row_to_alias(row: tuple) -> Dict[str, Any]:
    return {
        "alias_id": row[0],
        "account_id": row[1],
        "account_key_id": row[2],
        "alias_name": row[3],
        "target_model": row[4],
        "target_endpoint": row[5],
        "enabled": bool(row[6]),
        "label": row[7],
        "created_at": row[8],
        "updated_at": row[9],
        "targets": row[10] if len(row) > 10 and row[10] is not None else "",
        "strategy": row[11] if len(row) > 11 and row[11] is not None else "waterfall",
        "context_length": row[12] if len(row) > 12 and row[12] is not None else 0,
    }


def add_alias_db(
    account_id: str,
    alias_name: str,
    target_model: str,
    account_key_id: Optional[str] = None,
    target_endpoint: Optional[str] = None,
    enabled: bool = True,
    label: str = "",
    targets: Union[str, List[Any], Dict[str, Any]] = "",
    strategy: str = "waterfall",
    context_length: int = 0,
) -> str:
    """Add a new model alias or combo mix.

    Args:
        account_id: Owner account ID
        alias_name: Name user will call (e.g., "claude-3-5-sonnet")
        target_model: Actual model to route to (e.g., "gemini-flash")
        account_key_id: Key-specific alias (None = account-wide)
        target_endpoint: Custom endpoint name (None = use pool)
        enabled: Whether alias is active
        label: User description
        targets: Optional list/JSON of combo targets
        strategy: "waterfall" or "load_balance"
        context_length: Override context length (0 = compute from min)

    Returns:
        alias_id: UUID of created alias
    """
    alias_id = str(uuid.uuid4())
    now = int(time.time())

    targets_str = json.dumps(targets) if isinstance(targets, (list, dict)) else str(targets or "")

    with _LOCK:
        conn = _conn()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO model_aliases (
                    alias_id, account_id, account_key_id,
                    alias_name, target_model, target_endpoint,
                    enabled, label, created_at, updated_at,
                    targets, strategy, context_length
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alias_id,
                    account_id,
                    account_key_id,
                    alias_name,
                    target_model,
                    target_endpoint,
                    1 if enabled else 0,
                    label,
                    now,
                    now,
                    targets_str,
                    strategy,
                    int(context_length or 0),
                ),
            )
        except sqlite3.OperationalError:
            cursor.execute(
                """
                INSERT INTO model_aliases (
                    alias_id, account_id, account_key_id,
                    alias_name, target_model, target_endpoint,
                    enabled, label, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alias_id,
                    account_id,
                    account_key_id,
                    alias_name,
                    target_model,
                    target_endpoint,
                    1 if enabled else 0,
                    label,
                    now,
                    now,
                ),
            )
        conn.commit()
    return alias_id


def get_alias_by_id_db(alias_id: str) -> Optional[Dict[str, Any]]:
    """Get alias by ID."""
    with _LOCK:
        conn = _conn()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"SELECT {_ALIAS_COLS} FROM model_aliases WHERE alias_id = ?",
                (alias_id,),
            )
            row = cursor.fetchone()
        except sqlite3.OperationalError:
            cursor.execute(
                f"SELECT {_ALIAS_LEGACY_COLS} FROM model_aliases WHERE alias_id = ?",
                (alias_id,),
            )
            row = cursor.fetchone()
    if not row:
        return None
    return _row_to_alias(row)


def resolve_alias_db(
    alias_name: str,
    account_id: str,
    account_key_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve alias to target model/endpoint.

    Precedence:
    1. Key-specific alias (if account_key_id provided)
    2. Account-wide alias
    """
    with _LOCK:
        conn = _conn()
        cursor = conn.cursor()

        # Try key-specific first
        if account_key_id:
            try:
                cursor.execute(
                    f"""
                    SELECT {_ALIAS_COLS}
                    FROM model_aliases
                    WHERE account_id = ?
                      AND account_key_id = ?
                      AND alias_name = ?
                      AND enabled = 1
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (account_id, account_key_id, alias_name),
                )
                row = cursor.fetchone()
            except sqlite3.OperationalError:
                cursor.execute(
                    f"""
                    SELECT {_ALIAS_LEGACY_COLS}
                    FROM model_aliases
                    WHERE account_id = ?
                      AND account_key_id = ?
                      AND alias_name = ?
                      AND enabled = 1
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (account_id, account_key_id, alias_name),
                )
                row = cursor.fetchone()

            if row:
                return _row_to_alias(row)

        # Fallback to account-wide
        try:
            cursor.execute(
                f"""
                SELECT {_ALIAS_COLS}
                FROM model_aliases
                WHERE account_id = ?
                  AND account_key_id IS NULL
                  AND alias_name = ?
                  AND enabled = 1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (account_id, alias_name),
            )
            row = cursor.fetchone()
        except sqlite3.OperationalError:
            cursor.execute(
                f"""
                SELECT {_ALIAS_LEGACY_COLS}
                FROM model_aliases
                WHERE account_id = ?
                  AND account_key_id IS NULL
                  AND alias_name = ?
                  AND enabled = 1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (account_id, alias_name),
            )
            row = cursor.fetchone()

        if not row:
            return None

        return _row_to_alias(row)


def list_aliases_db(
    account_id: Optional[str] = None,
    account_key_id: Optional[str] = None,
    include_disabled: bool = False,
    enabled_only: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """List aliases for account (or all accounts if account_id is None)."""
    if enabled_only is not None:
        include_disabled = not enabled_only

    with _LOCK:
        conn = _conn()
        cursor = conn.cursor()

        cols = _ALIAS_COLS
        try:
            # Probe if new columns exist
            cursor.execute("SELECT targets, strategy, context_length FROM model_aliases LIMIT 0")
        except sqlite3.OperationalError:
            cols = _ALIAS_LEGACY_COLS

        if account_id:
            if account_key_id:
                if include_disabled:
                    cursor.execute(
                        f"""
                        SELECT {cols}
                        FROM model_aliases
                        WHERE account_id = ? AND account_key_id = ?
                        ORDER BY created_at DESC
                        """,
                        (account_id, account_key_id),
                    )
                else:
                    cursor.execute(
                        f"""
                        SELECT {cols}
                        FROM model_aliases
                        WHERE account_id = ? AND account_key_id = ? AND enabled = 1
                        ORDER BY created_at DESC
                        """,
                        (account_id, account_key_id),
                    )
            else:
                if include_disabled:
                    cursor.execute(
                        f"""
                        SELECT {cols}
                        FROM model_aliases
                        WHERE account_id = ?
                        ORDER BY created_at DESC
                        """,
                        (account_id,),
                    )
                else:
                    cursor.execute(
                        f"""
                        SELECT {cols}
                        FROM model_aliases
                        WHERE account_id = ? AND enabled = 1
                        ORDER BY created_at DESC
                        """,
                        (account_id,),
                    )
        else:
            if include_disabled:
                cursor.execute(
                    f"""
                    SELECT {cols}
                    FROM model_aliases
                    ORDER BY created_at DESC
                    """
                )
            else:
                cursor.execute(
                    f"""
                    SELECT {cols}
                    FROM model_aliases
                    WHERE enabled = 1
                    ORDER BY created_at DESC
                    """
                )

        rows = cursor.fetchall()
        return [_row_to_alias(row) for row in rows]


def update_alias_db(
    alias_id: str,
    enabled: Optional[bool] = None,
    label: Optional[str] = None,
    target_model: Optional[str] = None,
    target_endpoint: Optional[str] = None,
    targets: Optional[Union[str, List[Any], Dict[str, Any]]] = None,
    strategy: Optional[str] = None,
    context_length: Optional[int] = None,
) -> bool:
    """Update alias fields."""
    with _LOCK:
        conn = _conn()
        cursor = conn.cursor()

        updates = []
        values = []

        if enabled is not None:
            updates.append("enabled = ?")
            values.append(1 if enabled else 0)

        if label is not None:
            updates.append("label = ?")
            values.append(label)

        if target_model is not None:
            updates.append("target_model = ?")
            values.append(target_model)

        if target_endpoint is not None:
            updates.append("target_endpoint = ?")
            values.append(target_endpoint)

        if targets is not None:
            targets_str = json.dumps(targets) if isinstance(targets, (list, dict)) else str(targets)
            updates.append("targets = ?")
            values.append(targets_str)

        if strategy is not None:
            updates.append("strategy = ?")
            values.append(str(strategy))

        if context_length is not None:
            updates.append("context_length = ?")
            values.append(int(context_length))

        if not updates:
            return False

        updates.append("updated_at = ?")
        values.append(int(time.time()))
        values.append(alias_id)

        try:
            cursor.execute(
                f"UPDATE model_aliases SET {', '.join(updates)} WHERE alias_id = ?",
                values,
            )
            conn.commit()
            return cursor.rowcount > 0
        except sqlite3.OperationalError:
            # Fallback if updated columns are not in legacy table
            legacy_updates = []
            legacy_values = []
            for u, v in zip(updates[:-2], values[:-2]):
                if any(u.startswith(k) for k in ("enabled", "label", "target_model", "target_endpoint")):
                    legacy_updates.append(u)
                    legacy_values.append(v)
            if not legacy_updates:
                return False
            legacy_updates.append("updated_at = ?")
            legacy_values.append(int(time.time()))
            legacy_values.append(alias_id)
            cursor.execute(
                f"UPDATE model_aliases SET {', '.join(legacy_updates)} WHERE alias_id = ?",
                legacy_values,
            )
            conn.commit()
            return cursor.rowcount > 0


def delete_alias_db(alias_id: str) -> bool:
    """Delete alias."""
    with _LOCK:
        conn = _conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM model_aliases WHERE alias_id = ?", (alias_id,))
        conn.commit()
        return cursor.rowcount > 0


def count_aliases_for_account_db(account_id: str) -> int:
    """Count total aliases for account (enabled + disabled)."""
    with _LOCK:
        conn = _conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM model_aliases WHERE account_id = ?",
            (account_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else 0

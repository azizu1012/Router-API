"""Backend CRUD operations for model_aliases table."""

import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

from src.backend._db import _LOCK, conn as _conn


def add_alias_db(
    account_id: str,
    alias_name: str,
    target_model: str,
    account_key_id: Optional[str] = None,
    target_endpoint: Optional[str] = None,
    enabled: bool = True,
    label: str = "",
) -> str:
    """Add a new model alias.

    Args:
        account_id: Owner account ID
        alias_name: Name user will call (e.g., "claude-3-5-sonnet")
        target_model: Actual model to route to (e.g., "gemini-flash")
        account_key_id: Key-specific alias (None = account-wide)
        target_endpoint: Custom endpoint name (None = use pool)
        enabled: Whether alias is active
        label: User description

    Returns:
        alias_id: UUID of created alias

    Raises:
        sqlite3.IntegrityError: If duplicate alias_name exists
    """
    alias_id = str(uuid.uuid4())
    now = int(time.time())

    with _LOCK:
        conn = _conn()
        cursor = conn.cursor()
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
        cursor.execute(
            """
            SELECT alias_id, account_id, account_key_id,
                   alias_name, target_model, target_endpoint,
                   enabled, label, created_at, updated_at
            FROM model_aliases
            WHERE alias_id = ?
            """,
            (alias_id,),
        )
        row = cursor.fetchone()
    if not row:
        return None

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
    }


def resolve_alias_db(
    alias_name: str,
    account_id: str,
    account_key_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve alias to target model/endpoint.

    Precedence:
    1. Key-specific alias (if account_key_id provided)
    2. Account-wide alias

    Args:
        alias_name: Alias to resolve
        account_id: Owner account ID
        account_key_id: Optional key ID for key-specific lookup

    Returns:
        Alias data if found and enabled, else None
    """
    with _LOCK:
        conn = _conn()
        cursor = conn.cursor()

        # Try key-specific first
        if account_key_id:
            cursor.execute(
                """
                SELECT alias_id, account_id, account_key_id,
                       alias_name, target_model, target_endpoint,
                       enabled, label, created_at, updated_at
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
                }

        # Fallback to account-wide
        cursor.execute(
            """
            SELECT alias_id, account_id, account_key_id,
                   alias_name, target_model, target_endpoint,
                   enabled, label, created_at, updated_at
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
        }


def list_aliases_db(
    account_id: Optional[str] = None,
    account_key_id: Optional[str] = None,
    include_disabled: bool = False,
    enabled_only: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """List aliases for account (or all accounts if account_id is None).

    Args:
        account_id: Owner account ID (None = all accounts)
        account_key_id: Filter by key ID (None = include all)
        include_disabled: Include disabled aliases
        enabled_only: If True, only include enabled aliases (overrides include_disabled)

    Returns:
        List of alias dicts, sorted by created_at DESC
    """
    if enabled_only is not None:
        include_disabled = not enabled_only

    with _LOCK:
        conn = _conn()
        cursor = conn.cursor()

        if account_id:
            if account_key_id:
                # Key-specific only
                if include_disabled:
                    cursor.execute(
                        """
                        SELECT alias_id, account_id, account_key_id,
                               alias_name, target_model, target_endpoint,
                               enabled, label, created_at, updated_at
                        FROM model_aliases
                        WHERE account_id = ? AND account_key_id = ?
                        ORDER BY created_at DESC
                        """,
                        (account_id, account_key_id),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT alias_id, account_id, account_key_id,
                               alias_name, target_model, target_endpoint,
                               enabled, label, created_at, updated_at
                        FROM model_aliases
                        WHERE account_id = ? AND account_key_id = ? AND enabled = 1
                        ORDER BY created_at DESC
                        """,
                        (account_id, account_key_id),
                    )
            else:
                # All aliases for this account
                if include_disabled:
                    cursor.execute(
                        """
                        SELECT alias_id, account_id, account_key_id,
                               alias_name, target_model, target_endpoint,
                               enabled, label, created_at, updated_at
                        FROM model_aliases
                        WHERE account_id = ?
                        ORDER BY created_at DESC
                        """,
                        (account_id,),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT alias_id, account_id, account_key_id,
                               alias_name, target_model, target_endpoint,
                               enabled, label, created_at, updated_at
                        FROM model_aliases
                        WHERE account_id = ? AND enabled = 1
                        ORDER BY created_at DESC
                        """,
                        (account_id,),
                    )
        else:
            # Global listing across all accounts (admin)
            if include_disabled:
                cursor.execute(
                    """
                    SELECT alias_id, account_id, account_key_id,
                           alias_name, target_model, target_endpoint,
                           enabled, label, created_at, updated_at
                    FROM model_aliases
                    ORDER BY created_at DESC
                    """
                )
            else:
                cursor.execute(
                    """
                    SELECT alias_id, account_id, account_key_id,
                           alias_name, target_model, target_endpoint,
                           enabled, label, created_at, updated_at
                    FROM model_aliases
                    WHERE enabled = 1
                    ORDER BY created_at DESC
                    """
                )

        rows = cursor.fetchall()
        return [
            {
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
            }
            for row in rows
        ]


def update_alias_db(
    alias_id: str,
    enabled: Optional[bool] = None,
    label: Optional[str] = None,
    target_model: Optional[str] = None,
    target_endpoint: Optional[str] = None,
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

        if not updates:
            return False

        updates.append("updated_at = ?")
        values.append(int(time.time()))
        values.append(alias_id)

        cursor.execute(
            f"UPDATE model_aliases SET {', '.join(updates)} WHERE alias_id = ?",
            values,
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

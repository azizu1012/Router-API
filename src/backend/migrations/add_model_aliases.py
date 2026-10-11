"""Database migration: Add model_aliases table for model name spoofing.

This migration adds support for model aliases, allowing users to define custom
model names that map to actual models in the pool or custom endpoints.

Use cases:
- Bypass client-side regex validation (call gemini-flash as "claude-3-5-sonnet")
- Self-managed custom endpoints with spoofed names
- Account-wide or key-specific routing rules
"""

import sqlite3
from pathlib import Path
from src.backend._db import _LOCK, conn as _conn


def migrate_add_model_aliases():
    """Add model_aliases table and related indices."""
    with _LOCK:
        c = _conn()
        try:
            # Create model_aliases table
            c.execute("""
                CREATE TABLE IF NOT EXISTS model_aliases (
                    alias_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    account_key_id TEXT DEFAULT NULL,

                    alias_name TEXT NOT NULL,
                    target_model TEXT NOT NULL,
                    target_endpoint TEXT DEFAULT NULL,

                    enabled INTEGER DEFAULT 1,
                    label TEXT DEFAULT '',
                    created_at INTEGER,
                    updated_at INTEGER,

                    UNIQUE(account_id, account_key_id, alias_name)
                )
            """)

            # Create lookup index
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_model_aliases_lookup
                ON model_aliases(account_id, account_key_id, alias_name, enabled)
            """)

            # Create account index for listing
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_model_aliases_account
                ON model_aliases(account_id, enabled)
            """)

            c.commit()
            print("[Migration] model_aliases table created successfully")

        except sqlite3.Error as e:
            print(f"[Migration] Error: {e}")
            raise


if __name__ == "__main__":
    print("Running migration: add model_aliases table")
    migrate_add_model_aliases()
    print("Migration completed")

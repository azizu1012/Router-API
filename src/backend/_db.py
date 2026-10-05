import sqlite3
import threading
from pathlib import Path

_DB = str(Path(__file__).resolve().parents[2] / "usage.db")
_LOCK = threading.RLock()

# Tables read while modules are still being imported. APIRouter queries
# key_status while it is constructed, and custom_endpoint_manager builds its
# cache at module level — both happen during import, long before any FastAPI
# startup hook, so a missing usage.db otherwise fails with "no such table"
# before the app exists.
#
# Keep this to the import-time readers only, and keep the column definitions
# identical to src/backend/schema.py: CREATE TABLE IF NOT EXISTS will not fix a
# table that already exists with the wrong shape, so a divergence here silently
# wins the race against schema.py and leaves columns that no ALTER migration
# ever adds. tests/test_schema_bootstrap.py compares the two.
_BOOTSTRAP_DDL = """
CREATE TABLE IF NOT EXISTS key_status (
    key TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1,
    usage INTEGER DEFAULT 0,
    active_requests INTEGER DEFAULT 0,
    frozen_until REAL DEFAULT 0,
    consecutive_failures INTEGER DEFAULT 0,
    last_success REAL DEFAULT 0,
    date TEXT DEFAULT '',
    today INTEGER DEFAULT 0,
    per_model TEXT DEFAULT '{}',
    data TEXT,
    tier TEXT DEFAULT 'free'
);
CREATE TABLE IF NOT EXISTS custom_endpoints (
    name TEXT PRIMARY KEY,
    base_url TEXT NOT NULL,
    auth_key TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    models TEXT DEFAULT '[]',
    disabled_models TEXT DEFAULT '[]',
    enabled_models TEXT DEFAULT '[]',
    account_id TEXT DEFAULT '',
    fallback INTEGER DEFAULT 0,
    pool_assignments TEXT DEFAULT '{}',
    updated_at TEXT
);
"""

_bootstrapped = False


def _bootstrap(c: sqlite3.Connection) -> None:
    """Create the import-time tables. Idempotent; runs once per process."""
    global _bootstrapped
    if _bootstrapped:
        return
    try:
        c.executescript(_BOOTSTRAP_DDL)
        c.commit()
        _bootstrapped = True
    except sqlite3.Error:
        # Never let schema creation take the process down. A real problem will
        # surface as a clearer error at the call site that needed the table.
        pass


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    _bootstrap(c)
    return c

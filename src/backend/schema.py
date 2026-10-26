import json
from pathlib import Path

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_system as logger
from src.backend._db import _LOCK, conn as _conn


# ── Automatic Migration Registry ──────────────────────────────────────
# All table columns that must exist across schema evolutions.
# auto_migrate_schema() queries PRAGMA table_info and automatically executes
# ALTER TABLE ADD COLUMN for any column missing from an existing database.
_SCHEMA_COLUMN_REGISTRY = {
    "accounts": {
        "tier": "TEXT DEFAULT 'free'",
        "web_search_enabled": "INTEGER DEFAULT 0",
        "mcp_only": "INTEGER DEFAULT 0",
        "search_engine": "TEXT DEFAULT 'duckduckgo'",
        "rpm": "INTEGER DEFAULT 300",
        "tpm": "INTEGER DEFAULT 6000000",
        "rpd": "INTEGER DEFAULT 20000",
    },
    "custom_endpoints": {
        "models": "TEXT DEFAULT '[]'",
        "disabled_models": "TEXT DEFAULT '[]'",
        "enabled_models": "TEXT DEFAULT '[]'",
        "account_id": "TEXT DEFAULT ''",
        "account_key_id": "TEXT DEFAULT ''",
        "fallback": "INTEGER DEFAULT 0",
        "api_format": "TEXT DEFAULT 'openai'",
        "rpm": "INTEGER DEFAULT 0",
        "timeout": "INTEGER DEFAULT 0",
        "pool_assignments": "TEXT DEFAULT '{}'",
        "model_contexts": "TEXT DEFAULT '{}'",
        "context_length": "INTEGER DEFAULT 0",
        "updated_at": "TEXT",
    },
    "endpoint_transitions": {
        "transition_id": "TEXT DEFAULT ''",
        "request_id": "TEXT DEFAULT ''",
        "endpoint": "TEXT DEFAULT ''",
        "state": "TEXT DEFAULT ''",
        "failures": "INTEGER DEFAULT 0",
        "frozen_until": "REAL DEFAULT 0",
        "metadata": "TEXT DEFAULT '{}'",
        "created_at": "REAL DEFAULT 0",
    },
    "key_status": {
        "enabled": "INTEGER DEFAULT 1",
        "project_id": "TEXT DEFAULT ''",
        "last_transition_id": "TEXT DEFAULT ''",
        "last_transition_at": "REAL DEFAULT 0",
        "last_transition_json": "TEXT DEFAULT '{}'",
        "usage": "INTEGER DEFAULT 0",
        "active_requests": "INTEGER DEFAULT 0",
        "frozen_until": "REAL DEFAULT 0",
        "consecutive_failures": "INTEGER DEFAULT 0",
        "last_success": "REAL DEFAULT 0",
        "date": "TEXT DEFAULT ''",
        "today": "INTEGER DEFAULT 0",
        "per_model": "TEXT DEFAULT '{}'",
        "data": "TEXT",
        "tier": "TEXT DEFAULT 'free'",
    },
    "account_credentials": {
        "password_enc": "TEXT",
        "must_change": "INTEGER DEFAULT 0",
        "updated_at": "INTEGER",
    },
    "account_keys": {
        "enabled": "INTEGER DEFAULT 1",
        "tier": "TEXT DEFAULT 'free'",
        "rpm": "INTEGER DEFAULT 30",
        "tpm": "INTEGER DEFAULT 200000",
        "rpd": "INTEGER DEFAULT 1000",
        "max_concurrency": "INTEGER DEFAULT 6",
        "min_interval_seconds": "REAL DEFAULT 3.0",
        "label": "TEXT DEFAULT ''",
        "created_at": "INTEGER",
        "updated_at": "INTEGER",
    },
    "model_aliases": {
        "account_key_id": "TEXT DEFAULT NULL",
        "target_endpoint": "TEXT DEFAULT NULL",
        "enabled": "INTEGER DEFAULT 1",
        "label": "TEXT DEFAULT ''",
        "created_at": "INTEGER NOT NULL DEFAULT 0",
        "updated_at": "INTEGER NOT NULL DEFAULT 0",
        "targets": "TEXT DEFAULT ''",
        "strategy": "TEXT DEFAULT 'waterfall'",
        "context_length": "INTEGER DEFAULT 0",
    },
    "model_config": {
        "account_id": "TEXT DEFAULT ''",
        "display": "TEXT DEFAULT ''",
        "rpm": "INTEGER DEFAULT 0",
        "tpm": "INTEGER DEFAULT 0",
        "rpd": "INTEGER DEFAULT 0",
        "context_length": "INTEGER DEFAULT 220000",
        "pool_name": "TEXT DEFAULT ''",
        "priority": "INTEGER DEFAULT 100",
        "hidden": "INTEGER DEFAULT 0",
        "enabled": "INTEGER DEFAULT 1",
        "updated_at": "INTEGER DEFAULT 0",
    },
}


def auto_migrate_schema(c) -> None:
    """Inspect existing SQLite tables and automatically add any missing columns.

    Eliminates the need for external migration scripts or manual ALTER TABLEs.
    Runs idempotently on server startup whenever schema definitions evolve.
    """
    for table_name, columns in _SCHEMA_COLUMN_REGISTRY.items():
        cur = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        if not cur.fetchone():
            continue

        existing_cols = {row[1] for row in c.execute(f"PRAGMA table_info({table_name})")}
        for col_name, col_def in columns.items():
            if col_name not in existing_cols:
                try:
                    c.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}")
                    logger.info("[AutoMigrate] Added missing column '%s' to '%s'", col_name, table_name)
                except Exception as e:
                    logger.warning("[AutoMigrate] Failed to add column '%s' to '%s': %s", col_name, table_name, e)
    c.commit()


def init_config_tables() -> None:
    with _LOCK:
        c = _conn()
        try:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    auth_key TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    rpm INTEGER DEFAULT 300,
                    tpm INTEGER DEFAULT 6000000,
                    rpd INTEGER DEFAULT 20000,
                    tier TEXT DEFAULT 'free',
                    created_at INTEGER,
                    updated_at INTEGER
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
                    api_format TEXT DEFAULT 'openai',
                    rpm INTEGER DEFAULT 0,
                    timeout INTEGER DEFAULT 0,
                    pool_assignments TEXT DEFAULT '{}',
                    model_contexts TEXT DEFAULT '{}',
                    context_length INTEGER DEFAULT 0,
                    updated_at TEXT
                );
                -- Member-owned endpoint lookup (endpoints.py: get_endpoint_by_account_db,
                -- get_endpoints_by_account_db). Both filter on account_id + enabled.
                CREATE INDEX IF NOT EXISTS idx_custom_endpoints_account
                    ON custom_endpoints(account_id, enabled);
                CREATE TABLE IF NOT EXISTS oauth_clients (
                    client_id TEXT PRIMARY KEY,
                    encrypted_secret TEXT NOT NULL,
                    updated_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS key_usage (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS key_status (
                    key TEXT PRIMARY KEY,
                    enabled INTEGER DEFAULT 1,
                    project_id TEXT DEFAULT '',
                    usage INTEGER DEFAULT 0,
                    active_requests INTEGER DEFAULT 0,
                    frozen_until REAL DEFAULT 0,
                    consecutive_failures INTEGER DEFAULT 0,
                    last_success REAL DEFAULT 0,
                    date TEXT DEFAULT '',
                    today INTEGER DEFAULT 0,
                    per_model TEXT DEFAULT '{}',
                    data TEXT,
                    tier TEXT DEFAULT 'free',
                    last_transition_id TEXT DEFAULT '',
                    last_transition_at REAL DEFAULT 0,
                    last_transition_json TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS endpoint_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT DEFAULT '', endpoint TEXT DEFAULT '',
                    state TEXT DEFAULT '', failures INTEGER DEFAULT 0,
                    frozen_until REAL DEFAULT 0, metadata TEXT DEFAULT '{}',
                    created_at REAL DEFAULT 0, transition_id TEXT UNIQUE DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_endpoint_transitions_lookup
                    ON endpoint_transitions(endpoint, created_at);
                CREATE TABLE IF NOT EXISTS key_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT DEFAULT '',
                    transition_id TEXT DEFAULT '',
                    key_suffix TEXT DEFAULT '',
                    model_id TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}',
                    created_at REAL DEFAULT 0,
                    UNIQUE(transition_id)
                );
                CREATE INDEX IF NOT EXISTS idx_key_transitions_lookup
                    ON key_transitions(request_id, key_suffix, created_at);
                CREATE TABLE IF NOT EXISTS key_penalties (
                    pkey TEXT PRIMARY KEY,
                    api_key TEXT,
                    model_id TEXT,
                    reason TEXT,
                    expires REAL,
                    score_reduction INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_key_penalties_expires ON key_penalties(expires);
                CREATE TABLE IF NOT EXISTS model_prices (
                    model_name TEXT PRIMARY KEY,
                    input_rate_per_1k REAL NOT NULL DEFAULT 0.0025,
                    output_rate_per_1k REAL NOT NULL DEFAULT 0.01,
                    response_model_name TEXT DEFAULT ''
                );

                -- ── Account auth: structured tokens, web credentials, enrollment ──
                -- Auth tokens are stored decomposed (name + 6-char code) rather
                -- than as the literal wire string "sk-<name>-<code>". Splitting
                -- them keeps "which tokens does this account own" an indexed
                -- lookup instead of a LIKE scan, and lets each token carry its
                -- own quota.
                CREATE TABLE IF NOT EXISTS account_keys (
                    key_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    token_code TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    tier TEXT DEFAULT 'free',
                    rpm INTEGER DEFAULT 30,
                    tpm INTEGER DEFAULT 200000,
                    rpd INTEGER DEFAULT 1000,
                    max_concurrency INTEGER DEFAULT 6,
                    min_interval_seconds REAL DEFAULT 3.0,
                    label TEXT DEFAULT '',
                    created_at INTEGER,
                    updated_at INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_account_keys_name_code
                    ON account_keys(name, token_code);
                CREATE INDEX IF NOT EXISTS idx_account_keys_account
                    ON account_keys(account_id);

                CREATE TABLE IF NOT EXISTS account_credentials (
                    account_id TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    password_enc TEXT,
                    must_change INTEGER DEFAULT 0,
                    updated_at INTEGER
                );

                -- One-time enrollment codes. used_at IS NULL means still live.
                CREATE TABLE IF NOT EXISTS invite_codes (
                    code TEXT PRIMARY KEY,
                    created_by TEXT,
                    created_at INTEGER,
                    expires_at INTEGER,
                    used_at INTEGER,
                    used_by TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_invite_expiry ON invite_codes(expires_at);

                -- Model aliases for model name spoofing and routing
                CREATE TABLE IF NOT EXISTS model_aliases (
                    alias_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    account_key_id TEXT DEFAULT NULL,
                    alias_name TEXT NOT NULL,
                    target_model TEXT NOT NULL,
                    target_endpoint TEXT DEFAULT NULL,
                    enabled INTEGER DEFAULT 1,
                    label TEXT DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    targets TEXT DEFAULT '',
                    strategy TEXT DEFAULT 'waterfall',
                    context_length INTEGER DEFAULT 0,
                    UNIQUE(account_id, account_key_id, alias_name)
                );
                CREATE INDEX IF NOT EXISTS idx_model_aliases_lookup
                    ON model_aliases(account_id, account_key_id, alias_name, enabled);
                CREATE INDEX IF NOT EXISTS idx_model_aliases_account
                    ON model_aliases(account_id, enabled);
            """)
            c.commit()

            # Initialize model config table
            init_model_config_table(c)

            # Automatic Schema Migration: inspects PRAGMA table_info across all tables
            # and automatically issues ALTER TABLE ADD COLUMN for any missing column.
            # Eliminates external migration scripts and ensures seamless database upgrades.
            auto_migrate_schema(c)

            # Migration: upgrade default account limits for old accounts
            try:
                c.execute("UPDATE accounts SET tpm = 6000000 WHERE tpm = 200000")
                c.execute("UPDATE accounts SET rpm = 300 WHERE rpm = 30")
                c.execute("UPDATE accounts SET rpd = 20000 WHERE rpd = 1000")
                # Auto-migrate legacy 200k/220k context lengths to safe 185k/14k
                c.execute("UPDATE model_config SET context_length = 185000 WHERE context_length IN (200000, 220000) AND alias NOT LIKE '%gemma%'")
                c.execute("UPDATE model_config SET context_length = 14000 WHERE alias LIKE '%gemma%'")
            except Exception as e:
                logger.warning("[Schema] Failed to upgrade default account limits: %s", e)
            c.commit()

            # Seed or update model_prices to real pricing
            from src.backend.models.prices import DEFAULT_MODEL_PRICES
            cur = c.execute("SELECT input_rate_per_1k FROM model_prices WHERE model_name = 'gemini-3.5-flash'")
            old_flash_row = cur.fetchone()
            if not old_flash_row or (old_flash_row and abs(old_flash_row[0] - 0.0015) < 1e-6):
                c.executemany(
                    "INSERT OR REPLACE INTO model_prices (model_name, input_rate_per_1k, output_rate_per_1k, response_model_name) VALUES (?,?,?,?)",
                    DEFAULT_MODEL_PRICES,
                )
                logger.info("[Schema] Synchronized %d real model prices to DB", len(DEFAULT_MODEL_PRICES))
            else:
                c.executemany(
                    "INSERT OR IGNORE INTO model_prices (model_name, input_rate_per_1k, output_rate_per_1k, response_model_name) VALUES (?,?,?,?)",
                    DEFAULT_MODEL_PRICES,
                )
            c.commit()
        finally:
            c.close()

    try:
        from src.backend.models.config import sync_default_models_to_db
        sync_default_models_to_db()
    except Exception as e:
        logger.warning("[Schema] Failed to sync default models: %s", e)




def init_model_config_table(c) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS model_config (
            alias TEXT NOT NULL,
            account_id TEXT DEFAULT '',
            display TEXT DEFAULT '',
            model_id TEXT DEFAULT '',
            rpm INTEGER DEFAULT 10,
            tpm INTEGER DEFAULT 1000000,
            rpd INTEGER DEFAULT 1000,
            rpd_enabled INTEGER DEFAULT 0,
            hidden INTEGER DEFAULT 0,
            priority INTEGER DEFAULT 1,
            context_length INTEGER DEFAULT 220000,
            pool_name TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            updated_at TEXT,
            PRIMARY KEY (alias, account_id)
        );
    """)

    try:
        c.execute("ALTER TABLE model_config ADD COLUMN rpd_enabled INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE model_config ADD COLUMN pool_name TEXT DEFAULT ''")
    except Exception:
        pass
    _migrate_model_config_pk(c)


def _migrate_model_config_pk(c) -> None:
    try:
        c.execute("ALTER TABLE model_config ADD COLUMN account_id TEXT DEFAULT ''")
    except Exception:
        pass
    info = c.execute("PRAGMA table_info(model_config)").fetchall()
    pk_cols = [row[0] for row in info if row[5] == 1]
    if pk_cols == ["alias"]:
        c.executescript("""
            CREATE TABLE model_config_v2 (
                alias TEXT NOT NULL,
                account_id TEXT DEFAULT '',
                display TEXT DEFAULT '',
                model_id TEXT DEFAULT '',
                rpm INTEGER DEFAULT 10,
                tpm INTEGER DEFAULT 1000000,
                rpd INTEGER DEFAULT 1000,
                rpd_enabled INTEGER DEFAULT 0,
                hidden INTEGER DEFAULT 0,
                priority INTEGER DEFAULT 1,
                context_length INTEGER DEFAULT 220000,
                pool_name TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                updated_at TEXT,
                PRIMARY KEY (alias, account_id)
            );
            INSERT INTO model_config_v2 (alias, account_id, display, model_id, rpm, tpm, rpd, rpd_enabled, hidden, priority, context_length, pool_name, enabled, updated_at)
                SELECT alias, COALESCE(account_id, ''), display, model_id, rpm, tpm, rpd, COALESCE(rpd_enabled, 0), hidden, priority, context_length, COALESCE(pool_name, ''), enabled, updated_at FROM model_config;
            DROP TABLE model_config;
            ALTER TABLE model_config_v2 RENAME TO model_config;
        """)


def _migrate_key_status_columns() -> None:
    with _LOCK:
        c = _conn()
        try:
            cur = c.execute("SELECT key, data FROM key_status WHERE data IS NOT NULL AND data != '' LIMIT 1")
            if not cur.fetchone():
                return
            rows = c.execute("SELECT key, data FROM key_status WHERE data IS NOT NULL AND data != ''").fetchall()
            for r in rows:
                try:
                    d = json.loads(r["data"])
                except Exception:
                    continue
                pm_json = json.dumps(d.get("per_model", {}))
                c.execute(
                    """UPDATE key_status SET usage=?, active_requests=?, frozen_until=?,
                       consecutive_failures=?, last_success=?, date=?, today=?, per_model=?, data=NULL
                       WHERE key=?""",
                    (d.get("usage", 0), d.get("active_requests", 0),
                     d.get("frozen_until", 0.0), d.get("consecutive_failures", 0),
                     d.get("last_success", 0.0), d.get("date", ""),
                     d.get("today", 0), pm_json, r["key"]),
                )
            c.commit()
            logger.info("[Schema] Migrated key_status columns for %d keys", len(rows))
        finally:
            c.close()


def _rename_bak(p: Path) -> None:
    if p.exists():
        bak = p.with_suffix(p.suffix + ".bak")
        if not bak.exists():
            p.rename(bak)
            logger.info("[Schema] Renamed %s -> %s", p.name, bak.name)


def migrate_from_json() -> None:
    with _LOCK:
        c = _conn()
        try:
            cur = c.execute("SELECT COUNT(*) FROM accounts")
            if cur.fetchone()[0] > 0:
                return

            accounts_path = Path(config.ACCOUNTS_FILE)
            if accounts_path.exists():
                try:
                    raw = json.loads(accounts_path.read_text(encoding="utf-8"))
                except Exception:
                    raw = {}
                for acc in raw.get("accounts", []):
                    c.execute(
                        """INSERT OR IGNORE INTO accounts
                           (account_id, name, auth_key, enabled, rpm, tpm, rpd, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (acc["account_id"], acc["name"], acc["auth_key"],
                         1 if acc.get("enabled", True) else 0,
                         acc.get("rpm", 30), acc.get("tpm", 200000), acc.get("rpd", 1000),
                         acc.get("created_at", 0), acc.get("updated_at", 0)),
                    )
                gmu = raw.get("gemini_key_usage", {})
                if gmu:
                    for k, v in gmu.items():
                        c.execute("INSERT OR REPLACE INTO key_usage (key, data) VALUES (?,?)", (k, json.dumps(v)))
                logger.info("[Schema] Migrated accounts from %s", accounts_path)

            quota_path = Path(config.PROJECT_ROOT / "quota.json")
            if quota_path.exists():
                try:
                    raw = json.loads(quota_path.read_text(encoding="utf-8"))
                except Exception:
                    raw = {}
                for k, v in raw.get("key_usage", {}).items():
                    c.execute("INSERT OR REPLACE INTO key_usage (key, data) VALUES (?,?)", (k, json.dumps(v)))
                for k, v in raw.get("key_status", {}).items():
                    c.execute("INSERT OR REPLACE INTO key_status (key, data) VALUES (?,?)", (k, json.dumps(v)))
                logger.info("[Schema] Migrated key data from %s", quota_path)

            custom_path = Path(__file__).resolve().parents[2] / "custom_endpoints.json"
            if custom_path.exists():
                try:
                    raw = json.loads(custom_path.read_text(encoding="utf-8"))
                except Exception:
                    raw = {}
                for name, ep in raw.items():
                    c.execute(
                        """INSERT OR REPLACE INTO custom_endpoints
                           (name, base_url, auth_key, enabled, models, account_id, fallback, pool_assignments, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (name, ep.get("base_url", ""), ep.get("auth_key", ""),
                         1 if ep.get("enabled", True) else 0,
                         json.dumps(ep.get("models", [])),
                         "",
                         1 if ep.get("fallback", False) else 0,
                         json.dumps(ep.get("pool_assignments", {})),
                         ep.get("updated_at", "")),
                    )
                logger.info("[Schema] Migrated custom endpoints from %s", custom_path)

            c.commit()
            _rename_bak(Path(config.ACCOUNTS_FILE))
            _rename_bak(Path(config.PROJECT_ROOT / "quota.json"))
            _rename_bak(Path(__file__).resolve().parents[2] / "custom_endpoints.json")
        finally:
            c.close()


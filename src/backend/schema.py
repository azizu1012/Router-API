import json
from pathlib import Path

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_system as logger
from src.backend._db import _LOCK, conn as _conn


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
                    pool_assignments TEXT DEFAULT '{}',
                    updated_at TEXT
                );
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
            """)
            c.commit()
 
            # Migration: add tier to accounts
            try:
                c.execute("ALTER TABLE accounts ADD COLUMN tier TEXT DEFAULT 'free'")
            except Exception:
                pass

            # Migration: add enabled to key_status
            try:
                c.execute("ALTER TABLE key_status ADD COLUMN enabled INTEGER DEFAULT 1")
            except Exception:
                pass

            # Migration: add web_search_enabled to accounts
            try:
                c.execute("ALTER TABLE accounts ADD COLUMN web_search_enabled INTEGER DEFAULT 0")
            except Exception:
                pass

            # Migration: add account_id to custom_endpoints
            try:
                c.execute("ALTER TABLE custom_endpoints ADD COLUMN account_id TEXT DEFAULT ''")
            except Exception:
                pass

            # Migration: add disabled_models to custom_endpoints
            try:
                c.execute("ALTER TABLE custom_endpoints ADD COLUMN disabled_models TEXT DEFAULT '[]'")
            except Exception:
                pass

            # Migration: add enabled_models to custom_endpoints
            try:
                c.execute("ALTER TABLE custom_endpoints ADD COLUMN enabled_models TEXT DEFAULT '[]'")
            except Exception:
                pass

            # Migration: add pool_assignments to custom_endpoints
            try:
                c.execute("ALTER TABLE custom_endpoints ADD COLUMN pool_assignments TEXT DEFAULT '{}'")
            except Exception:
                pass

            # Migration: upgrade default account limits for old accounts
            try:
                c.execute("UPDATE accounts SET tpm = 6000000 WHERE tpm = 200000")
                c.execute("UPDATE accounts SET rpm = 300 WHERE rpm = 30")
                c.execute("UPDATE accounts SET rpd = 20000 WHERE rpd = 1000")
            except Exception as e:
                logger.warning("[Schema] Failed to upgrade default account limits: %s", e)
            c.commit()
 
            for col_sql in [
                "enabled INTEGER DEFAULT 1",
                "usage INTEGER DEFAULT 0",
                "active_requests INTEGER DEFAULT 0",
                "frozen_until REAL DEFAULT 0",
                "consecutive_failures INTEGER DEFAULT 0",
                "last_success REAL DEFAULT 0",
                "date TEXT DEFAULT ''",
                "today INTEGER DEFAULT 0",
                "per_model TEXT DEFAULT '{}'",
                "data TEXT",
                "tier TEXT DEFAULT 'free'",
            ]:
                try:
                    c.execute(f"ALTER TABLE key_status ADD COLUMN {col_sql}")
                except Exception:
                    pass
            c.commit()

            # Seed model_prices if empty
            cur = c.execute("SELECT COUNT(*) FROM model_prices")
            if cur.fetchone()[0] == 0:
                default_prices = [
                    ("gemini-3.5-flash", 0.0015, 0.009, "gemini-3.5-flash"),
                    ("gemini-3.1-flash", 0.0005, 0.003, "gemini-3.1-flash"),
                    ("gemini-3.1-flash-lite", 0.00025, 0.0015, "gemini-3.1-flash-lite"),
                    ("gemini-3.1-pro", 0.002, 0.012, "gemini-3.1-pro"),
                    ("gemini-2.5-flash", 0.0003, 0.0025, "gemini-2.5-flash"),
                    ("gemini-2.5-flash-lite", 0.0001, 0.0004, "gemini-2.5-flash-lite"),
                    ("gemini-2.5-pro", 0.00125, 0.01, "gemini-2.5-pro"),
                    ("gemini-2.0-flash", 0.0001, 0.0004, "gemini-2.0-flash"),
                    ("gemini-flash", 0.0005, 0.003, "gemini-3.1-flash"),
                    ("gemini-flash-lite", 0.00025, 0.0015, "gemini-3.1-flash-lite"),
                    ("custom-model", 0.0, 0.0, "custom-model"),
                    ("gemini-flash-35", 0.0015, 0.009, "gemini-3.5-flash"),
                    ("gemini-flash-25", 0.0003, 0.0025, "gemini-2.5-flash"),
                    ("gemini-flash-25-lite", 0.0001, 0.0004, "gemini-2.5-flash-lite"),
                ]
                c.executemany(
                    "INSERT OR IGNORE INTO model_prices (model_name, input_rate_per_1k, output_rate_per_1k, response_model_name) VALUES (?,?,?,?)",
                    default_prices,
                )
                logger.info("[Schema] Seeded %d model prices", len(default_prices))
            c.commit()
        finally:
            c.close()
            



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

"""The import-time bootstrap and schema.py must not disagree.

src/backend/_db.py creates key_status and custom_endpoints the first time a
connection opens, because both are read while modules are still importing —
before any startup hook can run. src/backend/schema.py declares the full schema
and patches older databases with ALTER TABLE.

That ordering makes the bootstrap dangerous. CREATE TABLE IF NOT EXISTS does
not correct a table that already has the wrong shape, so if the two
definitions drift the bootstrap silently wins: schema.py's CREATE becomes a
no-op, and any column schema.py has no ALTER migration for is never created.
custom_endpoints is the sharpest example — the bootstrap can create it without
`disabled_models` or `fallback` and every later INSERT names those columns.

The comparison is deliberately against the DDL text in schema.py, not against a
database built by init_config_tables(). Building a database to inspect it
would run the bootstrap first and pollute exactly what we are trying to check.
"""

import inspect
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from src.backend._db import _BOOTSTRAP_DDL
from src.backend.schema import init_config_tables

# Read at import time, therefore present in the bootstrap.
_BOOTSTRAP_TABLES = ("key_status", "custom_endpoints")

_CREATE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_COLUMN = re.compile(r"^\s*(\w+)\s+(?:TEXT|INTEGER|REAL|BLOB|NUMERIC)", re.IGNORECASE)


def _declared_tables(ddl: str) -> dict[str, set[str]]:
    """Map table name -> declared column names, from DDL text."""
    out: dict[str, set[str]] = {}
    for name, body in _CREATE.findall(ddl):
        cols = set()
        for line in body.splitlines():
            line = line.split("--")[0].strip()
            if not line:
                continue
            m = _COLUMN.match(line)
            if m and m.group(1).upper() not in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK"):
                cols.add(m.group(1))
        out[name] = cols
    return out


def _schema_ddl() -> str:
    """Every executescript() literal reachable in schema.py."""
    source = inspect.getsource(sys.modules["src.backend.schema"])
    # Strip string prefixes/quotes is unnecessary: the CREATE regex matches the
    # SQL inside the literals regardless of the surrounding Python quoting.
    return source


class TestBootstrapMatchesSchemaDeclaration:
    def test_schema_parser_sees_every_table(self):
        declared = _declared_tables(_schema_ddl())
        for table in ("key_status", "custom_endpoints", "account_keys",
                      "account_credentials", "invite_codes", "accounts"):
            assert table in declared, f"test cannot see {table} in schema.py"

    def test_bootstrap_parser_round_trips(self):
        declared = _declared_tables(_BOOTSTRAP_DDL)
        assert set(declared) == set(_BOOTSTRAP_TABLES)
        for table in _BOOTSTRAP_TABLES:
            assert "key" in declared[table] or "name" in declared[table]

    @pytest.mark.parametrize("table", _BOOTSTRAP_TABLES)
    def test_columns_identical(self, table):
        schema_decl = _declared_tables(_schema_ddl())
        boot_decl = _declared_tables(_BOOTSTRAP_DDL)

        assert table in schema_decl, f"{table} not declared in schema.py"
        missing = schema_decl[table] - boot_decl.get(table, set())
        extra = boot_decl.get(table, set()) - schema_decl[table]

        assert not missing, (
            f"{table}: bootstrap omits {sorted(missing)} — schema.py has no "
            f"ALTER for these, so on a fresh DB they would never exist"
        )
        assert not extra, (
            f"{table}: bootstrap adds {sorted(extra)}, which schema.py does "
            f"not declare"
        )

    def test_bootstrap_declares_nothing_schema_lacks(self):
        schema_decl = _declared_tables(_schema_ddl())
        for table in _declared_tables(_BOOTSTRAP_DDL):
            assert table in schema_decl, (
                f"bootstrap creates {table}, which schema.py never declares — "
                f"the two have diverged"
            )


class TestBootstrapIsSufficient:
    """The bootstrap must be enough to get past module import on an empty DB."""

    def test_import_time_tables_exist_after_bootstrap(self):
        c = sqlite3.connect(":memory:")
        try:
            c.executescript(_BOOTSTRAP_DDL)
            names = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            c.close()
        for table in _BOOTSTRAP_TABLES:
            assert table in names

    def test_bootstrap_then_full_init_keeps_columns(self):
        """Real deployment order: conn() bootstraps, then init runs on top."""
        from src.backend import _db as db_mod

        fd, name = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        tmp = Path(name)
        original = db_mod._DB
        db_mod._DB = str(tmp)
        db_mod._bootstrapped = False
        try:
            init_config_tables()
            c = sqlite3.connect(str(tmp))
        finally:
            db_mod._DB = original
            db_mod._bootstrapped = False
        try:
            schema_decl = _declared_tables(_schema_ddl())
            for table in _BOOTSTRAP_TABLES:
                actual = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                missing = schema_decl[table] - actual
                assert not missing, (
                    f"{table} is missing {sorted(missing)} after the full init; "
                    f"the bootstrap shape won and no migration added them"
                )
        finally:
            c.close()
            tmp.unlink(missing_ok=True)

    def test_ddl_is_idempotent(self):
        c = sqlite3.connect(":memory:")
        try:
            c.executescript(_BOOTSTRAP_DDL)
            c.executescript(_BOOTSTRAP_DDL)
            c.executescript(_BOOTSTRAP_DDL)
        finally:
            c.close()
"""Shared fixtures, including a guard against tests writing to the real database.

Two tests were quietly appending to usage.db on every run:
test_master_key.py called create_key_db() with no way to redirect it, and
test_account_auth.py created invite codes. create_key_db takes no db_path
argument, so nothing in the call could have pointed it elsewhere. 59 runs left
59 token rows behind under an account name that does not exist.

The rows were harmless — account_manager only caches a token whose owning
account is found, so _check_auth rejected every one — but the failure mode is
what matters. A test suite that mutates the database it runs against is a suite
whose results depend on execution order and whose fixtures are lying about what
they cover.

So the guard is a session fixture that snapshots every table's row count before
the first test and compares at the end. It catches the next one, not just these
two. Point the offending test at the `temp_db` fixture to satisfy it.
"""
import os
import sqlite3

import pytest

from src.backend import _db as db_mod
from src.backend import schema as schema_mod

REAL_DB = 'usage.db'

TABLES = ('accounts', 'account_keys', 'account_credentials', 'invite_codes',
          'custom_endpoints', 'model_config', 'key_status', 'key_penalties')


def _row_counts(path):
    if not os.path.exists(path):
        return None
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        out = {}
        for t in TABLES:
            try:
                out[t] = con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
            except sqlite3.Error:
                out[t] = None      # table absent; nothing to compare
        return out
    finally:
        con.close()


@pytest.fixture(scope='session', autouse=True)
def _real_database_is_not_a_scratchpad():
    """Fail if the suite changed usage.db.

    Autouse and session-scoped, so it wraps every test in the run. Comparing
    row counts rather than file bytes keeps it immune to WAL churn and vacuum
    rewrites that write no rows.
    """
    before = _row_counts(REAL_DB)
    yield
    after = _row_counts(REAL_DB)
    if before is None:
        return
    changed = {
        t: (before.get(t), after.get(t))
        for t in TABLES
        if before.get(t) != after.get(t)
    }
    assert not changed, (
        f"the test suite wrote to {REAL_DB}: {changed}. "
        "Tests must not touch the real database — use the temp_db fixture, which "
        "redirects src.backend._db for the duration of the test."
    )


@pytest.fixture
def temp_db():
    """Point src.backend._db at a throwaway file for the duration of one test.

    Mirrors what several tests were already doing by hand, in one place so the
    next one does not forget. Restores _DB and clears the bootstrap flag so the
    real database is re-read afterwards.
    """
    import tempfile

    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    original_db = db_mod._DB
    original_boot = db_mod._bootstrapped
    db_mod._DB = path
    db_mod._bootstrapped = False
    try:
        schema_mod.init_config_tables()
        yield path
    finally:
        db_mod._DB = original_db
        db_mod._bootstrapped = original_boot
        for suffix in ('', '-wal', '-shm'):
            try:
                os.unlink(path + suffix)
            except OSError:
                pass
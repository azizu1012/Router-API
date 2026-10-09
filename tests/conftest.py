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
    """Row counts per table, or a sentinel if the file is absent.

    Absence is data, not an excuse to skip: a missing usage.db that turns into a
    20 KB file full of tables is still a change to the developer's database.
    """
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
def _schema_exists_before_any_test():
    """Give the suite a database to run against.

    Not mock data — a real SQLite file with the real schema, created once.

    This suite used to pass on a developer's machine and fail in CI for one
    reason: ``usage.db`` was already sitting in the working copy. It contains
    the tables ``accounts`` and ``account_keys`` were queried from, so every test
    that touched account storage found them. A fresh checkout has no such file,
    and ``src/backend/_db.py`` only bootstraps ``key_status`` and
    ``custom_endpoints`` at import — everything else is created by
    ``init_config_tables()``, which ``main.py`` calls and pytest never did.

    The symptom was three ``no such table: accounts`` errors. The cause was not
    missing fixtures but a missing file that happened to be gitignored, so it
    could never travel with the code.

    A committed ``usage.db`` would be worse: a binary blob that drifts from
    schema.py, is silently rewritten by any test that writes to it, and looks
    like sample data while actually being someone's real accounts.
    """
    from src.backend import schema as schema_mod

    schema_mod.init_config_tables()


@pytest.fixture(scope='session', autouse=True)
def _password_recovery_is_testable():
    """Set a recovery key for the session if the operator has not.

    ``set_password_db`` only writes the reversible ``password_enc`` copy when
    ROUTER_API_PASSWORD_KEY is set, so the recovery tests asserted ``None`` in a
    clean checkout and passed in a working copy that had a key in ``.env``.
    A fixed non-secret value is enough; nothing decrypts anything real here.
    """
    key = 'test-only-not-a-real-key'
    if not os.environ.get('ROUTER_API_PASSWORD_KEY'):
        os.environ['ROUTER_API_PASSWORD_KEY'] = key
        yield
        del os.environ['ROUTER_API_PASSWORD_KEY']
    else:
        yield


@pytest.fixture(scope='session', autouse=True)
def _real_database_is_not_a_scratchpad(_schema_exists_before_any_test,
                                        _password_recovery_is_testable):
    """Fail if the suite changed usage.db.

    Autouse and session-scoped, so it wraps every test in the run. Comparing
    row counts rather than file bytes keeps it immune to WAL churn and vacuum
    rewrites that write no rows.
    """
    before = _row_counts(REAL_DB)
    yield
    after = _row_counts(REAL_DB)
    if before is None or after is None:
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


@pytest.fixture(autouse=True)
def _no_shared_ip_rate_limit():
    """Clear the per-IP dashboard/login counters around each test.

    security_middleware caps /dashboard/* at 60 req/min per IP, keyed on
    request.client.host. Every TestClient in this suite reports the same host
    ("testclient"), and the counters live in module-level dicts that live as
    long as the process — so the requests of test 61 were counted against test
    1. test_dashboard_tokens.py tripped that on its last few cases and failed
    with `KeyError: 'code'` / 429s that had nothing to do with what it asserted.

    Running the file alone passed, which is exactly why it survived: a shared
    global crossed a production limit only under full-suite ordering.

    This is a fixture, not a change to the limiter. 60/min/IP is the intended
    production behaviour and nothing here weakens it.
    """
    from src.server.openai_server import security

    def _clear():
        # Clear the dicts outright rather than the per-IP helpers: the helpers
        # take a lock and need their own event loop, and this runs between tests
        # where nothing is in flight.
        security._dash_hits.clear()
        security._login_hits.clear()
        security._bf_fails.clear()
        security._bf_blocked.clear()

    _clear()
    yield
    _clear()


@pytest.fixture
def temp_db():
    """Point src.backend._db at a throwaway file for the duration of one test.

    Mirrors what several tests were already doing by hand, in one place so the
    next one does not forget. Restores _DB and clears the bootstrap flag so the
    real database is re-read afterwards.

    The router singleton is also refreshed, and that is not optional. It holds
    key status loaded at import time from whatever _DB pointed at then. A test
    that boots the app registers keys and records successes through that
    cached state, and those writes go back to the real database — which is how
    a suite that redirects _DB still ends up writing to usage.db. Refreshing
    after the redirect points the router at the temp file, and refreshing again
    on teardown points it back.
    """
    import tempfile

    from src.core.router import router as core_router

    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    original_db = db_mod._DB
    original_boot = db_mod._bootstrapped
    db_mod._DB = path
    db_mod._bootstrapped = False
    try:
        schema_mod.init_config_tables()
        core_router.refresh_keys()
        yield path
    finally:
        db_mod._DB = original_db
        db_mod._bootstrapped = original_boot
        try:
            core_router.refresh_keys()
        except Exception:
            pass          # teardown must not mask the test's own failure
        for suffix in ('', '-wal', '-shm'):
            try:
                os.unlink(path + suffix)
            except OSError:
                pass
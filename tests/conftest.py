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
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import time

import pytest

from src.backend import _db as db_mod
from src.backend import schema as schema_mod

# The guard and the fake-provider kit are sibling modules, not a package. pytest
# puts this directory on sys.path when it imports this file, but only when it is
# running as the suite root's conftest — say it out loud rather than relying on
# it, because the resulting ImportError is otherwise very confusing.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import fake_provider          # noqa: E402  (needs the sys.path line above)
import network_guard          # noqa: E402

REAL_DB = 'usage.db'

TABLES = ('accounts', 'account_keys', 'account_credentials', 'invite_codes',
          'custom_endpoints', 'model_config', 'key_status', 'key_penalties')


def _row_counts(path):
    """Row counts per table, or a sentinel if the file is absent.

    Absence is data, not an excuse to skip: a missing usage.db that turns into a
    20 KB file full of tables is still a change to the developer's database.

    key_penalties is counted differently, and deliberately. `db_clean_expired_
    penalties` deletes `WHERE expires <= now` on a timer, so a suite that runs
    longer than a penalty's lifetime sweeps the developer's expired rows on its
    way past — the row count drops and the guard fires on the app cleaning up
    after itself. Nothing is lost: the next real request would have deleted the
    same row. So what this table actually has to protect is the rows that still
    mean something. A penalty a test leaves behind with a live `expires` is a
    penalty that suppresses a real key's score, and that still trips the guard.

    The cost is stated rather than hidden: a test that inserts an already-expired
    penalty is invisible here. That is the same class of state as the sweep
    removes, so it would not survive the next real request either.
    """
    if not os.path.exists(path):
        return None
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        out = {}
        for t in TABLES:
            try:
                if t == 'key_penalties':
                    # Snapshot active penalty pkeys. Comparing existing keys and ignoring
                    # TTL expiration avoids false alarms from natural expiration during test runs.
                    out[t] = set(
                        row[0] for row in con.execute(
                            'SELECT penalty_id FROM key_penalties WHERE expires > ?',
                            (time.time(),)
                        ).fetchall()
                    )
                else:
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
def _mcp_accepts_the_test_client_host():
    """Keep an operator's MCP_ALLOWED_HOSTS from breaking the MCP tests.

    TestClient sends ``Host: testserver``. The variable *replaces* the default
    list instead of extending it, so anyone who added their own domain to
    ``.env`` — the documented fix for a 421 in production — silently dropped
    ``testserver`` and every MCP test answered 421 here. Green on a clean
    checkout, red in a working copy: the same shape as
    ``_password_recovery_is_testable`` above, and for the same reason.

    The import has to come first. ``config.py`` calls
    ``load_dotenv(override=True)`` at module import, which runs lazily when the
    first test imports the app — after this fixture — and would put the ``.env``
    value straight back over whatever is set here. Importing it explicitly
    makes the ordering deterministic instead of a race between two fixtures.
    """
    import importlib

    importlib.import_module("src.core.config_n_logg.config")

    existing = os.environ.get('MCP_ALLOWED_HOSTS', '')
    hosts = [h.strip() for h in existing.split(',') if h.strip()]
    for required in ('testserver', 'testclient'):
        if required not in hosts:
            hosts.append(required)
    os.environ['MCP_ALLOWED_HOSTS'] = ','.join(hosts)
    yield
    if existing:
        os.environ['MCP_ALLOWED_HOSTS'] = existing
    else:
        os.environ.pop('MCP_ALLOWED_HOSTS', None)


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
    changed = {}
    for t in TABLES:
        b = before.get(t)
        a = after.get(t)
        if t == 'key_penalties':
            # Only trigger guard if the suite added unexpected penalty rows to the real DB
            if isinstance(b, set) and isinstance(a, set):
                added = a - b
                if added:
                    changed[t] = f"added unexpected rows: {added}"
        elif b != a:
            changed[t] = (b, a)
    assert not changed, (
        f"the test suite wrote to {REAL_DB}: {changed}. "
        "Tests must not touch the real database — use the temp_db fixture, which "
        "redirects src.backend._db for the duration of the test."
    )


@pytest.fixture(autouse=True)
def _no_shared_account_rate_limit():
    """Keep provider wire tests independent of persisted/account limiter state.

    The provider fixtures replace the upstream provider, so consuming the real
    account TPM/RPM budget would test SQLite-backed throttling rather than the
    wire adapter.  CI starts with a fresh database and therefore exposes this
    ordering leak much more reliably than a developer checkout.
    """
    from src.core.limits.account_limiter import account_limiter
    from src.core.limits import token_limiter

    account_limiter.reset()
    token_limiter.reset()
    yield
    account_limiter.reset()
    token_limiter.reset()


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
        try:
            from src.backend.keys.status import flush_pending_writes
            flush_pending_writes()
        except Exception:
            pass
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


# ── no paid LLM calls ──────────────────────────────────────────────────────
#
# The database guard above and this one are the same argument applied to a
# different resource. A test that writes to usage.db corrupts the developer's
# state; a test that calls a real provider spends money, needs a credential CI
# does not have, and asserts against whatever the provider returned today. Both
# failures are silent — the test passes, or fails for an unrelated reason.
#
# See tests/network_guard.py for why the block sits on the socket rather than on
# any one client.


@pytest.fixture(autouse=True)
def _no_paid_provider_network(request):
    """Fail the test if it tried to reach a paid LLM host.

    Autouse and function-scoped, so it wraps every test in the run. The guard
    raises on the connect *and* records it: the raise is what a test sees when
    nothing catches the exception, and the record is what catches the case that
    matters more — the retry loops in pool_manager handle provider errors on
    purpose, so a blocked call would otherwise be swallowed and leave the test
    green while asserting nothing.
    """
    if network_guard.guard_exempt(request.node):
        yield                       # live test, explicitly opted into
        return

    mark = len(network_guard.attempts())
    restore = network_guard.install()
    try:
        yield
    finally:
        restore()
        try:
            network_guard.assert_no_blocked_attempts(mark)
        except network_guard.BlockedHostError as exc:
            pytest.fail(str(exc))


def pytest_collection_modifyitems(items):
    """Leave `live` tests out of the run unless they were opted into.

    A test marked `@pytest.mark.live` costs money, so it is skipped by default
    rather than merely discouraged — and the marker on its own is not enough to
    lift the network guard either (see `network_guard.guard_exempt`).
    """
    network_guard.skip_unallowed_live(items)


# ── the deterministic test kit ─────────────────────────────────────────────
#
# Fixtures over tests/fake_provider.py. Tests can import that module directly;
# these exist so the common case is one argument instead of a two-step
# install, and so the kit has a documented entry point in one place.


@pytest.fixture
def provider(monkeypatch):
    """Build a scripted provider and install it for this test.

        def test_x(provider):
            provider(fake_provider.text_scenario("hi"))

    or, for a bare provider with no install:

        provider(fake_provider.text_scenario("hi"), install=False)
    """
    def _provider(fake: fake_provider.FakeProvider, install: bool = True, **kw):
        return fake.install(monkeypatch, **kw) if install else fake

    return _provider


@pytest.fixture
def fake_search(monkeypatch):
    """A local, deterministic web search.

    Patches the DuckDuckGo scrape rather than the search manager, so
    `execute_hybrid_search` still runs for real — the engine dispatch is part of
    what the test is checking, and the HTTP scrape is the only part that would
    touch the network. Use `fake_search.install_manager(...)` for route-level
    tests that only need the finished `(text, citations)` pair.
    """
    return fake_provider.FakeSearch().install(monkeypatch)
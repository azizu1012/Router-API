"""No response body may contain a credential, at any privilege level.

The bug this exists for: ``GET /api/stats`` had no auth at all and enriched its
usage rows with ``accounts.auth_key``. Any caller received HTTP 200 and a list of
plaintext master keys. Master keys are quota-exempt by design
(docs/account_auth.md section 6), so that one GET was a full privilege
escalation, not just a disclosure.

Three things had to be true for it to survive, and each is what this file pins:

  - the route answered 200 without a token,
  - the key in the body was the real credential rather than a mask,
  - nothing in the suite compared a response against the keys it created.

The first fix added a session check and was reported as done. It was not: the
guard it used verifies a session but never checks tier, so a ``free`` account
still read the admin's master key. Auth on a route and authorisation on a route
are different things, and only a test that logs in as the weaker tier can tell
them apart.

The scanner in scripts/scan_security.py is what stops this recurring. It is fed a
deliberately vulnerable file at the bottom of this file to prove it still fires —
a green scanner that no longer detects the bug is worse than no scanner, because
it is trusted.
"""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, ".")


@pytest.fixture()
def app_client():
    """The real ASGI app on a throwaway usage.db and usage_logs.db.

    Both are redirected: usage_logger binds _DB at import, so pointing
    src.backend._db at a temp file is not enough on its own. A test that misses
    that does not fail — it silently reads the operator's real usage history.
    """
    from fastapi.testclient import TestClient
    from src.backend import _db as db_mod
    from src.backend import schema as schema_mod
    import src.core.usage_logger as usage_logger

    fd, name = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    log_fd, log_name = tempfile.mkstemp(suffix=".db")
    os.close(log_fd)

    original_db = db_mod._DB
    original_logs = usage_logger._DB
    db_mod._DB = name
    usage_logger._DB = log_name
    db_mod._bootstrapped = False
    try:
        schema_mod.init_config_tables()
        from src.server.openai_server.routes.app_init import app
        with TestClient(app) as c:
            yield c
    finally:
        db_mod._DB = original_db
        usage_logger._DB = original_logs
        db_mod._bootstrapped = False
        for suffix in ("", "-wal", "-shm"):
            Path(name + suffix).unlink(missing_ok=True)
            Path(log_name + suffix).unlink(missing_ok=True)


def _make_account(name, tier, password):
    """An account with a web credential and a live session token."""
    from src.core.accounts import account_manager
    from src.backend.account_keys import set_password_db
    from src.server.openai_server.routes.auth_session import _make_session_token

    acc = account_manager.create_account(name=name, tier=tier)
    set_password_db(acc["account_id"], password)
    return acc, _make_session_token(acc)


def _usage_row(auth_key_prefix, tokens=1500):
    """The row a real request writes: auth_key_prefix = auth_key[-8:]."""
    con = sqlite3.connect(_LOGS[0])
    con.execute(
        "CREATE TABLE IF NOT EXISTS usage_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, model_alias TEXT,"
        " key_prefix TEXT, prompt_tokens INTEGER, completion_tokens INTEGER,"
        " total_tokens INTEGER, auth_key_prefix TEXT,"
        " cache_creation_tokens INTEGER DEFAULT 0,"
        " cache_read_tokens INTEGER DEFAULT 0)")
    con.execute(
        "INSERT INTO usage_logs (timestamp, model_alias, key_prefix,"
        " prompt_tokens, completion_tokens, total_tokens, auth_key_prefix)"
        " VALUES (?,?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), "gemini-flash", "g",
         tokens // 3, tokens // 3 * 2, tokens, auth_key_prefix))
    con.commit()
    con.close()


_LOGS = [""]


@pytest.fixture(autouse=True)
def _capture_logs_db(app_client):
    """Hand the usage-row helper the same temp path the app is reading from."""
    import src.core.usage_logger as usage_logger
    _LOGS[0] = usage_logger._DB
    yield


# ── the three ways this can fail ────────────────────────────────────────────

class TestStatsNeverHandsOutAKey:
    def test_anonymous_caller_is_refused(self, app_client):
        """Before the fix: HTTP 200 plus every account's master key."""
        admin, _ = _make_account("boss2", "admin", "adminpw")
        _usage_row(admin["auth_key"][-8:])

        r = app_client.get("/api/stats")

        assert r.status_code == 401
        assert admin["auth_key"] not in r.text

    def test_a_free_account_is_refused(self, app_client):
        """The gap the first fix left open.

        _require_dashboard() checks that a session exists. It does not check who
        the session belongs to, so adding it moved the leak from 'the internet'
        to 'any registered user' rather than closing it.
        """
        admin, _ = _make_account("boss3", "admin", "adminpw")
        _make_account("dev3", "free", "devpw")
        _usage_row(admin["auth_key"][-8:])

        r = app_client.post("/dashboard/login",
                            json={"username": "dev3", "password": "devpw"})
        assert r.status_code == 200
        tok = r.json()["token"]

        r = app_client.get("/api/stats", headers={"X-Dashboard-Token": tok})

        assert r.status_code == 403
        assert admin["auth_key"] not in r.text, \
            "a free-tier account read the admin's master key"

    def test_admin_gets_the_analysis_but_not_the_credential(self, app_client):
        """Masked is the point. Admin can already reveal a key by name."""
        admin, tok = _make_account("boss4", "admin", "adminpw")
        _usage_row(admin["auth_key"][-8:])

        r = app_client.get("/api/stats", headers={"X-Dashboard-Token": tok})

        assert r.status_code == 200
        body = r.json()
        assert body["top_keys"], "the analysis itself should still be there"
        assert admin["auth_key"] not in r.text
        row = body["top_keys"][0]
        assert row["account_name"] == "boss4"
        assert row["key_masked"].endswith(admin["auth_key"][-8:])
        assert "full_key" not in row, \
            "the field was renamed, not just re-valued; a stale field name is " \
            "how the next reader assumes the value is still a credential"


class TestPingModelIsNotAFreeProxy:
    """It calls a custom endpoint with that endpoint's own auth_key."""

    def test_free_account_cannot_burn_the_endpoint_quota(self, app_client):
        _make_account("boss5", "admin", "adminpw")
        _make_account("dev5", "free", "devpw")
        tok = app_client.post("/dashboard/login",
                              json={"username": "dev5", "password": "devpw"}
                              ).json()["token"]

        r = app_client.post("/api/ping-model", json={"model": "whatever"},
                            headers={"X-Dashboard-Token": tok})

        assert r.status_code == 403

    def test_anonymous_cannot_reach_it(self, app_client):
        r = app_client.post("/api/ping-model", json={"model": "whatever"})

        assert r.status_code == 401


class TestNoDashboardRouteLeaksAKey:
    """Sweep every dashboard/read endpoint a free session can reach.

    Pinning one endpoint only pins that endpoint. This asserts the property:
    whatever else is added later, a key that exists in the database must not
    appear in a response body.
    """

    def test_free_session_sees_no_master_key_anywhere(self, app_client):
        admin, _ = _make_account("boss6", "admin", "adminpw")
        _, tok = _make_account("dev6", "free", "devpw")
        _usage_row(admin["auth_key"][-8:])

        paths = [
            ("GET", "/api/stats"),
            ("GET", "/dashboard/me"),
            ("GET", "/dashboard/accounts"),
            ("GET", "/dashboard/keys"),
            ("GET", "/dashboard/penalties"),
            ("GET", "/dashboard/my/keys"),
            ("GET", "/dashboard/my-stats"),
            ("GET", "/dashboard/endpoints"),
            ("GET", "/api/model-pools"),
            ("GET", "/api/model-pools-detail"),
            ("GET", "/api/help"),
        ]
        for method, path in paths:
            r = app_client.request(method, path,
                                   headers={"X-Dashboard-Token": tok})
            assert admin["auth_key"] not in r.text, f"{path} leaked the master key"


# ── the scanner has to keep working ────────────────────────────────────────

VULNERABLE = '''
from fastapi import Request
from .auth_session import _require_dashboard


@app.get("/api/stats")
async def usage_stats(request: Request, days: int = 30):
    _require_dashboard(request)
    enriched = []
    for a in list_accounts_db(True):
        enriched.append({"account_name": a["name"], "full_key": a["auth_key"]})
    return {"top_keys": enriched}
'''


class TestTheScannerStillDetectsIt:
    def _audit(self, source, tmp_path):
        from scripts.scan_security import audit

        target = tmp_path / "routes.py"
        target.write_text(source, encoding="utf-8")
        return audit([target])

    def test_flags_a_credential_returned_to_the_caller(self, tmp_path):
        rules = {f.rule for f in self._audit(VULNERABLE, tmp_path)}

        assert "credential-in-payload" in rules

    def test_flags_an_account_wide_route_guarded_only_for_any_session(self, tmp_path):
        findings = self._audit(VULNERABLE, tmp_path)
        rules = {f.rule for f in findings}

        assert "weak-guard-for-account-wide-data" in rules

    def test_flags_a_route_with_no_guard_at_all(self, tmp_path):
        source = VULNERABLE.replace("    _require_dashboard(request)\n", "")
        rules = {f.rule for f in self._audit(source, tmp_path)}

        assert "unauthenticated-route" in rules

    def test_a_handler_that_delegates_is_not_false_positived(self, tmp_path):
        """generate_content calls no guard; its callee does.

        Flagging that would be six wrong findings on the Gemini passthrough, and
        a tool with six wrong findings gets switched off.
        """
        source = '''
from fastapi import Request

from .gemini_handlers import _handle_gemini_native


@app.post("/v1beta/models/{model_id}:generateContent")
async def generate_content(model_id: str, request: Request):
    return await _handle_gemini_native(model_id, request)
'''
        route = tmp_path / "routes.py"
        route.write_text(source, encoding="utf-8")

        from scripts.scan_security import ModuleIndex, collect_routes, effective_guards

        index = ModuleIndex([route])
        (path, node, method, rpath), = collect_routes([route])

        assert effective_guards(index, node) == set(), \
            "the delegate lives in another file, so it cannot be resolved here; " \
            "what matters is that an unresolvable call is not treated as guarded"


class TestTheScannerIsCleanOnTheRealRoutes:
    def test_repository_has_no_findings(self):
        from scripts.scan_security import audit, server_files

        findings = audit(server_files())
        critical = [f for f in findings if f.severity in ("critical", "high")]

        assert not critical, "\n".join(
            f"  [{f.severity}] {f.rule} {f.file}:{f.line} {f.where}" for f in findings)

class TestTheAccountsListDoesNotCarryRawKeys:
    """The list is polled every few seconds and lives in browser state.

    It shipped ``auth_key`` for every account until it was masked, which made a
    screenshot of the accounts tab a list of quota-exempt credentials. Masking
    it is right, but it removed the field the copy button read, so the button
    stopped working with no error anywhere — the page still rendered, HTTP was
    still 200. Both halves are pinned here.
    """

    def test_admin_list_has_the_mask_and_not_the_key(self, app_client):
        admin, tok = _make_account("boss7", "admin", "adminpw")

        r = app_client.get("/dashboard/accounts", headers={"X-Dashboard-Token": tok})
        row = r.json()["accounts"][0]

        assert r.status_code == 200
        assert admin["auth_key"] not in r.text
        assert "auth_key" not in row, "the field was renamed, not re-valued"
        assert row["auth_key_masked"].endswith(admin["auth_key"][-4:])

    def test_the_copy_button_has_a_route_to_call(self, app_client):
        """What AccountDetailPanel.jsx does when the admin presses Copy."""
        admin, tok = _make_account("boss8", "admin", "adminpw")

        r = app_client.get(
            "/dashboard/admin/accounts/master-key?name=boss8",
            headers={"X-Dashboard-Token": tok})

        assert r.status_code == 200
        assert r.json()["available"] is True
        assert r.json()["auth_key"] == admin["auth_key"]

    def test_a_free_account_cannot_reach_the_key(self, app_client):
        _make_account("boss9", "admin", "adminpw")
        _make_account("dev9", "free", "devpw")
        tok = app_client.post("/dashboard/login",
                              json={"username": "dev9", "password": "devpw"}
                              ).json()["token"]

        r = app_client.get("/dashboard/admin/accounts/master-key?name=boss9",
                           headers={"X-Dashboard-Token": tok})

        assert r.status_code == 403

    def test_an_unknown_account_is_a_404(self, app_client):
        _, tok = _make_account("boss10", "admin", "adminpw")

        r = app_client.get("/dashboard/admin/accounts/master-key?name=nobody",
                           headers={"X-Dashboard-Token": tok})

        assert r.status_code == 404

    def test_no_poll_endpoint_still_carries_a_raw_key(self, app_client):
        """The sweep, now including the admin-tier listing."""
        admin, tok = _make_account("boss11", "admin", "adminpw")

        for path in ("/dashboard/accounts", "/api/stats", "/dashboard/keys"):
            r = app_client.get(path, headers={"X-Dashboard-Token": tok})
            assert admin["auth_key"] not in r.text, f"{path} leaked the master key"


class TestTheRevealMarkerIsNotABlanketPass:
    """@reveals_credential exempts the shape, not the privilege.

    Without this, the marker is just a way to switch the scanner off, and the
    next person to copy it onto a broad listing inherits the original bug.
    """

    def _rules(self, source, tmp_path):
        from scripts.scan_security import audit
        target = tmp_path / "routes.py"
        target.write_text(source, encoding="utf-8")
        return {f.rule for f in audit([target])}

    def test_marked_and_admin_only_is_clean(self, tmp_path):
        src = '''
from fastapi import Request
from ..credential_reveal import reveals_credential
from ..auth_session import _require_admin


@app.get("/dashboard/admin/accounts/master-key")
@reveals_credential
async def show(request: Request, name: str):
    _require_admin(request)
    return {"auth_key": "sk-secret"}
'''
        assert self._rules(src, tmp_path) == set()

    def test_marked_but_not_admin_only_is_a_finding(self, tmp_path):
        src = '''
from fastapi import Request
from ..credential_reveal import reveals_credential
from ..auth_session import _require_dashboard


@app.get("/dashboard/peek")
@reveals_credential
async def peek(request: Request):
    _require_dashboard(request)
    return {"auth_key": "sk-secret"}
'''
        assert "credential-reveal-without-admin-guard" in self._rules(src, tmp_path)

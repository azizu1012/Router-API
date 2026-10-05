"""Token management endpoints, exercised through the real ASGI app.

The dashboard UI is only useful if these endpoints agree with what it sends:
clamping, ownership checks, and the fields the client reads back. Mocking the
DB layer would miss exactly those mismatches, so this drives the app with a
real temporary database.

Covers both scopes, because they are deliberately asymmetric — an admin may
raise a ceiling, a user may only tighten.
"""

import asyncio
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, ".")


@pytest.fixture()
def client():
    """The real app, pointed at a throwaway usage.db."""
    from fastapi.testclient import TestClient
    from src.backend import _db as db_mod
    from src.backend import schema as schema_mod

    fd, name = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    tmp = Path(name)

    original = db_mod._DB
    db_mod._DB = str(tmp)
    db_mod._bootstrapped = False
    try:
        schema_mod.init_config_tables()
        from src.server.openai_server.routes.app_init import app
        with TestClient(app) as c:
            yield c
    finally:
        db_mod._DB = original
        db_mod._bootstrapped = False
        for suffix in ("", "-wal", "-shm"):
            Path(str(tmp) + suffix).unlink(missing_ok=True)


def _admin(client):
    """An admin account with a web credential, plus its session token."""
    from src.core.accounts import account_manager
    from src.backend.account_keys import set_password_db
    from src.server.openai_server.routes.auth_session import _make_session_token

    acc = account_manager.create_account(name="boss", tier="admin")
    set_password_db(acc["account_id"], "adminpw")
    return acc, _make_session_token(acc)


def _user(client, name="dev"):
    from src.core.accounts import account_manager
    from src.backend.account_keys import set_password_db
    from src.server.openai_server.routes.auth_session import _make_session_token

    acc = account_manager.create_account(name=name, tier="free")
    set_password_db(acc["account_id"], "userpw")
    return acc, _make_session_token(acc)


def _h(session):
    return {"X-Dashboard-Token": session, "Authorization": f"Bearer {session}"}


# ── create ─────────────────────────────────────────────────────────────────

class TestIssueToken:
    def test_admin_can_issue(self, client):
        _, admin = _admin(client)
        acc, _ = _user(client)
        r = client.post("/dashboard/admin/accounts/keys/issue",
                        json={"name": acc["name"]}, headers=_h(admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["token"].startswith(f"sk-{acc['name']}-")
        assert len(body["token"].split("-")[-1]) == 6

    def test_non_admin_cannot_issue_for_others(self, client):
        _, admin = _admin(client)
        acc, user = _user(client)
        r = client.post("/dashboard/admin/accounts/keys/issue",
                        json={"name": acc["name"]}, headers=_h(user))
        assert r.status_code in (401, 403), r.text

    def test_user_creates_their_own(self, client):
        _, user = _user(client)
        r = client.post("/dashboard/my/keys/create", json={}, headers=_h(user))
        assert r.status_code == 200, r.text
        assert r.json()["token"].startswith("sk-dev-")

    def test_unknown_account_404(self, client):
        _, admin = _admin(client)
        r = client.post("/dashboard/admin/accounts/keys/issue",
                        json={"name": "nobody"}, headers=_h(admin))
        assert r.status_code == 404
        assert "not found" in r.json()["detail"].lower()


# ── the four independent limits ────────────────────────────────────────────

class TestLimitsAreIndependent:
    def test_user_cannot_exceed_default_concurrency(self, client):
        _, user = _user(client)
        r = client.post("/dashboard/my/keys/create",
                        json={"max_concurrency": 64}, headers=_h(user))
        assert r.json()["max_concurrency"] == 6, "free user is capped at 6"

    def test_admin_can_exceed_default_concurrency(self, client):
        _, admin = _admin(client)
        acc, _ = _user(client)
        r = client.post("/dashboard/admin/accounts/keys/issue",
                        json={"name": acc["name"], "max_concurrency": 32},
                        headers=_h(admin))
        assert r.json()["max_concurrency"] == 32

    def test_each_limit_takes_its_own_value(self, client):
        _, admin = _admin(client)
        acc, _ = _user(client)
        r = client.post("/dashboard/admin/accounts/keys/issue", json={
            "name": acc["name"], "rpm": 111, "tpm": 222_000,
            "rpd": 333, "max_concurrency": 7,
        }, headers=_h(admin))
        b = r.json()
        assert (b["rpm"], b["tpm"], b["rpd"], b["max_concurrency"]) == \
               (111, 222_000, 333, 7)

    def test_interval_reported_inactive_beyond_one_slot(self, client):
        """interval_effective must be False or the UI explains nothing."""
        _, admin = _admin(client)
        acc, _ = _user(client)
        r = client.post("/dashboard/admin/accounts/keys/issue", json={
            "name": acc["name"], "max_concurrency": 6,
            "min_interval_seconds": 3,
        }, headers=_h(admin))
        assert r.json()["interval_effective"] is False

    def test_interval_effective_at_one_slot(self, client):
        _, admin = _admin(client)
        acc, _ = _user(client)
        r = client.post("/dashboard/admin/accounts/keys/issue", json={
            "name": acc["name"], "max_concurrency": 1,
            "min_interval_seconds": 3,
        }, headers=_h(admin))
        assert r.json()["interval_effective"] is True


# ── list ───────────────────────────────────────────────────────────────────

class TestListTokens:
    def test_admin_sees_tokens_of_a_named_account(self, client):
        _, admin = _admin(client)
        acc, _ = _user(client)
        client.post("/dashboard/admin/accounts/keys/issue",
                    json={"name": acc["name"]}, headers=_h(admin))
        r = client.post("/dashboard/admin/accounts/keys",
                        json={"name": acc["name"]}, headers=_h(admin))
        assert len(r.json()["keys"]) == 1

    def test_list_includes_the_fields_the_ui_renders(self, client):
        _, admin = _admin(client)
        acc, _ = _user(client)
        client.post("/dashboard/admin/accounts/keys/issue",
                    json={"name": acc["name"], "label": "laptop"},
                    headers=_h(admin))
        k = client.post("/dashboard/admin/accounts/keys",
                        json={"name": acc["name"]},
                        headers=_h(admin)).json()["keys"][0]
        for field in ("key_id", "token", "label", "enabled", "tier",
                      "rpm", "tpm", "rpd", "max_concurrency",
                      "min_interval_seconds"):
            assert field in k, f"TokenTable reads {field}"
        assert k["label"] == "laptop"

    def test_user_sees_only_their_own(self, client):
        _, a1 = _user(client, "one")
        _, a2 = _user(client, "two")
        client.post("/dashboard/my/keys/create", json={}, headers=_h(a1))
        client.post("/dashboard/my/keys/create", json={}, headers=_h(a1))
        client.post("/dashboard/my/keys/create", json={}, headers=_h(a2))
        assert len(client.get("/dashboard/my/keys", headers=_h(a1)).json()["keys"]) == 2
        assert len(client.get("/dashboard/my/keys", headers=_h(a2)).json()["keys"]) == 1


# ── update ─────────────────────────────────────────────────────────────────

class TestUpdateToken:
    def _issue(self, client, admin, name, **kw):
        r = client.post("/dashboard/admin/accounts/keys/issue",
                        json={"name": name, **kw}, headers=_h(admin))
        return r.json()["key_id"]

    def test_admin_raises_a_limit(self, client):
        _, admin = _admin(client)
        acc, _ = _user(client)
        kid = self._issue(client, admin, acc["name"], rpm=10)
        r = client.post("/dashboard/admin/accounts/keys/update",
                        json={"key_id": kid, "rpm": 999},
                        headers=_h(admin))
        assert r.status_code == 200, r.text
        ks = client.post("/dashboard/admin/accounts/keys",
                         json={"name": acc["name"]},
                         headers=_h(admin)).json()["keys"]
        assert ks[0]["rpm"] == 999

    def test_user_can_tighten_own_token(self, client):
        _, user = _user(client)
        kid = client.post("/dashboard/my/keys/create",
                          json={"rpm": 100}, headers=_h(user)).json()["key_id"]
        r = client.post("/dashboard/my/keys/update",
                        json={"key_id": kid, "rpm": 20}, headers=_h(user))
        assert r.status_code == 200, r.text
        ks = client.get("/dashboard/my/keys", headers=_h(user)).json()["keys"]
        assert ks[0]["rpm"] == 20

    def test_user_cannot_widen_beyond_ceiling(self, client):
        """The clamp is the whole point: a hand-rolled client must not win."""
        _, user = _user(client)
        kid = client.post("/dashboard/my/keys/create",
                          json={}, headers=_h(user)).json()["key_id"]
        client.post("/dashboard/my/keys/update",
                    json={"key_id": kid, "max_concurrency": 60},
                    headers=_h(user))
        ks = client.get("/dashboard/my/keys", headers=_h(user)).json()["keys"]
        assert ks[0]["max_concurrency"] == 6

    def test_user_cannot_touch_another_accounts_token(self, client):
        _, admin = _admin(client)
        victim, _ = _user(client, "victim")
        _, attacker = _user(client, "attacker")
        kid = self._issue(client, admin, victim["name"])
        r = client.post("/dashboard/my/keys/update",
                        json={"key_id": kid, "rpm": 1}, headers=_h(attacker))
        assert r.status_code == 404, r.text

    def test_empty_update_rejected(self, client):
        _, user = _user(client)
        kid = client.post("/dashboard/my/keys/create", json={},
                          headers=_h(user)).json()["key_id"]
        r = client.post("/dashboard/my/keys/update",
                        json={"key_id": kid}, headers=_h(user))
        assert r.status_code == 400

    def test_disabling_a_token_is_readable_afterwards(self, client):
        _, user = _user(client)
        kid = client.post("/dashboard/my/keys/create", json={},
                          headers=_h(user)).json()["key_id"]
        client.post("/dashboard/my/keys/update",
                    json={"key_id": kid, "enabled": False}, headers=_h(user))
        ks = client.get("/dashboard/my/keys", headers=_h(user)).json()["keys"]
        assert ks[0]["enabled"] is False


# ── revoke ─────────────────────────────────────────────────────────────────

class TestRevokeToken:
    def test_revoked_token_disappears(self, client):
        _, user = _user(client)
        kid = client.post("/dashboard/my/keys/create", json={},
                          headers=_h(user)).json()["key_id"]
        r = client.post("/dashboard/my/keys/revoke",
                        json={"key_id": kid}, headers=_h(user))
        assert r.status_code == 200, r.text
        assert client.get("/dashboard/my/keys",
                          headers=_h(user)).json()["keys"] == []

    def test_cannot_revoke_another_accounts_token(self, client):
        _, admin = _admin(client)
        victim, _ = _user(client, "victim")
        _, attacker = _user(client, "attacker")
        kid = client.post("/dashboard/admin/accounts/keys/issue",
                          json={"name": victim["name"]},
                          headers=_h(admin)).json()["key_id"]
        r = client.post("/dashboard/my/keys/revoke",
                        json={"key_id": kid}, headers=_h(attacker))
        assert r.status_code == 404


# ── invites ────────────────────────────────────────────────────────────────

class TestInvites:
    def test_issue_returns_a_code(self, client):
        _, admin = _admin(client)
        r = client.post("/dashboard/admin/invites/issue", json={},
                        headers=_h(admin))
        assert r.status_code == 200, r.text
        code = r.json()["code"]
        assert len(code) == 4 and code.isdigit()

    def test_ttl_is_clamped(self, client):
        _, admin = _admin(client)
        r = client.post("/dashboard/admin/invites/issue",
                        json={"ttl_seconds": 5}, headers=_h(admin))
        assert r.json()["ttl_seconds"] == 30, "below the floor is raised"

    def test_reissue_supersedes(self, client):
        _, admin = _admin(client)
        first = client.post("/dashboard/admin/invites/issue", json={},
                            headers=_h(admin)).json()["code"]
        second = client.post("/dashboard/admin/invites/issue", json={},
                             headers=_h(admin)).json()["code"]
        assert first != second, "two live codes at once would break the rule"

    def test_user_cannot_issue(self, client):
        _, user = _user(client)
        r = client.post("/dashboard/admin/invites/issue", json={},
                        headers=_h(user))
        assert r.status_code in (401, 403)

    def test_register_consumes_the_code(self, client):
        _, admin = _admin(client)
        code = client.post("/dashboard/admin/invites/issue", json={},
                           headers=_h(admin)).json()["code"]
        r = client.post("/dashboard/register",
                        json={"invite_code": code, "name": "newbie",
                              "password": "abcd"})
        assert r.status_code == 200, r.text
        # and the same code cannot enroll a second account
        again = client.post("/dashboard/register",
                            json={"invite_code": code, "name": "other",
                                  "password": "abcd"})
        assert again.status_code in (400, 403), again.text
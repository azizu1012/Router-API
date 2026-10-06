"""The admin route that hands a password back.

Two things are being held down here. The first is access: only an admin tier
may read a password, and no tier flag on the request is trusted. The second is
honesty: when the password cannot be recovered the route says why instead of
returning an empty string, because a blank field in a UI reads as "the password
is blank" and not as "this account predates the feature".

The third is that the account listing must not start carrying passwords. That
is the shape of the table an operator scans, and a screenshot of it should never
be a list of everyone's credentials.
"""
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.backend.password_recovery import ENV_KEY_NAME


@pytest.fixture
def client_with_admin(temp_db):
    """App wired to the temp DB, with one admin and one free account.

    The key is patched around the whole body, not just the requests. Setting a
    password is what writes the encrypted copy, so the env has to be in place
    before set_password_db runs — otherwise the fixture quietly tests the
    no-key path while claiming to test the keyed one.
    """
    from src.core.accounts import account_manager
    from src.backend.account_keys import set_password_db
    from src.server.openai_server.routes.auth_session import _make_session_token
    from src.server.openai_server.routes.app_init import app

    with patch.dict(os.environ, {ENV_KEY_NAME: "route-test-passphrase"}, clear=False):
        admin = account_manager.create_account(name="boss", tier="admin")
        set_password_db(admin["account_id"], "boss-secret")
        user = account_manager.create_account(name="peon", tier="free")
        set_password_db(user["account_id"], "peon-secret")

        with TestClient(app) as client:
            client.admin_session = _make_session_token(admin)
            client.user_session = _make_session_token(user)
            yield client


def _h(client, who="admin_session"):
    s = getattr(client, who)
    return {"X-Dashboard-Token": s, "Authorization": f"Bearer {s}"}


def _recovery(client, name, who="admin_session"):
    from urllib.parse import quote
    return client.get(
        f"/dashboard/admin/accounts/recovery?name={quote(name)}",
        headers=_h(client, who),
    )


class TestAdminCanRead:
    def test_the_password_comes_back(self, client_with_admin):
        client = client_with_admin
        res = _recovery(client, "peon").json()
        assert res["available"] is True
        assert res["password"] == "peon-secret"
        assert res["name"] == "peon"

    def test_it_works_for_the_admin_itself(self, client_with_admin):
        res = _recovery(client_with_admin, "boss").json()
        assert res["password"] == "boss-secret"

    def test_the_password_is_not_in_the_account_listing(self, client_with_admin):
        """The listing is what an operator scans. It must stay credential-free."""
        client = client_with_admin
        body = client.get("/dashboard/admin/accounts", headers=_h(client)).text
        assert "peon-secret" not in body
        assert "password" not in body.lower()


class TestAccessControl:
    def test_a_free_account_is_refused(self, client_with_admin):
        client = client_with_admin
        out = client.get(
            "/dashboard/admin/accounts/recovery?name=peon",
            headers=_h(client, "user_session"),
        )
        assert out.status_code == 403

    def test_no_token_at_all_is_refused(self, client_with_admin):
        client = client_with_admin
        out = client.get("/dashboard/admin/accounts/recovery?name=peon")
        assert out.status_code == 401

    def test_a_forged_tier_header_is_refused(self, client_with_admin):
        """Tier comes from the session, not from anything the caller sends."""
        client = client_with_admin
        out = client.get(
            "/dashboard/admin/accounts/recovery?name=peon",
            headers={"Authorization": "Bearer nonsense", "X-Tier": "admin"},
        )
        assert out.status_code == 401


class TestHonestFailures:
    def test_an_unknown_account_is_404(self, client_with_admin):
        assert _recovery(client_with_admin, "ghost").status_code == 404

    def test_a_blank_name_is_400(self, client_with_admin):
        assert _recovery(client_with_admin, "").status_code == 400

    def test_no_key_configured_reports_the_key_not_a_blank_password(self, client_with_admin):
        """The distinction that matters: unavailable is not the same as empty."""
        client = client_with_admin
        env = dict(os.environ)
        env.pop(ENV_KEY_NAME, None)
        with patch.dict(os.environ, env, clear=True):
            res = _recovery(client, "peon").json()

        assert res["available"] is False
        assert res["password"] is None
        assert ENV_KEY_NAME in res["reason"], "must name the env var the operator has to set"

    def test_an_account_predating_the_feature_is_reported(self, client_with_admin):
        """A row written before the column existed must not read as a blank password."""
        from src.backend import _db
        from src.backend.accounts import create_account_db, find_account_by_name
        from src.backend.account_keys import set_password_db

        client = client_with_admin
        create_account_db(name="legacy")
        set_password_db(find_account_by_name("legacy")["account_id"], "legacy-secret")

        # exactly the shape an upgrade leaves behind: hash present, no copy
        with _db._LOCK:
            conn = _db.conn()
            conn.execute(
                "UPDATE account_credentials SET password_enc = NULL WHERE account_id = ?",
                (find_account_by_name("legacy")["account_id"],),
            )
            conn.commit()

        res = _recovery(client, "legacy").json()
        assert res["available"] is False
        assert res["password"] is None, "a missing copy must not come back as an empty password"
        assert res["reason"], "an unavailable password needs a reason the operator can act on"

    def test_a_row_whose_password_cannot_be_decrypted_says_so(self, client_with_admin):
        """Wrong key, or a blob from an older key — either way, no password out."""
        from src.backend import _db
        from src.backend.accounts import find_account_by_name

        client = client_with_admin
        blob = "not-a-valid-fernet-token"
        with _db._LOCK:
            conn = _db.conn()
            conn.execute(
                "UPDATE account_credentials SET password_enc = ? WHERE account_id = ?",
                (blob, find_account_by_name("peon")["account_id"]),
            )
            conn.commit()

        res = _recovery(client, "peon").json()
        assert res["available"] is False
        assert res["password"] is None

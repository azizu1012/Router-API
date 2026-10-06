"""The must-change flag, end to end.

This flag was written to the database correctly for a long time and never
reached the dashboard, because /dashboard/me did not include it in its payload.
Nothing raised, no request failed — the security banner simply never appeared.
So the tests here deliberately exercise the whole chain rather than the storage
function on its own: if someone drops the field from the /me payload again, the
banner test goes red instead of the feature quietly disappearing.

The flag is also not a lock. It is advisory text plus a button, so nothing in
these tests should depend on a login being blocked.
"""
import pytest
from fastapi.testclient import TestClient

from src.server.openai_server.routes.auth_session import _make_session_token
from src.server.openai_server.routes.app_init import app


@pytest.fixture
def world(temp_db):
    from src.core.accounts import account_manager
    from src.backend.account_keys import set_password_db
    from src.server.openai_server.security import clear_dashboard_rate

    # Every TestClient in the suite shares the host "testclient", and
    # /dashboard/* is capped at 60 req/min/IP. This file makes enough calls to
    # trip that on its own once other dashboard tests have run, which reads as a
    # broken feature rather than as a limiter doing its job.
    clear_dashboard_rate("testclient")

    admin = account_manager.create_account(name="boss", tier="admin")
    set_password_db(admin["account_id"], "adminpw")
    user = account_manager.create_account(name="dev", tier="free")
    set_password_db(user["account_id"], "userpw")

    with TestClient(app) as client:
        client.admin = _make_session_token(admin)
        client.dev = _make_session_token(user)
        yield client


def _h(client, who="admin"):
    s = getattr(client, who)
    return {"X-Dashboard-Token": s, "Authorization": f"Bearer {s}"}


def _flag(client, name, required, who="admin"):
    return client.post(
        "/dashboard/admin/accounts/require-password-change",
        json={"name": name, "required": required},
        headers=_h(client, who),
    )


class TestTheFlagReachesTheDashboard:
    """The chain that was broken."""

    def test_me_reports_the_flag_set(self, world):
        _flag(world, "dev", True)
        me = world.get("/dashboard/me", headers=_h(world, "dev")).json()
        assert me["must_change_password"] is True, (
            "the banner in App.jsx keys off this field; without it in /me the "
            "flag is stored and never displayed"
        )

    def test_me_reports_the_flag_clear(self, world):
        me = world.get("/dashboard/me", headers=_h(world, "dev")).json()
        assert me["must_change_password"] is False

    def test_the_field_is_present_even_when_false(self, world):
        """Absent and false are different to a JS truthiness check that has been
        wrong before. Pin that the key exists rather than being undefined."""
        me = world.get("/dashboard/me", headers=_h(world, "dev")).json()
        assert "must_change_password" in me

    def test_an_account_with_no_credential_row_still_gets_the_field(self, world):
        """No row is not an error — the banner should stay hidden, not explode."""
        from src.core.accounts import account_manager

        acc = account_manager.create_account(name="keyonly", tier="free")
        world.keyonly = _make_session_token(acc)
        me = world.get("/dashboard/me", headers=_h(world, "keyonly"))
        assert me.status_code == 200
        assert me.json()["must_change_password"] is False


class TestTheFlagIsIndependent:
    def test_raising_the_flag_leaves_the_password_working(self, world):
        """It is a banner, not a lock. Login must survive it."""
        from src.backend.account_keys import verify_login_db

        _flag(world, "dev", True)
        assert verify_login_db("dev", "userpw"), "login broke while the flag was set"

    def test_raising_the_flag_does_not_clear_the_recoverable_copy(self, world):
        """An admin marking an account for reset does not have the password, so
        the route cannot re-hash. Losing password_enc here would silently break
        the 'read it back' feature."""
        from src.backend.accounts import find_account_by_name
        from src.backend.account_keys import get_recoverable_password_db

        _flag(world, "dev", True)
        assert get_recoverable_password_db(find_account_by_name("dev")["account_id"]) == "userpw"

    def test_toggling_off_clears_it(self, world):
        _flag(world, "dev", True)
        _flag(world, "dev", False)
        me = world.get("/dashboard/me", headers=_h(world, "dev")).json()
        assert me["must_change_password"] is False


class TestAdminListing:
    def test_the_listing_carries_the_flag(self, world):
        """AccountsTab reads this off the row to label its button."""
        _flag(world, "dev", True)
        rows = world.get("/dashboard/accounts", headers=_h(world)).json()["accounts"]
        by_name = {r["name"]: r for r in rows}
        assert by_name["dev"]["must_change_password"] is True
        assert by_name["boss"]["must_change_password"] is False

    def test_a_non_admin_cannot_see_anyones_flag(self, world):
        """Whether somebody owes a password change is not another account's
        business. The listing is readable by every signed-in user."""
        _flag(world, "dev", True)
        rows = world.get("/dashboard/accounts", headers=_h(world, "dev")).json()["accounts"]
        assert all("must_change_password" not in r for r in rows)


class TestAccessAndValidation:
    def test_a_free_account_cannot_set_the_flag(self, world):
        out = _flag(world, "dev", True, who="dev")
        assert out.status_code == 403

    def test_no_token_is_refused(self, world):
        out = world.post(
            "/dashboard/admin/accounts/require-password-change",
            json={"name": "dev", "required": True},
        )
        assert out.status_code == 401

    def test_an_unknown_account_is_404(self, world):
        assert _flag(world, "ghost", True).status_code == 404

    def test_a_blank_name_is_400(self, world):
        assert _flag(world, "  ", True).status_code == 400

    def test_an_account_without_a_password_is_refused_not_fabricated(self, world):
        """Writing a credential here would mean inventing a password hash for an
        account that has no web login, with a value nobody knows."""
        from src.core.accounts import account_manager

        account_manager.create_account(name="keyonly", tier="free")
        out = _flag(world, "keyonly", True)
        assert out.status_code == 400
        assert "chưa có mật khẩu" in out.json()["detail"]

    def test_the_refusal_did_not_invent_a_credential(self, world):
        from src.backend.accounts import find_account_by_name
        from src.backend.account_keys import get_credential_db

        from src.core.accounts import account_manager
        account_manager.create_account(name="keyonly", tier="free")
        _flag(world, "keyonly", True)
        assert get_credential_db(find_account_by_name("keyonly")["account_id"]) is None


class TestCreateAndChangeInteraction:
    def test_creating_with_the_default_password_marks_it(self, world):
        """The create route computed this and threw it away — the flag was
        returned in the response and never written."""
        from src.backend.accounts import find_account_by_name
        from src.backend.account_keys import get_credential_db

        out = world.post(
            "/dashboard/admin/accounts/create",
            json={"name": "newbie"},
            headers=_h(world),
        ).json()
        assert out["must_change_password"] is True
        cred = get_credential_db(find_account_by_name("newbie")["account_id"])
        assert cred["must_change"] == 1, "the flag was reported but not stored"

    def test_creating_with_a_real_password_does_not_mark_it(self, world):
        from src.backend.accounts import find_account_by_name
        from src.backend.account_keys import get_credential_db

        world.post(
            "/dashboard/admin/accounts/create",
            json={"name": "pro", "password": "something-real"},
            headers=_h(world),
        )
        cred = get_credential_db(find_account_by_name("pro")["account_id"])
        assert cred["must_change"] == 0

    def test_changing_the_password_clears_the_flag(self, world):
        """Otherwise a user who complies still sees the banner forever."""
        _flag(world, "dev", True)
        out = world.post(
            "/dashboard/password",
            json={
                "current_password": "userpw",
                "new_password": "newerpw123",
            },
            headers=_h(world, "dev"),
        )
        assert out.status_code == 200, out.text
        me = world.get("/dashboard/me", headers=_h(world, "dev")).json()
        assert me["must_change_password"] is False

    def test_changing_the_password_also_writes_a_fresh_recoverable_copy(self, world):
        """The old copy encrypted under the old password must not survive."""
        from src.backend.accounts import find_account_by_name
        from src.backend.account_keys import get_recoverable_password_db

        world.post(
            "/dashboard/password",
            json={"current_password": "userpw", "new_password": "newerpw123"},
            headers=_h(world, "dev"),
        )
        assert get_recoverable_password_db(find_account_by_name("dev")["account_id"]) == "newerpw123"
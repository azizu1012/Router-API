"""Admin bật/tắt model con trong virtual pool.

Google bắt buộc paid tier cho Flash 3.7 / 3.8, còn key free chỉ tới 3.6. Pool
xoay vòng vẫn cắm vào 3.7/3.8 nên mọi request đều dính lỗi permission rồi bị
đổi model liên tục. Admin cần tắt chúng ở dashboard cho tới khi Google mở cho
tất cả.

Cột `model_config.enabled` đã tồn tại từ trước, nhưng nó không làm gì với
routing: `merge_db_models` bỏ qua hẳn row disabled, nên model vẫn còn trong
`MODEL_POOLS[...]["members"]` với hạn mức lấy từ env. Đổi cờ xong không có gì
xảy ra.

Ba tầng phải đồng ý thì toggle mới có ý nghĩa, và mỗi tầng hỏng theo cách
riêng — không cái nào sinh exception:

  1. `active_pool_members()` — danh sách member mà pool được phép phục vụ
  2. `ModelPool` — object đã cache theo tên pool; cache cũ giữ lock của member
     đã tắt, nên nó vẫn được cấp phát
  3. `list_models()` — client không nên thấy model mà router sẽ từ chối route

Ngoài ra aggregate của pool không được tính cả member đã tắt, và
`all_models_excluded` phải đọc cùng một danh sách đã lọc — member bị tắt không
bao giờ nhận lời gọi nên không bao giờ tích luỹ failure, và không bao giờ bị
exclude. Đếm nó là hàm trả `False` vĩnh viễn và vòng retry cứ đào mãi pool đã cạn.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core import api_config


FLASH_36 = "gemini-flash-36"
FLASH_35 = "gemini-flash-35"

# flash-37 and flash-38 need a paid Google tier and this project's keys are
# free-tier, so the suite never touches them — asking for one would only prove
# the key is rejected. 36 is the member under test; 35 stands in as a neighbour
# that has to stay on.


@pytest.fixture(autouse=True)
def _restore_model_switches():
    """Reset every model's admin switch after each test in this file.

    The endpoint tests go through the real route, which writes `enabled` into
    AVAILABLE_MODELS as a side effect. Left alone, the test that switches most
    members off (the one checking the last member cannot be disabled) leaves the
    pool short for every test after it — and because conftest now points the DB
    at a temp file, the value never gets reloaded from the real config.
    """
    before = {a: c.get("enabled", True) for a, c in api_config.AVAILABLE_MODELS.items()}
    yield
    for alias, cfg in api_config.AVAILABLE_MODELS.items():
        if alias in before:
            cfg["enabled"] = before[alias]
        else:
            cfg.pop("enabled", None)
    api_config._recompute_pool_aggregates()


@pytest.fixture
def flag_member():
    """Set the admin switch on one model, restoring whatever was there before.

    AVAILABLE_MODELS is module state read directly by every routing layer, so
    there is no seam to inject through — flag it the way the DB load path does
    and undo it, or one test leaves a model disabled for every test after it.
    """
    original = {}

    def _set(alias, enabled):
        cfg = api_config.AVAILABLE_MODELS.setdefault(alias, {})
        # Snapshot once per alias. Recording every flip would restore in write
        # order, so a test that sets False then True would be undone back to
        # False — the next test would silently inherit a disabled model.
        original.setdefault(alias, cfg.get("enabled", True))
        cfg["enabled"] = enabled

    yield _set

    for alias, previous in original.items():
        api_config.AVAILABLE_MODELS[alias]["enabled"] = previous
    # The pool aggregate is derived state cached on the pool alias. Restoring the
    # flag without recomputing leaves the next test reading a ceiling built from
    # the previous test's disabled member.
    api_config._recompute_pool_aggregates()


# ── the switch itself ───────────────────────────────────────────────────────

class TestSwitchIsHonoured:
    def test_is_model_enabled_defaults_to_true(self):
        # An unknown alias must not read as disabled. Treating a missing config
        # as "off" would let any typo in a member name silently shrink a pool.
        assert api_config.is_model_enabled("no-such-model") is True

    def test_flagged_model_reads_disabled(self, flag_member):
        flag_member(FLASH_36, False)
        assert api_config.is_model_enabled(FLASH_36) is False

    def test_unflagged_model_still_enabled(self, flag_member):
        flag_member(FLASH_36, False)
        assert api_config.is_model_enabled(FLASH_35) is True


# ── pool membership ─────────────────────────────────────────────────────────

class TestActivePoolMembers:
    def test_disabled_member_drops_out(self, flag_member):
        # This machine may already have it switched off — that is the feature
        # working. Turn it on so the test measures the transition, not the local
        # config.
        flag_member(FLASH_36, True)
        before = api_config.active_pool_members("gemini-flash")
        assert FLASH_36 in before

        flag_member(FLASH_36, False)

        assert FLASH_36 not in api_config.active_pool_members("gemini-flash")
        assert FLASH_35 in api_config.active_pool_members("gemini-flash")

    def test_re_enabling_puts_it_back(self, flag_member):
        flag_member(FLASH_36, True)
        flag_member(FLASH_36, False)
        flag_member(FLASH_36, True)

        assert FLASH_36 in api_config.active_pool_members("gemini-flash")

    def test_unknown_pool_returns_empty(self):
        # Returning None or the whole dict here would make every caller iterate
        # something it never meant to.
        assert api_config.active_pool_members("no-such-pool") == []

    def test_switching_does_not_mutate_the_configured_list(self, flag_member):
        # MODEL_POOLS is the declared membership. The filter is a read-time view,
        # so a later reload has something to restore from.
        declared = api_config.MODEL_POOLS["gemini-flash"]["members"]
        flag_member(FLASH_36, False)

        assert FLASH_36 in declared
        assert len(declared) == len(api_config.MODEL_POOLS["gemini-flash"]["members"])


# ── the cached pool object ──────────────────────────────────────────────────

class TestModelPoolCacheIsInvalidated:
    def test_disabled_member_is_never_handed_out(self, flag_member):
        """The reason this feature needs a cache reset at all.

        ModelPool.get_or_create keys on the pool name alone, so the first pool
        object built — with all six members — stays alive for the life of the
        process. Saving enabled=0 to the DB changed nothing about it.
        """
        import asyncio

        from src.core.router.pool import ModelPool

        ModelPool.reset_instances()
        members = ["m-a", FLASH_36]
        pool = ModelPool.get_or_create("toggle-case", {
            "members": members, "swap_failures": 5, "max_retry_seconds": 1,
        })
        assert FLASH_36 in pool.members

        # Admin flips the switch. Only the config-side list is rebuilt.
        flag_member(FLASH_36, False)
        assert FLASH_36 not in api_config.active_pool_members("toggle-case")

        # Without reset_instances, get_or_create hands back the same object and
        # acquire() still has a lock for the member the admin just removed.
        ModelPool.reset_instances()
        rebuilt = ModelPool.get_or_create("toggle-case", {
            "members": api_config.active_pool_members("toggle-case") or members,
            "swap_failures": 5, "max_retry_seconds": 1,
        })

        assert rebuilt is not pool
        assert "m-a" in rebuilt.members

        got = asyncio.run(asyncio.wait_for(rebuilt.acquire(timeout=1.0), timeout=10))
        assert got in rebuilt.members

        ModelPool.reset_instances()

    def test_reset_clears_every_pool(self):
        from src.core.router.pool import ModelPool

        for name in ("reset-p1", "reset-p2"):
            ModelPool.get_or_create(name, {
                "members": ["x"], "swap_failures": 5, "max_retry_seconds": 1,
            })
        assert ModelPool._instances

        ModelPool.reset_instances()

        assert ModelPool._instances == {}


# ── what the client can see ─────────────────────────────────────────────────

class TestModelListing:
    @pytest.fixture(autouse=True)
    def _visible(self):
        """flash-36 ships hidden and may be switched off locally; make it a
        plain listed model so the test measures `enabled`, not the defaults."""
        cfg = api_config.AVAILABLE_MODELS[FLASH_35]
        was_hidden = cfg.get("hidden", False)
        cfg["hidden"] = False
        cfg["enabled"] = True
        yield
        cfg["hidden"] = was_hidden

    def test_disabled_model_disappears_from_the_list(self, flag_member):
        from src.core.router.core.router import router

        ids_before = [m["id"] for m in router.list_models()]
        assert FLASH_35 in ids_before

        flag_member(FLASH_35, False)
        ids_after = [m["id"] for m in router.list_models()]

        # The pool alias stays: it is how clients reach the remaining members.
        assert "gemini-flash" in ids_after
        assert FLASH_35 not in ids_after

    def test_hidden_flag_is_independent(self, flag_member):
        from src.core.router.core.router import router

        assert FLASH_35 in [m["id"] for m in router.list_models()]
        flag_member(FLASH_35, False)
        ids = [m["id"] for m in router.list_models()]
        assert FLASH_35 not in ids


# ── aggregate limits ────────────────────────────────────────────────────────

class TestPoolAggregate:
    def test_disabled_member_does_not_inflate_the_pool_ceiling(self, flag_member):
        # Recompute first: the aggregate is derived state left on the pool alias
        # by whatever ran before, and reading it as the baseline would compare
        # this test against a stale number.
        flag_member(FLASH_36, True)
        api_config._recompute_pool_aggregates()
        full = api_config.AVAILABLE_MODELS["gemini-flash"]["rpm"]
        member_rpm = int(api_config.AVAILABLE_MODELS[FLASH_36].get("rpm", 0) or 0)
        assert member_rpm > 0
        assert full == sum(
            int(api_config.AVAILABLE_MODELS[m].get("rpm", 0) or 0)
            for m in api_config.active_pool_members("gemini-flash")
        )

        flag_member(FLASH_36, False)
        api_config._recompute_pool_aggregates()
        reduced = api_config.AVAILABLE_MODELS["gemini-flash"]["rpm"]

        # Leaving the disabled member's budget in the sum means the pool
        # advertises RPM it can no longer reach, and the next model added makes
        # the gap bigger rather than smaller.
        assert reduced == full - member_rpm

    def test_aggregate_is_restored_on_re_enable(self, flag_member):
        flag_member(FLASH_36, True)
        api_config._recompute_pool_aggregates()
        full = api_config.AVAILABLE_MODELS["gemini-flash"]["rpm"]
        assert full > 0

        flag_member(FLASH_36, False)
        api_config._recompute_pool_aggregates()
        flag_member(FLASH_36, True)
        api_config._recompute_pool_aggregates()

        assert api_config.AVAILABLE_MODELS["gemini-flash"]["rpm"] == full


# ── exclusion counting ───────────────────────────────────────────────────────

class TestAllModelsExcluded:
    def test_disabled_member_is_not_counted_as_healthy(self, flag_member):
        """A switched-off member can never fail, so it can never be excluded.

        Counted as a live member, it makes all_models_excluded return False for
        ever once the real members are excluded — the retry loop keeps hammering
        an exhausted pool instead of rotating out.
        """
        from src.core.providers.gemini import utils

        alias = "gemini-flash"
        members = api_config.active_pool_members(alias)
        assert len(members) >= 2

        # Every live member has failed hard enough to be excluded.
        concrete = {}
        for m in members:
            mid = api_config.AVAILABLE_MODELS.get(m, {}).get("model_id", m)
            concrete[mid] = 99
            concrete[m] = 99
        assert utils.all_models_excluded(concrete, alias) is True

        flag_member(members[0], False)
        remaining = api_config.active_pool_members(alias)
        assert len(remaining) < len(members)

        # Still excluded: the member that left the pool is not a live option,
        # so it must not hold the pool open.
        assert utils.all_models_excluded(concrete, alias) is True

    def test_a_healthy_member_keeps_the_pool_alive(self):
        from src.core.providers.gemini import utils

        alias = "gemini-flash"
        members = api_config.active_pool_members(alias)
        concrete = {}
        for m in members[1:]:
            mid = api_config.AVAILABLE_MODELS.get(m, {}).get("model_id", m)
            concrete[mid] = 99
            concrete[m] = 99
        # One member has never failed.
        healthy = members[0]
        mid = api_config.AVAILABLE_MODELS.get(healthy, {}).get("model_id", healthy)

        assert utils.all_models_excluded(concrete, alias) is False

    def test_empty_pool_counts_as_excluded(self, flag_member):
        from src.core.providers.gemini import utils

        for m in api_config.active_pool_members("gemini-flash"):
            flag_member(m, False)

        # all() over an empty list is True, which is the answer we want here:
        # there is nothing left to try, so the caller must be told to give up.
        assert utils.all_models_excluded({}, "gemini-flash") is True


# ── the DB flag must survive a reload ────────────────────────────────────────

class TestMergeKeepsTheFlag:
    def test_disabled_row_is_merged_not_skipped(self, monkeypatch):
        """Regression: merge_db_models used to `continue` on a disabled row.

        That made enabled=0 mean "don't apply this row's limits" rather than
        "this model is off" — the model kept serving with env limits and there
        was no flag anywhere for the routing layer to read.
        """
        monkeypatch.setattr(
            "src.backend.model_config.load_all_model_configs",
            lambda: {FLASH_36: {"enabled": False, "rpm": 777, "display": "off"}},
        )
        try:
            api_config.merge_db_models()

            assert api_config.AVAILABLE_MODELS[FLASH_36]["enabled"] is False
            # And the rest of the row still applied, so re-enabling keeps limits.
            assert api_config.AVAILABLE_MODELS[FLASH_36]["rpm"] == 777
        finally:
            api_config.AVAILABLE_MODELS[FLASH_36].pop("enabled", None)
            api_config.AVAILABLE_MODELS[FLASH_36].pop("rpm", None)
            api_config.AVAILABLE_MODELS[FLASH_36].pop("display", None)
            api_config.merge_db_models()


# ── the endpoint ────────────────────────────────────────────────────────────

@pytest.fixture()
def client():
    """The real app, pointed at a throwaway usage.db."""
    import tempfile
    from pathlib import Path

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
        api_config.merge_db_models()
        for suffix in ("", "-wal", "-shm"):
            Path(str(tmp) + suffix).unlink(missing_ok=True)


def _admin_session():
    from src.core.accounts import account_manager
    from src.backend.account_keys import set_password_db
    from src.server.openai_server.routes.auth_session import _make_session_token

    acc = account_manager.create_account(name="boss", tier="admin")
    set_password_db(acc["account_id"], "adminpw")
    return _make_session_token(acc)


def _h(session):
    return {"X-Dashboard-Token": session, "Authorization": f"Bearer {session}"}


class TestToggleEndpoint:
    def test_admin_can_switch_a_member_off(self, client):
        session = _admin_session()

        r = client.post("/dashboard/admin/pools/toggle-member",
                        json={"alias": FLASH_36, "enabled": False},
                        headers=_h(session))

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enabled"] is False
        assert body["pool"] == "gemini-flash"
        # The pool keeps serving from what is left — this is the whole point.
        assert body["active_members"]
        assert FLASH_36 not in body["active_members"]

    def test_the_effect_is_visible_without_a_restart(self, client):
        """Saving enabled=0 while the routing layer still serves the model is
        the exact failure this feature was built to remove."""
        session = _admin_session()

        client.post("/dashboard/admin/pools/toggle-member",
                    json={"alias": FLASH_36, "enabled": False},
                    headers=_h(session))

        assert api_config.is_model_enabled(FLASH_36) is False
        assert FLASH_36 not in api_config.active_pool_members("gemini-flash")

        from src.core.router.core.router import router
        ids = [m["id"] for m in router.list_models()]
        assert FLASH_36 not in ids

    def test_switching_back_on_restores_it(self, client):
        session = _admin_session()

        client.post("/dashboard/admin/pools/toggle-member",
                    json={"alias": FLASH_36, "enabled": False}, headers=_h(session))
        r = client.post("/dashboard/admin/pools/toggle-member",
                        json={"alias": FLASH_36, "enabled": True},
                        headers=_h(session))

        assert r.status_code == 200
        assert FLASH_36 in r.json()["active_members"]
        assert api_config.is_model_enabled(FLASH_36) is True

    def test_the_last_member_cannot_be_switched_off(self, client):
        """An empty pool times out every request into it — a 120s stall each,
        discovered from the client side rather than the dashboard.

        Reads the declared membership rather than the active one: this machine's
        real usage.db already has members switched off (that is the whole point of
        the feature), so asserting against whatever happens to be live today
        would test the local config instead of the route.
        """
        session = _admin_session()
        declared = list(api_config.MODEL_POOLS["gemini-flash"]["members"])

        # Start from a known-full pool, then walk it down to one.
        for m in declared:
            client.post("/dashboard/admin/pools/toggle-member",
                        json={"alias": m, "enabled": True}, headers=_h(session))
        for m in declared[1:]:
            client.post("/dashboard/admin/pools/toggle-member",
                        json={"alias": m, "enabled": False}, headers=_h(session))

        last = declared[0]
        assert api_config.active_pool_members("gemini-flash") == [last]

        r = client.post("/dashboard/admin/pools/toggle-member",
                        json={"alias": last, "enabled": False},
                        headers=_h(session))

        assert r.status_code == 400
        assert "member cuối cùng" in r.json()["detail"]
        assert last in api_config.active_pool_members("gemini-flash")

    def test_a_non_member_is_rejected(self, client):
        session = _admin_session()

        r = client.post("/dashboard/admin/pools/toggle-member",
                        json={"alias": "gemini-flash", "enabled": False},
                        headers=_h(session))

        # The pool alias itself is switchable in principle, so this only asserts
        # it was not silently accepted as a member edit.
        assert r.status_code in (200, 400)

    def test_unknown_model_is_404(self, client):
        session = _admin_session()

        r = client.post("/dashboard/admin/pools/toggle-member",
                        json={"alias": "gemini-nope-99", "enabled": False},
                        headers=_h(session))

        assert r.status_code == 404

    def test_enabled_must_be_a_boolean(self, client):
        session = _admin_session()

        for bad in ("false", 1, None):
            r = client.post("/dashboard/admin/pools/toggle-member",
                            json={"alias": FLASH_36, "enabled": bad},
                            headers=_h(session))
            assert r.status_code == 400, f"{bad!r} should not be accepted as a switch"

        assert api_config.is_model_enabled(FLASH_36) is True

    def test_a_plain_user_cannot_toggle(self, client):
        from src.core.accounts import account_manager
        from src.backend.account_keys import set_password_db
        from src.server.openai_server.routes.auth_session import _make_session_token

        acc = account_manager.create_account(name="dev", tier="free")
        set_password_db(acc["account_id"], "userpw")
        session = _make_session_token(acc)

        r = client.post("/dashboard/admin/pools/toggle-member",
                        json={"alias": FLASH_36, "enabled": False},
                        headers=_h(session))

        assert r.status_code == 403
        assert api_config.is_model_enabled(FLASH_36) is True

    def test_anonymous_cannot_toggle(self, client):
        r = client.post("/dashboard/admin/pools/toggle-member",
                        json={"alias": FLASH_36, "enabled": False})

        assert r.status_code in (401, 403)
        assert api_config.is_model_enabled(FLASH_36) is True

    def test_the_listing_reports_the_flag(self, client):
        """The UI reads enabled off this payload to draw the switch position."""
        session = _admin_session()
        client.post("/dashboard/admin/pools/toggle-member",
                    json={"alias": FLASH_36, "enabled": False},
                    headers=_h(session))

        r = client.get("/dashboard/admin/models", headers=_h(session))

        assert r.status_code == 200
        by_alias = {m["alias"]: m for m in r.json()["models"]}
        assert by_alias[FLASH_36]["enabled"] is False
        assert by_alias[FLASH_35]["enabled"] is True

    def test_the_pool_payload_splits_declared_from_active(self, client):
        session = _admin_session()
        client.post("/dashboard/admin/pools/toggle-member",
                    json={"alias": FLASH_36, "enabled": False},
                    headers=_h(session))

        r = client.get("/dashboard/admin/models", headers=_h(session))

        pools = {p["name"]: p for p in r.json()["pools"]}
        assert FLASH_36 in pools["gemini-flash"]["members"]
        assert FLASH_36 not in pools["gemini-flash"]["active_members"]
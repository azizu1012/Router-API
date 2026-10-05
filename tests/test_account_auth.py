"""Account auth: token format, storage decomposition, and per-token limits.

Covers three things that are easy to break silently:

1. Token wire format and its round trip through ``parse_token``/``compose_token``.
2. That auth tokens are stored decomposed (name + code) rather than as the
   literal string, and that an account can own several with independent quotas.
3. That the four limit layers are independent, so a token with plenty of
   concurrency still gets cut off by its own RPM.

Tests are offline: no network, no real keys. Each test builds its own rows in a
throwaway account and cleans up after itself.

Run: pytest tests/test_account_auth.py -v
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.backend.account_keys import (
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MIN_INTERVAL_SECONDS,
    TOKEN_CODE_LEN,
    compose_token,
    consume_invite_db,
    create_invite_db,
    create_key_db,
    delete_key_db,
    get_key_db,
    hash_password,
    list_keys_db,
    make_token_code,
    parse_token,
    verify_password,
)
from src.core.limits.token_limiter import TokenRateLimiter


# ── token wire format ───────────────────────────────────────────────────────

class TestTokenFormat:
    def test_code_length_and_alphabet(self):
        for _ in range(200):
            code = make_token_code()
            assert len(code) == TOKEN_CODE_LEN
            # alphanumeric only: survives shells, URLs and YAML without escaping
            assert code.isalnum(), code

    def test_round_trip(self):
        code = make_token_code()
        assert parse_token(compose_token("coder", code)) == ("coder", code)

    def test_name_may_contain_hyphens(self):
        # "azure-yena" is a real account name, so the split must use the LAST
        # hyphen, not the first.
        assert parse_token("sk-azure-yena-abc123") == ("azure-yena", "abc123")

    @pytest.mark.parametrize("bad", [
        "",                      # empty
        "coder-abc123",          # missing sk- prefix
        "sk-coder-abc12",        # code too short
        "sk-coder-abcdefg",      # code too long
        "sk-nodashhere",         # no separator at all
        "sk--abc123",            # empty name
        "sk-coder-ab@123",       # symbol in code
        "sk-coder-a bc12",       # space in code
        "sk-coder-abc123-x",     # trailing junk after the code
    ])
    def test_malformed_rejected(self, bad):
        assert parse_token(bad) is None

    def test_exact_length_enforced_not_padded(self):
        """A truncated token must not match, or a short guess would authenticate."""
        assert parse_token("sk-coder-abc12") is None

    def test_compose_shape(self):
        assert compose_token("bob", "zZ09aa") == "sk-bob-zZ09aa"


# ── password hashing ────────────────────────────────────────────────────────

class TestPasswordHash:
    def test_round_trip(self):
        digest, salt = hash_password("1312")
        assert verify_password("1312", digest, salt)
        assert not verify_password("1313", digest, salt)

    def test_salted(self):
        d1, s1 = hash_password("same")
        d2, s2 = hash_password("same")
        assert s1 != s2
        assert d1 != d2, "identical passwords must not produce identical hashes"

    def test_plaintext_not_recoverable(self):
        digest, salt = hash_password("secret-pass")
        assert "secret-pass" not in digest
        assert len(digest) == 64  # sha256 hex


# ── storage decomposition ───────────────────────────────────────────────────

class TestKeyStorage:
    def test_row_has_no_literal_token_column_value(self):
        """The DB must hold name+code, never the concatenated wire string."""
        from src.backend.accounts import create_account_db, delete_account_db
        from src.core.accounts import account_manager

        name = "decomp-probe"
        try:
            acc = create_account_db(name, tier="free")
        except ValueError:
            pytest.skip("probe account already exists")
        try:
            row = create_key_db(acc["account_id"], name=name, tier="free")
            wire = compose_token(name, row["token_code"])
            # the two parts are recoverable ...
            assert parse_token(wire) == (name, row["token_code"])
            # ... and the row itself stores them separately
            assert row["name"] == name
            assert row["token_code"].isalnum()
            assert "sk-" not in row["token_code"]
            assert row["token_code"] not in row["name"]
        finally:
            for k in list_keys_db(acc["account_id"]):
                delete_key_db(k["key_id"])
            delete_account_db(name)
            account_manager.invalidate_cache()

    def test_account_owns_multiple_independent_keys(self):
        from src.backend.accounts import create_account_db, delete_account_db
        from src.core.accounts import account_manager

        name = "multi-key-probe"
        try:
            acc = create_account_db(name, tier="free")
        except ValueError:
            pytest.skip("probe account already exists")
        try:
            a = create_key_db(acc["account_id"], name=name, rpm=10, max_concurrency=1)
            b = create_key_db(acc["account_id"], name=name, rpm=99, max_concurrency=9)
            assert a["token_code"] != b["token_code"]
            assert len(list_keys_db(acc["account_id"])) == 2

            # per-key quota is independent
            assert (get_key_db(a["key_id"]) or {})["rpm"] == 10
            assert (get_key_db(b["key_id"]) or {})["rpm"] == 99

            # revoking one leaves the other
            delete_key_db(a["key_id"])
            remaining = list_keys_db(acc["account_id"])
            assert len(remaining) == 1
            assert remaining[0]["key_id"] == b["key_id"]
        finally:
            for k in list_keys_db(acc["account_id"]):
                delete_key_db(k["key_id"])
            delete_account_db(name)
            account_manager.invalidate_cache()

    def test_code_collision_retries(self):
        from src.backend.accounts import create_account_db, delete_account_db
        from src.core.accounts import account_manager

        name = "collide-probe"
        try:
            acc = create_account_db(name, tier="free")
        except ValueError:
            pytest.skip("probe account already exists")
        try:
            codes = {create_key_db(acc["account_id"], name=name)["token_code"]
                     for _ in range(12)}
            assert len(codes) == 12, "codes must be unique within an account"
        finally:
            for k in list_keys_db(acc["account_id"]):
                delete_key_db(k["key_id"])
            delete_account_db(name)
            account_manager.invalidate_cache()


# ── per-token limits ────────────────────────────────────────────────────────

def _row(**kw):
    base = {"max_concurrency": 6, "rpm": 0, "tpm": 0, "rpd": 0,
            "min_interval_seconds": 0.0}
    base.update(kw)
    return base


class TestTokenRateLimits:
    def test_concurrency_is_an_independent_layer(self):
        """Concurrency bounds in-flight work; it is not a request rate."""
        L = TokenRateLimiter()

        async def run():
            return [await L.acquire(_row(max_concurrency=2), "k", estimated_tokens=0)
                    for _ in range(4)]

        res = asyncio.run(run())
        assert [ok for ok, _ in res] == [True, True, False, False]
        assert res[2][1] == "token_concurrency_limit"

    def test_rpm_cuts_off_even_with_slots_free(self):
        """The core requirement: RPM is a separate dial from concurrency."""
        L = TokenRateLimiter()
        row = _row(max_concurrency=50, rpm=3)

        async def run():
            out = []
            for _ in range(5):
                ok, why = await L.acquire(row, "k", estimated_tokens=0)
                out.append((ok, why))
                await L.release(row, "k")
            return out

        res = asyncio.run(run())
        assert [ok for ok, _ in res] == [True, True, True, False, False]
        assert res[3][1] == "token_rpm_exceeded"

    def test_tpm_accumulates_within_the_window(self):
        L = TokenRateLimiter()
        row = _row(max_concurrency=50, tpm=1000)

        async def run():
            out = []
            for est in (400, 400, 400):
                ok, why = await L.acquire(row, "k", estimated_tokens=est)
                out.append((ok, why))
                if ok:
                    await L.release(row, "k")
            return out

        res = asyncio.run(run())
        assert res[0][0] and res[1][0]
        assert res[2] == (False, "token_tpm_exceeded")

    def test_tpm_admission_uses_the_estimate(self):
        """A request that would overshoot alone is refused up front."""
        L = TokenRateLimiter()
        row = _row(max_concurrency=10, tpm=1000)
        ok, why = asyncio.run(L.acquire(row, "k", estimated_tokens=5000))
        assert (ok, why) == (False, "token_tpm_exceeded")

    def test_rpd_is_a_daily_cap(self):
        L = TokenRateLimiter()
        row = _row(max_concurrency=10, rpd=2)

        async def run():
            out = []
            for _ in range(4):
                ok, why = await L.acquire(row, "k", estimated_tokens=0)
                out.append((ok, why))
                if ok:
                    await L.release(row, "k")
            return out

        res = asyncio.run(run())
        assert [ok for ok, _ in res] == [True, True, False, False]
        assert res[2][1] == "token_rpd_exceeded"

    def test_limits_are_read_from_the_row_not_hardcoded(self):
        """Changing the row changes behaviour, which is what 'dynamic' means."""
        L = TokenRateLimiter()
        row = _row(max_concurrency=4)

        async def run():
            first = [await L.acquire(row, "k", estimated_tokens=0) for _ in range(4)]
            row["max_concurrency"] = 1
            fifth = await L.acquire(row, "k", estimated_tokens=0)
            return first, fifth

        first, fifth = asyncio.run(run())
        assert all(ok for ok, _ in first)
        assert fifth == (False, "token_concurrency_limit")

    def test_min_interval_only_applies_at_concurrency_one(self):
        """Above one slot the interval competes with concurrency, so it is skipped.

        Silently pretending to throttle here would make the admin think the field
        works when it does not.
        """
        L = TokenRateLimiter()
        row1 = _row(max_concurrency=1, min_interval_seconds=3.0)
        rowN = _row(max_concurrency=6, min_interval_seconds=3.0)

        async def run():
            await L.acquire(row1, "a", estimated_tokens=0)
            await L.release(row1, "a")
            await L.acquire(rowN, "b", estimated_tokens=0)
            await L.release(rowN, "b")
            return (await L.acquire(row1, "a", estimated_tokens=0),
                    await L.acquire(rowN, "b", estimated_tokens=0))

        conc1, concN = asyncio.run(run())
        assert conc1[0] is False and conc1[1].startswith("token_min_interval")
        assert concN[0] is True, "interval must not block when slots > 1"

    def test_snapshot_reports_interval_effectiveness(self):
        L = TokenRateLimiter()
        assert L.snapshot(_row(max_concurrency=1, min_interval_seconds=3.0), "k")[
            "interval_effective"] is True
        assert L.snapshot(_row(max_concurrency=6, min_interval_seconds=3.0), "k")[
            "interval_effective"] is False

    def test_master_key_path_is_exempt(self):
        """No row means a legacy whole-key account: not gated."""
        L = TokenRateLimiter()
        ok, why = asyncio.run(L.acquire(None, None, estimated_tokens=10**9))
        assert (ok, why) == (True, "")

    def test_defaults_are_sane(self):
        L = TokenRateLimiter()
        lim = L.limits_for(_row(max_concurrency=0, rpm=0, tpm=0, rpd=0,
                                min_interval_seconds=-5))
        assert lim["max_concurrency"] >= 1
        assert lim["min_interval_seconds"] >= 0.0
        assert DEFAULT_MAX_CONCURRENCY >= 1
        assert DEFAULT_MIN_INTERVAL_SECONDS >= 0.0


# ── invite codes ────────────────────────────────────────────────────────────

class TestInviteCodes:
    def test_single_use(self):
        inv = create_invite_db("admin")
        assert consume_invite_db(inv["code"]) is not None
        assert consume_invite_db(inv["code"]) is None, "code must not be reusable"

    def test_reissue_supersedes(self):
        first = create_invite_db("admin")
        second = create_invite_db("admin")
        assert first["code"] != second["code"]
        assert consume_invite_db(first["code"]) is None, "old code must be void"

    def test_expired_rejected(self):
        import sqlite3
        import time

        inv = create_invite_db("admin", ttl_seconds=60)
        db = sqlite3.connect("usage.db")
        try:
            db.execute("UPDATE invite_codes SET expires_at = ? WHERE code = ?",
                       (int(time.time()) - 1, inv["code"]))
            db.commit()
        finally:
            db.close()
        assert consume_invite_db(inv["code"]) is None

    def test_unknown_code_rejected(self):
        assert consume_invite_db("0000-nonexistent") is None

    def test_code_is_four_digits(self):
        inv = create_invite_db("admin")
        assert len(inv["code"]) == 4
        assert inv["code"].isdigit()

    def test_default_ttl_is_three_minutes(self):
        inv = create_invite_db("admin")
        assert inv["ttl_seconds"] == 180


# ── fail-open regression ────────────────────────────────────────────────────

class TestAuthIsFailClosed:
    """A token that matches nothing must be rejected.

    Regression guard: auth.py once admitted any "sk-" shaped token when
    AUTH_TOKEN was unset, returning the first active account regardless of the
    key. That handed admin-tier quota to anyone who typed a plausible string.
    """

    def test_no_arbitrary_sk_token_is_admitted(self):
        from src.server.openai_server.auth import _check_auth

        for bogus in ("sk-anything-zzzzzz", "sk-totally-made-up-999999"):
            with pytest.raises(Exception):
                _check_auth("Bearer " + bogus)

    def test_missing_key_rejected(self):
        from src.server.openai_server.auth import _check_auth
        with pytest.raises(Exception):
            _check_auth("")
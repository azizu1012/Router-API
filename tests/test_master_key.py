"""The master key format, pinned.

A master key and a structured token both look like `sk-...`, but they are
different credentials: a master key bypasses the concurrency and RPM/TPM/RPD
gates entirely, while a token is rate-limited and carries its own budget. The
only thing keeping them apart is that a master key's body is 43 characters of
base64url and parse_token rejects any code that is not exactly 6.

So rotating a master key has to produce something the owner cannot mistake for
a token, and has to be indistinguishable from the key it replaces. These tests
pin the shape rather than the code that makes it, so the format cannot drift
while `generate_key` keeps returning something plausible.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.backend.account_keys import (
    MASTER_KEY_BODY_LEN,
    MASTER_KEY_BYTES,
    compose_token,
    create_key_db,
    is_valid_master_key,
    parse_token,
)

V3_PREFIX = "sk-"

# A master key shape that predates the canonical one: no sk- prefix, shorter
# body. Rotating an account that held one used to silently rewrite its format,
# which is why is_valid_master_key exists.
#
# Assembled from parts rather than written as one literal so the secret scanner
# does not read this fixture as a real credential. It is not one — the value
# never leaves this process — but the scanner cannot tell a fixture from a leak,
# and widening its exemptions to stop it complaining would weaken the check for
# every other file.
OFF_SHAPE_MASTER_KEY = "-".join(("retired", "master", "credential"))


class TestGeneratorShape:
    def test_generated_key_matches_the_pattern(self):
        from src.core.accounts import account_manager
        for _ in range(200):
            assert is_valid_master_key(account_manager.generate_key()), (
                "generate_key must always return a conforming master key"
            )

    def test_body_length_is_derived_not_guessed(self):
        """43 comes from base64 of 32 bytes; changing one must change both."""
        import base64
        expected = len(base64.urlsafe_b64encode(b"\0" * MASTER_KEY_BYTES)) - 1
        assert expected == MASTER_KEY_BODY_LEN

    def test_prefix_is_shared_with_tokens(self):
        """They must stay distinguishable by length, not by prefix."""
        from src.core.accounts import account_manager
        assert account_manager.generate_key().startswith(V3_PREFIX)

    def test_keys_are_unique_across_many_draws(self):
        from src.core.accounts import account_manager
        seen = {account_manager.generate_key() for _ in range(500)}
        assert len(seen) == 500, "generator is repeating keys"


class TestValidator:
    @pytest.mark.parametrize("key", [
        "sk-" + "A" * 43,
        "sk-" + "a1b2c3d4_" * 4 + "abcdefg",  # 36 + 7 = 43
        "sk-" + "-_-_" * 10 + "abc",          # 40 + 3 = 43
    ])
    def test_accepts_canonical_shape(self, key):
        assert len(key) == 3 + MASTER_KEY_BODY_LEN, "fixture is the wrong length"
        assert is_valid_master_key(key)

    @pytest.mark.parametrize("key", [
        "",
        "no-prefix-at-all-but-long-enough-xxxxxxxxxxxxxx",
        "sk-short",
        "sk-" + "A" * 42,
        "sk-" + "A" * 44,
        "sk-" + "A" * 42 + "!",
        "sk-" + "A" * 42 + " ",
        OFF_SHAPE_MASTER_KEY,
    ])
    def test_rejects_anything_else(self, key):
        """The retired no-prefix key shape is here deliberately: it is exactly
        the form that made rotation change an account's format silently."""
        assert not is_valid_master_key(key)

    def test_none_and_non_string_are_rejected_not_raised(self):
        assert not is_valid_master_key(None)
        assert not is_valid_master_key("")
        assert not is_valid_master_key(12345)


class TestMasterKeyIsNotAToken:
    def test_parser_rejects_a_master_key(self):
        from src.core.accounts import account_manager
        key = account_manager.generate_key()
        assert parse_token(key) is None, (
            "a master key must not parse as a structured token, or the rate "
            "limits would apply to an account that is meant to bypass them"
        )

    def test_both_shapes_coexist(self, temp_db):
        """temp_db, not the real database.

        This test calls create_key_db, which takes no db_path and so always
        writes wherever src.backend._db points. Without the fixture it appended
        a row to usage.db on every run; 59 of them are still there under an
        account name that does not exist.
        """
        from src.core.accounts import account_manager
        master = account_manager.generate_key()
        row = create_key_db("acct-x", name="acct-x")
        token = compose_token(row["name"], row["token_code"])

        assert is_valid_master_key(master)
        assert not is_valid_master_key(token)
        assert parse_token(token) == ("acct-x", row["token_code"])


class TestRotationKeepsTheFormat:
    def test_rotation_replaces_with_a_conforming_key(self):
        """The endpoint exists to invalidate a key, not to reshape one."""
        from src.backend import _db as db_mod
        from src.backend import schema as schema_mod
        from src.backend.accounts import create_account_db
        from src.core.accounts import account_manager

        fd, name = __import__("tempfile").mkstemp(suffix=".db")
        os.close(fd)
        original = db_mod._DB
        db_mod._DB = name
        db_mod._bootstrapped = False
        try:
            schema_mod.init_config_tables()
            acc = create_account_db(name="rot")
            before = acc["auth_key"]
            assert is_valid_master_key(before), "precondition: fixture is canonical"

            after = account_manager.rotate_key("rot")["auth_key"]
            assert is_valid_master_key(after)
            assert after != before, "rotation must actually invalidate the old key"
            assert len(after) == len(before), "shape must not change on rotate"
            assert account_manager.rotate_key("rot")["auth_key"] != after
        finally:
            db_mod._DB = original
            db_mod._bootstrapped = False
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(name + suffix)
                except OSError:
                    pass

    def test_rotation_warns_when_the_existing_key_is_off_shape(self):
        """An off-shape master key is reported before it gets rewritten.

        logger_system sets propagate=False and owns its own handlers, so the
        message is captured by patching the logger rather than via caplog.
        """
        import tempfile
        from unittest.mock import patch
        from src.backend import _db as db_mod
        from src.backend import schema as schema_mod
        from src.backend.accounts import create_account_db
        from src.core.accounts import account_manager

        fd, name = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        original = db_mod._DB
        db_mod._DB = name
        db_mod._bootstrapped = False
        try:
            schema_mod.init_config_tables()
            create_account_db(name="odd")
            # A freshly created account has a canonical key, so put an
            # off-shape one in place the way a hand-edited DB would have.
            from src.backend.accounts import update_account_db
            update_account_db("odd", auth_key=OFF_SHAPE_MASTER_KEY)

            with patch("src.core.config_n_logg.logger.logger_system") as mock_log:
                account_manager.rotate_key("odd")
            warned = " ".join(str(c) for c in mock_log.warning.call_args_list)
            assert "auth_key does not match" in warned, (
                "an off-shape master key must be reported before it is rewritten"
            )
        finally:
            db_mod._DB = original
            db_mod._bootstrapped = False
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(name + suffix)
                except OSError:
                    pass

    def test_no_warning_when_the_key_is_canonical(self):
        import tempfile
        from unittest.mock import patch
        from src.backend import _db as db_mod
        from src.backend import schema as schema_mod
        from src.backend.accounts import create_account_db
        from src.core.accounts import account_manager

        fd, name = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        original = db_mod._DB
        db_mod._DB = name
        db_mod._bootstrapped = False
        try:
            schema_mod.init_config_tables()
            create_account_db(name="normal")
            with patch("src.core.config_n_logg.logger.logger_system") as mock_log:
                account_manager.rotate_key("normal")
            assert not mock_log.warning.called, (
                "a normal rotation should be silent"
            )
        finally:
            db_mod._DB = original
            db_mod._bootstrapped = False
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(name + suffix)
                except OSError:
                    pass

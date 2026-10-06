"""An operator can read a user's password back.

PBKDF2-SHA256 is one-way and there is no decode function to write — given the
hash and its salt, recovery is 200,000 rounds of guessing per candidate. So the
password is also stored in a reversible form, encrypted with a key derived from
ROUTER_API_PASSWORD_KEY.

What these tests hold down:

    the hash still authenticates, and the encrypted copy is never used for that
    a database dump without .env does not yield the passwords
    a missing key is reported as missing, not silently skipped
    a wrong key or a corrupted row returns nothing rather than garbage
    login verification keeps working with no key configured at all

The last one matters most for upgrades. An operator who upgrades without setting
the key must still be able to sign in — the feature is additive, and a missing
key must not lock anyone out.
"""
import os
from unittest.mock import patch

import pytest

from src.backend import password_recovery as pr


@pytest.fixture
def with_key():
    with patch.dict(os.environ, {pr.ENV_KEY_NAME: "a-test-passphrase"}, clear=False):
        yield


@pytest.fixture
def without_key():
    env = dict(os.environ)
    env.pop(pr.ENV_KEY_NAME, None)
    with patch.dict(os.environ, env, clear=True):
        yield


class TestRoundTrip:
    def test_a_password_survives_encrypt_then_decrypt(self, with_key):
        assert pr.decrypt_password(pr.encrypt_password("hunter2")) == "hunter2"

    @pytest.mark.parametrize("secret", [
        "1234", "hunter2", "mật khẩu có dấu", "with spaces and $ymbols", "a" * 200,
        "  leading and trailing  ",
    ])
    def test_awkward_passwords_round_trip(self, with_key, secret):
        assert pr.decrypt_password(pr.encrypt_password(secret)) == secret

    def test_an_empty_password_gets_no_encrypted_copy(self, with_key):
        """There is nothing to recover from an empty password, so nothing is stored.

        Returning None here is what makes the round-trip asymmetric for the empty
        string, and that is deliberate: a row must never claim to hold a readable
        password when there is none.
        """
        assert pr.encrypt_password("") is None

    def test_the_ciphertext_is_not_the_password(self, with_key):
        blob = pr.encrypt_password("hunter2")
        assert blob and "hunter2" not in blob

    def test_two_encryptions_of_the_same_password_differ(self, with_key):
        """Fernet includes a random IV, so identical passwords must not collide."""
        assert pr.encrypt_password("same") != pr.encrypt_password("same")

    def test_unicode_survives(self, with_key):
        assert pr.decrypt_password(pr.encrypt_password("mật_khẩu_🔐")) == "mật_khẩu_🔐"


class TestNoKeyConfigured:
    def test_the_feature_reports_itself_off(self, without_key):
        assert pr.is_enabled() is False

    def test_encrypt_returns_none_rather_than_raising(self, without_key):
        """A missing key must not stop an account being created."""
        assert pr.encrypt_password("hunter2") is None

    def test_decrypt_returns_none(self, without_key):
        assert pr.decrypt_password("anything") is None

    def test_an_empty_passphrase_counts_as_no_key(self, without_key):
        with patch.dict(os.environ, {pr.ENV_KEY_NAME: "   "}, clear=False):
            assert pr.is_enabled() is False


class TestWrongKeyOrTamperedRow:
    def test_a_different_key_cannot_read_it(self, with_key):
        blob = pr.encrypt_password("hunter2")
        with patch.dict(os.environ, {pr.ENV_KEY_NAME: "a-different-passphrase"}, clear=False):
            assert pr.decrypt_password(blob) is None

    def test_a_corrupted_blob_returns_none(self, with_key):
        blob = pr.encrypt_password("hunter2")
        assert pr.decrypt_password(blob[:-4] + "AAAA") is None

    def test_garbage_returns_none(self, with_key):
        assert pr.decrypt_password("not-even-a-token") is None

    def test_none_returns_none(self, with_key):
        assert pr.decrypt_password(None) is None

    def test_empty_string_returns_none(self, with_key):
        assert pr.decrypt_password("") is None


class TestHashStillGoverns:
    def test_login_verification_is_independent_of_the_encrypted_copy(self, with_key):
        """The digest is what authenticates. The ciphertext is for the operator."""
        from src.backend.account_keys import hash_password, verify_password

        digest, salt = hash_password("hunter2")
        blob = pr.encrypt_password("hunter2")

        assert verify_password("hunter2", digest, salt) is True
        assert verify_password("wrong", digest, salt) is False
        # the blob plays no part in either decision
        assert blob is not None

    def test_the_hash_alone_cannot_be_reversed(self, with_key):
        """The whole reason the second column exists — worth asserting."""
        from src.backend.account_keys import hash_password

        digest, salt = hash_password("hunter2")
        assert pr.decrypt_password(digest) is None
        assert pr.decrypt_password(salt) is None


class TestKeyDerivation:
    def test_any_passphrase_shape_is_accepted(self):
        """The operator sets a passphrase, not a base64 key, so length must not matter."""
        for passphrase in ("a", "short", "x" * 500, "with spaces", "🔐🔐🔐"):
            with patch.dict(os.environ, {pr.ENV_KEY_NAME: passphrase}, clear=False):
                assert pr.is_enabled() is True, passphrase
                assert pr.decrypt_password(pr.encrypt_password("p")) == "p", passphrase

    def test_a_stable_passphrase_gives_a_stable_key(self):
        with patch.dict(os.environ, {pr.ENV_KEY_NAME: "stable"}, clear=False):
            first = pr.encrypt_password("p")
        with patch.dict(os.environ, {pr.ENV_KEY_NAME: "stable"}, clear=False):
            assert pr.decrypt_password(first) == "p"


class TestStorage:
    def test_the_row_keeps_hash_salt_and_encrypted_copy(self, temp_db, with_key):
        from src.backend.account_keys import get_credential_db, set_password_db

        set_password_db("acct-1", "hunter2")
        cred = get_credential_db("acct-1")

        assert cred["password_hash"] and cred["password_salt"]
        assert cred["password_enc"], "no encrypted copy was written"
        assert "hunter2" not in cred["password_enc"]

    def test_the_recovery_reader_returns_the_password(self, temp_db, with_key):
        from src.backend.account_keys import (
            get_recoverable_password_db,
            set_password_db,
        )

        set_password_db("acct-1", "hunter2")
        assert get_recoverable_password_db("acct-1") == "hunter2"

    def test_with_no_key_the_row_still_verifies_but_has_no_copy(self, temp_db, without_key):
        """An operator who upgrades without setting the key can still sign in."""
        from src.backend.account_keys import (
            get_credential_db,
            get_recoverable_password_db,
            set_password_db,
            verify_login_db,
        )
        from src.backend.accounts import create_account_db

        create_account_db(name="nokey")
        set_password_db("nokey", "hunter2")

        cred = get_credential_db("nokey")
        assert cred["password_hash"], "no hash written"
        assert cred["password_enc"] is None, "an encrypted copy appeared with no key"
        assert get_recoverable_password_db("nokey") is None

    def test_setting_a_password_twice_replaces_both_copies(self, temp_db, with_key):
        from src.backend.account_keys import (
            get_recoverable_password_db,
            set_password_db,
        )

        set_password_db("acct-1", "first")
        set_password_db("acct-1", "second")
        assert get_recoverable_password_db("acct-1") == "second"

    def test_a_row_without_a_credential_reads_as_none(self, temp_db, with_key):
        from src.backend.account_keys import get_recoverable_password_db
        assert get_recoverable_password_db("nobody") is None

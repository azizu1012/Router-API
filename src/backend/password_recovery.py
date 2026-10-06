"""Reversible copy of a user's password, for the operator to read.

PBKDF2-SHA256 is one-way. That is the right choice for verifying a login and the
wrong one for an operator who needs to see what a user was given — there is no
decode function to write, because there is nothing to decode. Given the hash and
its salt, recovering the password means 200,000 rounds of guessing per candidate.

So this stores a second, reversible copy alongside the hash. Login still verifies
against PBKDF2, which means a database dump on its own does not hand over every
user's password: the reader also needs ROUTER_API_PASSWORD_KEY from .env.

What that buys and what it does not:

    buys    an operator can read a user's password from the dashboard, which is
            the operational reason this exists — telling a user their password
            over chat is otherwise impossible.

    does not  protect against an attacker who already has .env. They hold the
            key and can decrypt every row. Treat .env and usage.db as one
            secret, not two.

The key is derived from ROUTER_API_PASSWORD_KEY. If it is absent, the column
simply stays NULL and the feature reports itself unavailable rather than
silently degrading — a missing key should look like a missing key.
"""

import base64
import hashlib
import os
from typing import Optional

ENV_KEY_NAME = "ROUTER_API_PASSWORD_KEY"


def _key() -> Optional[bytes]:
    """A 32-byte urlsafe-base64 key derived from the env value, or None.

    Deriving with SHA-256 means the operator can set any passphrase they like
    instead of having to generate a correctly-shaped Fernet key by hand.
    """
    raw = (os.getenv(ENV_KEY_NAME) or "").strip()
    if not raw:
        return None
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def is_enabled() -> bool:
    return _key() is not None


def encrypt_password(password: str) -> Optional[str]:
    """Encrypt for operator recovery. None when no key is configured.

    Returns None rather than raising: a missing key must not stop an account
    from being created, only from having a readable password copy.
    """
    key = _key()
    if key is None or not password:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key).encrypt(str(password).encode("utf-8")).decode("ascii")


def decrypt_password(blob: Optional[str]) -> Optional[str]:
    """Reverse encrypt_password. None when unconfigured, absent or tampered."""
    if not blob:
        return None
    key = _key()
    if key is None:
        return None
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(key).decrypt(str(blob).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        # A wrong key or a corrupted row. Both mean the same thing to a caller:
        # there is no readable password, and guessing is not an option.
        return None

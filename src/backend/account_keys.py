"""Backend for account auth tokens, web credentials, and invite codes.

Schema notes
------------
auth tokens are stored decomposed rather than as the literal wire string:

    wire format   sk-<name>-<6 char code>
    stored as     name           = account name (primary grouping key)
                   token_code     = the 6 chars, unique per (name, code)

Storing the parts means "which tokens does this account own" is an indexed
lookup instead of a table scan with LIKE, and each token carries its own quota
and concurrency so one token can be throttled or revoked without touching the
others.
"""

import hashlib
import re
import secrets
import sqlite3
import string
import time
from typing import Any, Dict, List, Optional, Tuple

from src.backend._db import _LOCK, conn as _conn

# 6 chars, alphanumeric only. Excludes every symbol so the token survives being
# pasted through shells, YAML and URLs without escaping.
TOKEN_ALPHABET = string.ascii_letters + string.digits
TOKEN_CODE_LEN = 6
TOKEN_PREFIX = "sk-"

# The legacy master key on `accounts.auth_key`: `sk-` plus a 32-byte
# urlsafe-b64 body, which is always 43 characters from the base64url alphabet.
# It looks like a structured token to the eye but is not one — a master key is
# exempt from the concurrency and RPM/TPM/RPD gates, and parse_token rejects it
# because the code is not TOKEN_CODE_LEN long. Anything matching this shape is
# therefore safe to rotate in place: the replacement is indistinguishable.
MASTER_KEY_BYTES = 32
MASTER_KEY_BODY_LEN = 43
MASTER_KEY_PATTERN = re.compile(
    rf"^{re.escape(TOKEN_PREFIX)}[A-Za-z0-9_-]{{{MASTER_KEY_BODY_LEN}}}$"
)


def is_valid_master_key(key: Any) -> bool:
    """True when key has the canonical master-key shape.

    Tolerates None and non-string input: auth_key comes out of a database row,
    where it is nullable, and a validator that raised on the unexpected value
    would be useless exactly when there is something to report.

    Used to catch an account whose auth_key was set by hand or by an older
    scheme: rotating one would silently change its format, so it should be
    noticed before it happens rather than after.
    """
    return bool(MASTER_KEY_PATTERN.match(str(key or "")))

DEFAULT_MAX_CONCURRENCY = 6
DEFAULT_MIN_INTERVAL_SECONDS = 3.0
DEFAULT_PASSWORD = "1234"

INVITE_TTL_SECONDS = 180  # 3 minutes
INVITE_CODE_LEN = 4
# 4 digits = 10_000 codes, and spent codes are never deleted. Retrying turns an
# eventual-inevitable collision into an invisible detail.
INVITE_CODE_MAX_TRIES = 8


# ── token composition ───────────────────────────────────────────────────────

def make_token_code() -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_CODE_LEN))


def compose_token(name: str, code: str) -> str:
    return f"{TOKEN_PREFIX}{name}-{code}"


def parse_token(raw: str) -> Optional[Tuple[str, str]]:
    """Split a wire token into (name, code). Returns None if malformed.

    Accepts the exact ``sk-<name>-<6 chars>`` shape only. A code length other
    than TOKEN_CODE_LEN is rejected rather than padded, so a truncated token
    cannot accidentally match.
    """
    s = str(raw or "").strip()
    if not s.startswith(TOKEN_PREFIX):
        return None
    body = s[len(TOKEN_PREFIX):]
    # name may itself contain '-', so split on the LAST one
    idx = body.rfind("-")
    if idx <= 0:
        return None
    name = body[:idx]
    code = body[idx + 1:]
    if not name or len(code) != TOKEN_CODE_LEN:
        return None
    if any(ch not in TOKEN_ALPHABET for ch in code):
        return None
    return name, code


# ── password hashing ────────────────────────────────────────────────────────

def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", str(password).encode("utf-8"), salt.encode("utf-8"), 200_000
    ).hex()
    return digest, salt


def verify_password(password: str, digest: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return secrets.compare_digest(candidate.encode(), str(digest).encode())


# ── account_keys CRUD ───────────────────────────────────────────────────────

def list_keys_db(account_id: str, include_disabled: bool = True) -> List[Dict[str, Any]]:
    with _LOCK:
        c = _conn()
        try:
            sql = "SELECT * FROM account_keys WHERE account_id = ?"
            if not include_disabled:
                sql += " AND enabled = 1"
            sql += " ORDER BY created_at"
            return [dict(r) for r in c.execute(sql, (account_id,)).fetchall()]
        finally:
            c.close()


def create_key_db(
    account_id: str,
    name: str,
    tier: str = "free",
    rpm: int = 30,
    tpm: int = 200000,
    rpd: int = 1000,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
    label: str = "",
) -> Dict[str, Any]:
    now = int(time.time())
    # Retry on the (unlikely) code collision rather than failing the request.
    for _ in range(8):
        code = make_token_code()
        key_id = secrets.token_hex(8)
        row = {
            "key_id": key_id,
            "account_id": account_id,
            "name": name,
            "token_code": code,
            "enabled": 1,
            "tier": tier,
            "rpm": int(rpm),
            "tpm": int(tpm),
            "rpd": int(rpd),
            "max_concurrency": max(1, int(max_concurrency)),
            "min_interval_seconds": max(0.0, float(min_interval_seconds)),
            "label": label or "",
            "created_at": now,
            "updated_at": now,
        }
        with _LOCK:
            c = _conn()
            try:
                c.execute(
                    """INSERT INTO account_keys
                       (key_id, account_id, name, token_code, enabled, tier, rpm, tpm,
                        rpd, max_concurrency, min_interval_seconds, label, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    tuple(row[k] for k in (
                        "key_id", "account_id", "name", "token_code", "enabled", "tier",
                        "rpm", "tpm", "rpd", "max_concurrency", "min_interval_seconds",
                        "label", "created_at", "updated_at")),
                )
                c.commit()
            except Exception as e:
                c.close()
                if "UNIQUE" in str(e).upper():
                    continue  # code collision, try again
                raise
            finally:
                try:
                    c.close()
                except Exception:
                    pass
        return row
    raise RuntimeError("could not allocate a unique token code")


def find_key_db(name: str, code: str, include_disabled: bool = False) -> Optional[Dict[str, Any]]:
    sql = "SELECT * FROM account_keys WHERE LOWER(name) = LOWER(?) AND token_code = ?"
    if not include_disabled:
        sql += " AND enabled = 1"
    with _LOCK:
        c = _conn()
        try:
            r = c.execute(sql, (name, code)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def update_key_db(key_id: str, **updates: Any) -> Optional[Dict[str, Any]]:
    allowed = {"enabled", "tier", "rpm", "tpm", "rpd",
               "max_concurrency", "min_interval_seconds", "label"}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return get_key_db(key_id)
    sets = ", ".join(f"{k} = ?" for k in fields)
    args = list(fields.values()) + [int(time.time()), key_id]
    with _LOCK:
        c = _conn()
        try:
            c.execute(f"UPDATE account_keys SET {sets}, updated_at = ? WHERE key_id = ?", args)
            c.commit()
        finally:
            c.close()
    return get_key_db(key_id)


def get_key_db(key_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        c = _conn()
        try:
            r = c.execute("SELECT * FROM account_keys WHERE key_id = ?", (key_id,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def delete_key_db(key_id: str) -> Optional[Dict[str, Any]]:
    existing = get_key_db(key_id)
    if not existing:
        return None
    with _LOCK:
        c = _conn()
        try:
            c.execute("DELETE FROM account_keys WHERE key_id = ?", (key_id,))
            c.commit()
        finally:
            c.close()
    return existing


def count_keys_db(account_id: str, include_disabled: bool = False) -> int:
    sql = "SELECT COUNT(*) FROM account_keys WHERE account_id = ?"
    if not include_disabled:
        sql += " AND enabled = 1"
    with _LOCK:
        c = _conn()
        try:
            return int(c.execute(sql, (account_id,)).fetchone()[0])
        finally:
            c.close()


def all_keys_db() -> List[Dict[str, Any]]:
    with _LOCK:
        c = _conn()
        try:
            return [dict(r) for r in c.execute("SELECT * FROM account_keys").fetchall()]
        finally:
            c.close()


# ── account_credentials ─────────────────────────────────────────────────────

def set_password_db(account_id: str, password: str, must_change: int = 0) -> None:
    digest, salt = hash_password(password)
    # A second, reversible copy so an operator can read the password back. Login
    # still verifies against the PBKDF2 digest above; this column is never used
    # to authenticate. See src/backend/password_recovery.py for what this does
    # and does not protect against.
    from src.backend.password_recovery import encrypt_password
    recoverable = encrypt_password(password)
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "INSERT INTO account_credentials "
                "(account_id, password_hash, password_salt, password_enc, must_change, updated_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(account_id) DO UPDATE SET "
                "password_hash=excluded.password_hash, password_salt=excluded.password_salt, "
                "password_enc=excluded.password_enc, "
                "must_change=excluded.must_change, updated_at=excluded.updated_at",
                (account_id, digest, salt, recoverable, int(must_change), int(time.time())),
            )
            c.commit()
        finally:
            c.close()


def get_recoverable_password_db(account_id: str) -> Optional[str]:
    """The plaintext password for an account, or None.

    Admin-only by the caller's route guard. Returns None when the row has no
    encrypted copy, when no key is configured, or when the stored blob does not
    decrypt — all three mean the same thing to a caller and guessing is not an
    option.
    """
    with _LOCK:
        c = _conn()
        try:
            row = c.execute(
                "SELECT password_enc FROM account_credentials WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        finally:
            c.close()
    if not row:
        return None
    from src.backend.password_recovery import decrypt_password
    return decrypt_password(row[0])


def get_credential_db(account_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        c = _conn()
        try:
            r = c.execute(
                "SELECT * FROM account_credentials WHERE account_id = ?", (account_id,)
            ).fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def verify_login_db(name: str, password: str) -> Optional[Dict[str, Any]]:
    """Return the account row if name+password match, else None."""
    from src.backend.accounts import find_account_by_name
    acc = find_account_by_name(name)
    if not acc:
        return None
    cred = get_credential_db(acc["account_id"])
    if not cred:
        return None
    if not verify_password(password, cred["password_hash"], cred["password_salt"]):
        return None
    return acc


# ── invite codes ────────────────────────────────────────────────────────────

def create_invite_db(created_by: str, ttl_seconds: int = INVITE_TTL_SECONDS) -> Dict[str, Any]:
    """Issue a one-time enrollment code. Re-issuing supersedes the previous one.

    Codes are 4 digits (10_000 possibilities) but spent codes are kept as the
    audit trail, so a collision with one is inevitable once the table is
    populated — roughly 1 in 10_000 per issue. Retrying on IntegrityError keeps
    that a non-event; raising a bare sqlite3 error to the admin is not an
    acceptable outcome for a button they press by hand.
    """
    now = int(time.time())
    with _LOCK:
        c = _conn()
        try:
            # A creator holds at most one live code; issuing a new one voids the old.
            c.execute("DELETE FROM invite_codes WHERE used_at IS NULL")
            last_error: Optional[Exception] = None
            for _ in range(INVITE_CODE_MAX_TRIES):
                code = "".join(
                    secrets.choice(string.digits) for _ in range(INVITE_CODE_LEN)
                )
                try:
                    c.execute(
                        "INSERT INTO invite_codes "
                        "(code, created_by, created_at, expires_at) "
                        "VALUES (?,?,?,?)",
                        (code, created_by, now, now + int(ttl_seconds)),
                    )
                    c.commit()
                    break
                except sqlite3.IntegrityError as e:
                    last_error = e
            else:
                c.rollback()
                raise RuntimeError(
                    f"could not issue invite code after "
                    f"{INVITE_CODE_MAX_TRIES} attempts (table may be full): "
                    f"{last_error}"
                )
        finally:
            c.close()
    return {
        "code": code, "created_by": created_by, "created_at": now,
        "expires_at": now + int(ttl_seconds), "ttl_seconds": int(ttl_seconds),
    }


def consume_invite_db(code: str) -> Optional[Dict[str, Any]]:
    """Validate and burn an invite code. Returns the row if it was valid.

    Single-use and expiry are both enforced here so a race cannot consume the same
    code twice: the UPDATE is guarded by the still-unused, still-unexpired state.
    """
    now = int(time.time())
    with _LOCK:
        c = _conn()
        try:
            row = c.execute(
                "SELECT * FROM invite_codes WHERE code = ?", (str(code).strip(),)
            ).fetchone()
            if not row:
                return None
            if row["used_at"] is not None:
                return None
            if int(row["expires_at"]) <= now:
                return None
            cur = c.execute(
                "UPDATE invite_codes SET used_at = ? WHERE code = ? AND used_at IS NULL AND expires_at > ?",
                (now, str(code).strip(), now),
            )
            if cur.rowcount != 1:
                return None  # lost the race; someone else consumed it
            c.commit()
            return dict(row)
        finally:
            c.close()


def peek_invite_db(code: str) -> Optional[Dict[str, Any]]:
    now = int(time.time())
    with _LOCK:
        c = _conn()
        try:
            r = c.execute(
                "SELECT * FROM invite_codes WHERE code = ? AND used_at IS NULL AND expires_at > ?",
                (str(code).strip(), now),
            ).fetchone()
            return dict(r) if r else None
        finally:
            c.close()
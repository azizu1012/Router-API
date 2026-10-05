"""Invite codes are 4 digits, so a collision is a certainty over time.

create_invite_db clears the unused codes and then picks 4 random digits. Used
codes stay in the table — that is the point of them, they are the audit trail.
So once the table has accumulated enough used codes, roughly 1-in-10_000 issues
collides with one and the INSERT raises IntegrityError. In production that is
an admin who cannot issue a code at all, with no way to retry: the endpoint
just 500s.

The failure only shows up after the table is big enough, which is why it hides
from a fresh database and only ever appears in CI as a random red build.
"""

import sqlite3
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

from src.backend.account_keys import consume_invite_db, create_invite_db


def _spent(codes):
    """Put codes in the table as already-used rows (the audit trail)."""
    db = sqlite3.connect("usage.db")
    try:
        db.execute("DELETE FROM invite_codes")
        for i, code in enumerate(codes):
            db.execute(
                "INSERT INTO invite_codes "
                "(code, created_by, created_at, expires_at, used_at) "
                "VALUES (?,?,0,9999999999,1)",
                (code, f"old{i}"),
            )
        db.commit()
    finally:
        db.close()


class TestInviteCodeCollision:
    def test_retry_when_random_code_already_used(self):
        """A code that exists as spent must not abort the insert."""
        _spent(["1234"])

        # Force three collisions, then let the fourth attempt through.
        # secrets.choice is called once per digit, so the script is a flat
        # list of characters: 3 × "1234" then "5678".
        script = list("1234123412345678")
        with patch("secrets.choice",
                   side_effect=lambda _seq: script.pop(0)):
            inv = create_invite_db("admin")

        assert inv["code"] == "5678"
        assert not script, "should have consumed all sixteen digits"

    def test_exhausted_retries_raises_clear_error(self):
        """Randomness that never escapes must not hang or throw raw sqlite."""
        _spent(["9999"])

        with patch("secrets.choice", return_value="9"):
            with pytest.raises(RuntimeError) as exc:
                create_invite_db("admin")
        assert "invite" in str(exc.value).lower()

    def test_retry_gives_up_quickly(self):
        """8 attempts, not an unbounded loop."""
        _spent(["9999"])
        calls = []

        def count(_seq):
            calls.append(1)
            return "9"

        with patch("secrets.choice", side_effect=count):
            with pytest.raises(RuntimeError):
                create_invite_db("admin")
        assert len(calls) == 8 * 4, f"expected 32 digit draws, got {len(calls)}"

    def test_issue_works_against_a_crowded_table(self):
        """With retries, a table full of spent codes must still issue."""
        _spent([f"{n:04d}" for n in range(50)])

        issued = []
        for _ in range(50):
            inv = create_invite_db("admin")
            issued.append(inv["code"])
        assert len(issued) == 50
        # Every live code must be reachable and consumable.
        assert consume_invite_db(issued[-1]) is not None
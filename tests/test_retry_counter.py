"""The retry counter must advance in both pool paths.

call_nonstream and call_stream implement the same loop — retry the current
member, swap it after POOL_SWAP_FAILURES, keep going until the time budget
runs out. retry_attempt is what that loop reports to reserve_key, and reserve_key
uses it to switch on Extreme Checking at attempt >= 10 (70% of the limit, idle
keys only).

Because stream left the counter at zero, a streaming request could loop past
attempt ten and never enter the safety valve that the non-streaming path
switches into, under exactly the load the valve exists for. The two paths are
copy-adapted from one another, so they are meant to agree.

Both paths are pinned below so the counters cannot silently diverge again.
"""

import asyncio
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core import pool_manager as pm
from src.core.router import router


class _FakePool:
    """Hands out members in order; refuses once they are all skipped."""

    max_retry_seconds = 3.0

    def __init__(self, members):
        self.members = list(members)
        self.released = []

    async def acquire(self, skip=None, timeout=1.0):
        skip = skip or set()
        for m in self.members:
            if m not in skip:
                return m
        raise TimeoutError("no free member")

    def release(self, member):
        self.released.append(member)


async def _drive(path, members, pool):
    """Run one call with every provider attempt failing transiently.

    Returns the retry_attempt values reserve_key was handed.
    """
    seen = []

    def spy(model_alias, model_id=None, account=None, estimated_tokens=0,
            retry_attempt=0, **kw):
        seen.append(retry_attempt)
        return {"key": f"KEY{len(seen)}", "name": "m1",
                "model_alias": model_alias, "model_id": model_id,
                "provider": "gemini"}

    async def boom(*a, **k):
        raise RuntimeError("503 unavailable overload")

    async def quota_ok(*a, **k):
        return True

    # freeze_key / apply_error_penalty / count_transient_error each write to
    # SQLite, which takes long enough per iteration that the wall-clock budget
    # ends the loop after a different number of turns in each path. Stub them so
    # the assertions depend on the counter and not on how fast the disk is.
    patches = [
        patch.object(router, "reserve_key", spy),
        patch.object(router, "resolve_pool", lambda alias: pool),
        patch.object(router, "acquire_quota", quota_ok),
        patch.object(router, "release_key", lambda *a, **k: None),
        patch.object(router, "freeze_key", lambda *a, **k: None),
        patch.object(router, "update_model_health", lambda *a, **k: None),
        patch.object(router, "record_429", lambda *a, **k: None),
        patch.object(pm, "apply_error_penalty", lambda *a, **k: None),
        patch.object(pm, "count_transient_error", lambda *a, **k: None),
        patch.object(pm, "acompletion", boom),
        patch.object(pm, "_classify_error", lambda e: "unavailable"),
        patch.object(pm, "_retry_delay", lambda n: 0.0),
    ]
    for p in patches:
        p.start()
    try:
        try:
            if path == "stream":
                async for _ in pm.pool_manager.call_stream(
                    "gemini-flash",
                    [{"role": "user", "content": "hi"}],
                    max_tokens=16,
                ):
                    pass
            else:
                await pm.pool_manager.call_nonstream(
                    "gemini-flash",
                    [{"role": "user", "content": "hi"}],
                    max_tokens=16,
                )
        except Exception:
            pass
    finally:
        for p in patches:
            p.stop()
    return seen


def _run(path, members=("m1", "m2", "m3", "m4")):
    return asyncio.run(_drive(path, members, _FakePool(list(members))))


class TestRetryCounterAdvances:
    @pytest.mark.parametrize("path", ["nonstream", "stream"])
    def test_counter_increments(self, path):
        seen = _run(path)
        assert len(seen) >= 2, "test did not drive enough attempts to be meaningful"
        assert seen[0] == 0, "the first attempt must always report 0"
        assert seen[-1] > 0, (
            f"{path}: retry_attempt stayed at 0 across {len(seen)} attempts, so "
            f"reserve_key can never switch on Extreme Checking"
        )

    def test_both_paths_report_an_increasing_counter(self):
        """Not "identical sequences" — the loop is wall-clock bounded, so the
        two paths can legitimately turn a different number of times. What must
        hold is that each one climbs on its own."""
        for path in ("nonstream", "stream"):
            seen = _run(path)
            assert len(seen) >= 2
            assert all(b > a for a, b in zip(seen, seen[1:])), (
                f"{path}: counter stalled -> {seen}"
            )

    def test_counters_are_monotonic(self):
        for path in ("nonstream", "stream"):
            seen = _run(path)
            assert seen == sorted(seen), f"{path}: not monotonic -> {seen}"
            assert len(set(seen)) == len(seen), f"{path}: repeats a value -> {seen}"

    @pytest.mark.parametrize("path", ["nonstream", "stream"])
    def test_counter_never_resets_inside_the_loop(self, path):
        """The bug was `pool_try = -1` sitting inside the loop, which pinned
        the counter at 0 no matter how many turns were taken. The exact
        invariant is that the counter equals the turn index — reset once before
        the loop and incremented per turn, never reinitialised.

        This is asserted rather than "reaches 10" because the loop is bounded
        by wall clock, so how many turns a test manages is a property of the
        machine. Index-equality pins the defect without that dependency.
        """
        seen = _run(path)
        assert len(seen) >= 2, "not enough turns to tell reset from increment"
        assert seen == list(range(len(seen))), (
            f"{path}: counter did not track the turn index -> {seen}"
        )
"""ModelPool — slot-based concurrency.

ModelPool used to be a rotation pool with start/reset/record_failure/next and
time-based exhaustion. It is now slot-based: every member holds one asyncio.Lock
and a member is busy exactly while a request is in flight. Swapping on failure
and retry budgets live in PoolManager, not here.

These tests target the invariants that actually matter for availability:

  - a member serves one request at a time
  - N concurrent requests spread over N members, and the N+1 waits
  - a busy member is never handed out
  - custom endpoints are preferred over Gemini members when free
  - skipping works so PoolManager can exclude a member it just saw fail
  - acquire gives up rather than hanging forever

Every acquire goes through `_acquire`, which wraps the call in asyncio.wait_for.
ModelPool.acquire only checks its `timeout` at the top of the poll loop, so a
pool that hands out an already-busy member — or whose release() stopped freeing
slots — would block on lock.acquire() forever. That failure shows up here as a
fast TimeoutError instead of a suite that hangs until CI kills it.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.router.pool import ModelPool

# Outer guard, deliberately larger than the inner timeouts so a genuinely slow
# machine does not produce a spurious failure.
_GUARD = 10.0


def make_pool(members=("m1", "m2", "m3"), custom=None, max_retry_seconds=120,
              swap_failures=5):
    return ModelPool(
        {
            "members": list(members),
            "swap_failures": swap_failures,
            "max_retry_seconds": max_retry_seconds,
        },
        set(custom or ()),
    )


async def _acquire(pool, skip=None, timeout=1.0):
    """acquire() with a hard outer deadline. See module docstring."""
    return await asyncio.wait_for(pool.acquire(skip=skip, timeout=timeout),
                                  timeout=_GUARD)


# ── construction ────────────────────────────────────────────────────────────

class TestInit:
    def test_members_copied(self):
        src = ["a", "b"]
        pool = make_pool(src)
        assert pool.members == ["a", "b"]
        src.append("c")
        assert pool.members == ["a", "b"], "must not alias the caller's list"

    def test_one_lock_per_member(self):
        pool = make_pool(["a", "b", "c"])
        assert set(pool._locks) == {"a", "b", "c"}
        assert all(not lk.locked() for lk in pool._locks.values())

    def test_custom_members_split_out(self):
        pool = make_pool(["gem", "ep1", "ep2"], custom=["ep1", "ep2"])
        assert pool._custom_members == {"ep1", "ep2"}
        assert pool._gemini_members == {"gem"}

    def test_limits_parsed(self):
        pool = make_pool(max_retry_seconds=42, swap_failures=3)
        assert pool.max_retry_seconds == 42
        assert pool.swap_failures == 3

    def test_get_or_create_is_a_singleton_per_name(self):
        ModelPool._instances.clear()
        try:
            cfg = {"members": ["x"], "swap_failures": 5, "max_retry_seconds": 10}
            a = ModelPool.get_or_create("p", cfg)
            b = ModelPool.get_or_create("p", cfg)
            assert a is b
            c = ModelPool.get_or_create("other", cfg)
            assert c is not a
        finally:
            ModelPool._instances.clear()

    def test_get_or_create_ignores_config_on_second_call(self):
        """A later config change must not silently reconfigure a live pool."""
        ModelPool._instances.clear()
        try:
            ModelPool.get_or_create("p", {"members": ["a"], "swap_failures": 5,
                                          "max_retry_seconds": 10})
            again = ModelPool.get_or_create("p", {"members": ["a", "b"],
                                                 "swap_failures": 5,
                                                 "max_retry_seconds": 99})
            assert again.members == ["a"], "existing instance must be reused as-is"
        finally:
            ModelPool._instances.clear()


# ── acquire / release ───────────────────────────────────────────────────────

class TestAcquireRelease:
    @pytest.mark.anyio
    async def test_returns_a_member(self):
        pool = make_pool()
        m = await _acquire(pool)
        assert m in pool.members
        pool.release(m)

    @pytest.mark.anyio
    async def test_releases_free_the_slot(self):
        pool = make_pool(["only"])
        m = await _acquire(pool)
        assert pool._locks[m].locked()
        pool.release(m)
        assert not pool._locks[m].locked()

    @pytest.mark.anyio
    async def test_skip_excludes_members(self):
        pool = make_pool(["a", "b"])
        m = await _acquire(pool, skip={"a"})
        assert m == "b"
        pool.release(m)

    @pytest.mark.anyio
    async def test_concurrent_acquires_get_distinct_members(self):
        """The core invariant: one request per member at a time."""
        pool = make_pool(["a", "b", "c"])
        got = await asyncio.gather(*[_acquire(pool) for _ in range(3)])
        assert sorted(got) == ["a", "b", "c"], f"expected 3 distinct, got {got}"
        for m in got:
            pool.release(m)

    @pytest.mark.anyio
    async def test_fourth_waits_for_a_slot(self):
        pool = make_pool(["a", "b"])
        first = await _acquire(pool)
        second = await _acquire(pool)

        pending = asyncio.create_task(_acquire(pool, timeout=1.0))
        await asyncio.sleep(0.15)
        assert not pending.done(), "a third acquire must not succeed with 2 members"

        pool.release(first)
        got = await pending
        assert got == first, "the waiter should take the slot that just freed"
        pool.release(got)
        pool.release(second)

    @pytest.mark.anyio
    async def test_busy_member_is_not_handed_out(self):
        pool = make_pool(["a"])
        held = await _acquire(pool)
        with pytest.raises(TimeoutError):
            await _acquire(pool, timeout=0.2)
        pool.release(held)

    @pytest.mark.anyio
    async def test_timeout_raises_with_a_useful_message(self):
        pool = make_pool(["a"])
        held = await _acquire(pool)
        with pytest.raises(TimeoutError) as exc:
            await _acquire(pool, timeout=0.1)
        assert "a" in str(exc.value)
        pool.release(held)

    @pytest.mark.anyio
    async def test_all_skipped_times_out(self):
        pool = make_pool(["a", "b"])
        with pytest.raises(TimeoutError):
            await _acquire(pool, skip={"a", "b"}, timeout=0.1)

    @pytest.mark.anyio
    async def test_release_then_acquire_returns_a_freed_member(self):
        pool = make_pool(["a", "b"])
        first = await _acquire(pool)
        second = await _acquire(pool)
        pool.release(first)
        third = await _acquire(pool)
        assert third == first
        pool.release(second)
        pool.release(third)


# ── custom endpoint priority ────────────────────────────────────────────────

class TestCustomEndpointPriority:
    @pytest.mark.anyio
    async def test_custom_member_preferred_while_free(self):
        pool = make_pool(["gem", "ep"], custom=["ep"])
        got = [await _acquire(pool) for _ in range(2)]
        assert got[0] == "ep", "custom endpoint must be tried first"
        pool.release(got[0])
        pool.release(got[1])

    @pytest.mark.anyio
    async def test_falls_back_to_gemini_when_custom_busy(self):
        pool = make_pool(["gem", "ep"], custom=["ep"])
        first = await _acquire(pool)
        assert first == "ep"
        second = await _acquire(pool)
        assert second == "gem", "must fall back rather than wait on a busy endpoint"
        pool.release(first)
        pool.release(second)

    @pytest.mark.anyio
    async def test_custom_priority_survives_when_custom_is_wedged(self):
        """The exact production failure: an endpoint that never releases."""
        pool = make_pool(["gem", "ep"], custom=["ep"])
        wedged = await _acquire(pool)
        assert wedged == "ep"
        served = await _acquire(pool)
        assert served == "gem", "a stuck endpoint must not block Gemini traffic"
        pool.release(served)
        pool.release(wedged)


# ── sync_custom_members ─────────────────────────────────────────────────────

class TestSyncCustomMembers:
    def test_adds_new_endpoint_with_a_lock(self):
        pool = make_pool(["gem"])
        pool.sync_custom_members({"ep1"})
        assert "ep1" in pool.members
        assert pool._custom_members == {"ep1"}
        assert "ep1" in pool._locks
        assert pool._gemini_members == {"gem"}

    def test_idempotent(self):
        pool = make_pool(["gem"])
        pool.sync_custom_members({"ep1"})
        pool.sync_custom_members({"ep1"})
        assert pool.members.count("ep1") == 1
        assert len(pool._locks) == len(set(pool._locks))

    def test_moves_existing_member_from_gemini_to_custom(self):
        pool = make_pool(["gem", "ep1"])
        assert pool._gemini_members == {"gem", "ep1"}
        pool.sync_custom_members({"ep1"})
        assert pool._gemini_members == {"gem"}
        assert pool._custom_members == {"ep1"}

    @pytest.mark.anyio
    async def test_synced_member_is_usable(self):
        pool = make_pool(["gem"])
        pool.sync_custom_members({"ep1"})
        got = await _acquire(pool)
        assert got == "ep1"
        pool.release(got)


# ── the property that keeps the router alive ────────────────────────────────

class TestRecovery:
    @pytest.mark.anyio
    async def test_one_bad_member_does_not_take_down_the_pool(self):
        """A member left holding a lock must not starve the rest.

        Members are held in a set, so which one comes back first is not
        defined — take whatever arrives and wedge it.
        """
        pool = make_pool(["bad", "good"])
        stuck = await _acquire(pool)
        other = [m for m in pool.members if m != stuck][0]

        got = await _acquire(pool)
        assert got == other, "the other member must still serve"
        pool.release(got)

        # and the pool is usable again once the wedged member is released
        pool.release(stuck)
        again = await _acquire(pool)
        pool.release(again)

    @pytest.mark.anyio
    async def test_release_is_idempotent(self):
        """A surplus release must be a no-op, not an exception.

        pool_manager releases the member from its error handlers and again in
        the finally block. asyncio.Lock raises "Lock is not acquired" on a
        second release, and the handlers are not guarded — so without this the
        request aborts carrying the wrong error.
        """
        pool = make_pool(["a"])
        m = await _acquire(pool)
        pool.release(m)
        pool.release(m)  # would raise if release were not idempotent
        assert not pool._locks[m].locked()

    @pytest.mark.anyio
    async def test_release_of_never_acquired_member_is_harmless(self):
        pool = make_pool(["a"])
        pool.release("a")  # no acquire at all
        m = await _acquire(pool)
        assert m == "a"
        pool.release(m)

    @pytest.mark.anyio
    async def test_release_of_unknown_member_is_harmless(self):
        pool = make_pool(["a"])
        pool.release("not-a-member")
        m = await _acquire(pool)
        assert m == "a"
        pool.release(m)

    @pytest.mark.anyio
    async def test_one_slot_per_member_survives_a_surplus_release(self):
        """A surplus release must free the slot, and only free it once."""
        pool = make_pool(["only"])
        first = await _acquire(pool)
        pool.release(first)
        pool.release(first)  # surplus: must not corrupt the slot

        # available again, exactly as after a single release
        again = await _acquire(pool, timeout=0.5)
        assert again == "only"

        # and still only one holder
        with pytest.raises(TimeoutError):
            await _acquire(pool, timeout=0.2)
        pool.release(again)

    @pytest.mark.anyio
    async def test_slot_returns_after_churn(self):
        pool = make_pool(["a"])
        for _ in range(5):
            m = await _acquire(pool)
            pool.release(m)
        assert not pool._locks["a"].locked()
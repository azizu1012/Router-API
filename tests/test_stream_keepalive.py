"""Keepalive must not kill the stream it is protecting.

Anthropic SSE clients (Claude Code among them) hang up on a silent connection,
so the proxy pings while the model is thinking. The ping is emitted from a
wrapper around the pool iterator, and that wrapper used to pull from the same
async generator twice at once:

    item = await asyncio.wait_for(asyncio.shield(aiter_.__anext__()), 1.0)
    except asyncio.TimeoutError:
        continue          # <- starts a SECOND __anext__ on a generator still running

`shield` exists precisely so the in-flight pull survives the timeout, which
makes `continue` the worst possible next line. asyncio raised

    RuntimeError: anext(): asynchronous generator is already running

and the stream died before any content. A thinking turn takes longer than a
second, so this fired on the first slow model response — the exact case the
keepalive exists for. HTTP status was still 200 and the client received a
well-formed `error` SSE event, so nothing upstream flagged it.

These drive the real `_iter_with_keepalive`, not a copy of it: a test that
reimplements the logic proves only that the copy is correct.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.claude_proxy.handler.proxy_stream import (
    _KEEPALIVE,
    KEEPALIVE_INTERVAL,
    _POLL_TIMEOUT,
    _iter_with_keepalive,
)


def _slow_stream(chunks, delay):
    """A pool-iterator stand-in whose chunks arrive after a delay.

    Wrapped in a real async generator so the "already running" failure mode is
    reproduced exactly — a hand-rolled __anext__ would not raise it.
    """

    class _S:
        async def _gen(self):
            for c in chunks:
                await asyncio.sleep(delay)
                yield {"text": c}

        def __aiter__(self):
            return self._gen()

    return _S()


async def _drain(stream, keepalive_interval=KEEPALIVE_INTERVAL):
    """Collect real items and keepalive pings separately."""
    items, pings = [], 0
    async for got in _iter_with_keepalive(stream, keepalive_interval):
        if got is _KEEPALIVE:
            pings += 1
        else:
            items.append(got["text"])
    return items, pings


# ── the regression ──────────────────────────────────────────────────────────

class TestSlowModelStillStreams:
    @pytest.mark.anyio
    async def test_a_stream_slower_than_the_timeout_delivers_every_chunk(self):
        # Every chunk takes longer than _POLL_TIMEOUT, so every pull times out
        # at least once. The bug turned that into a RuntimeError on the second
        # concurrent pull.
        items, _ = await _drain(_slow_stream(["a", "b", "c"], delay=_POLL_TIMEOUT + 0.4))

        assert items == ["a", "b", "c"]

    @pytest.mark.anyio
    async def test_a_slow_first_chunk_does_not_raise(self):
        """The production symptom: first token after a thinking pause."""
        items, _ = await _drain(_slow_stream(["hello"], delay=_POLL_TIMEOUT + 0.5))

        assert items == ["hello"]

    @pytest.mark.anyio
    async def test_no_two_pulls_are_ever_in_flight(self):
        """The invariant, stated directly.

        The old code reached 2 concurrent pulls on every timeout.
        """
        live = 0
        peak = 0

        class _Counting:
            async def _gen(self):
                nonlocal live, peak
                for _ in range(4):
                    live += 1
                    peak = max(peak, live)
                    try:
                        await asyncio.sleep(_POLL_TIMEOUT + 0.3)
                        yield {"text": "t"}
                    finally:
                        live -= 1

            def __aiter__(self):
                return self._gen()

        await _drain(_Counting())

        assert peak == 1, f"{peak} concurrent pulls on one generator"

    @pytest.mark.anyio
    async def test_the_stream_still_terminates_after_a_timeout(self):
        items, _ = await _drain(_slow_stream([], delay=0))
        assert items == []


# ── keepalive still does its job ────────────────────────────────────────────

class TestKeepaliveStillPings:
    @pytest.mark.anyio
    async def test_a_quiet_model_produces_pings(self):
        # If the fix were "stop pinging", the reason this function exists would
        # be gone and long thinking phases would hang clients again.
        _, pings = await _drain(
            _slow_stream(["x"], delay=_POLL_TIMEOUT * 3),
            keepalive_interval=1.0,
        )
        assert pings >= 1

    @pytest.mark.anyio
    async def test_a_fast_model_is_not_spammed_with_pings(self):
        _, pings = await _drain(_slow_stream(["x", "y", "z"], delay=0))
        assert pings == 0

    @pytest.mark.anyio
    async def test_pings_do_not_reorder_the_content(self):
        items, pings = await _drain(
            _slow_stream(["a", "b", "c"], delay=_POLL_TIMEOUT + 0.2),
            keepalive_interval=1.0,
        )
        assert pings >= 1, "expected a ping during the wait"
        assert items == ["a", "b", "c"], "a ping displaced a chunk"


# ── fast path unchanged ─────────────────────────────────────────────────────

class TestFastStreamUnaffected:
    @pytest.mark.anyio
    async def test_an_instant_stream_passes_straight_through(self):
        items, _ = await _drain(_slow_stream(["1", "2", "3"], delay=0))
        assert items == ["1", "2", "3"]

    @pytest.mark.anyio
    async def test_a_single_chunk_stream_ends(self):
        items, _ = await _drain(_slow_stream(["only"], delay=0))
        assert items == ["only"]


# ── the shape, so a reintroduce is caught cheaply ───────────────────────────

class TestKeepaliveShape:
    def test_the_poll_timeout_is_shorter_than_the_ping_interval(self):
        """A poll timeout at or above the ping interval can never emit a ping:
        every pass times out too early to notice enough elapsed time."""
        assert _POLL_TIMEOUT < KEEPALIVE_INTERVAL

    def test_the_ping_interval_is_a_few_polls_long(self):
        # Guards a timeout so small that every chunk produces a ping, which
        # would bury the real content in noise.
        assert KEEPALIVE_INTERVAL >= _POLL_TIMEOUT * 2

    def test_exactly_one_pull_site_exists(self):
        """Reading the body catches a reintroduced `shield(aiter_.__anext__())`
        inside the except branch — the shape that caused this bug. The
        behavioural tests above catch it too; this one names the mistake.

        The docstring deliberately names __anext__, so the body is what gets
        counted, not the whole source.
        """
        import inspect

        src = inspect.getsource(_iter_with_keepalive)
        # Skip the docstring, which deliberately names __anext__ to explain the
        # bug. Count from the first statement instead.
        body = src[src.index("aiter_ = stream.__aiter__()"):]

        # Two sites are expected: the initial pull, and one after a chunk was
        # consumed. What must never exist is a pull inside the timeout handler.
        assert body.count("__anext__") == 2, (
            f"expected one initial pull plus one after each chunk, got "
            f"{body.count('__anext__')} sites"
        )
        timeout_block = body.split("except asyncio.TimeoutError:")[1].split("except")[0]
        assert "__anext__" not in timeout_block, (
            "a pull started inside the timeout handler is the double-pull bug: "
            "the previous one is still in flight"
        )
        # And the second site must come after the item is yielded, i.e. it runs
        # only once the previous pull has actually completed.
        after_yield = body[body.index("yield item"):]
        assert "__anext__" in after_yield, (
            "the next pull must be created after the item is yielded, so it "
            "cannot overlap the one that just finished"
        )

"""A custom pool model is rate limited on every path, not just non-streaming.

Custom endpoint models do not go through the Gemini quota path. They get their
own per-model sliding window, check_custom_pool_rate, which admits 10 requests
per minute and then says no. That check used to live only inside
_resolve_and_call — the single shared helper that resolves the key, applies
quota, and issues the call.

The streaming paths never call _resolve_and_call. They have to: the key has to
stay reserved for as long as the consumer keeps pulling chunks, and
_resolve_and_call releases it in a finally the moment it returns. So stream
re-implements the setup, and when it was written the re-implementation did not
include the custom-endpoint branch. Result: a custom model capped at 10 RPM on
the non-streaming route was uncapped on both streaming routes. Gemini keys were
unaffected, which is why nothing failed loudly — custom endpoints are the
opt-in feature, so the hole only opened once someone configured one.

These tests pin all three paths to the same limit.
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core import pool_manager as pm
from src.core.providers import custom_endpoint_client
from src.core.router import router


class _FakePool:
    max_retry_seconds = 2.0

    def __init__(self, member="m1"):
        self.member = member
        self.released = []

    async def acquire(self, skip=None, timeout=1.0):
        return self.member

    def release(self, member):
        self.released.append(member)


def _custom_reservation():
    return {
        "key": "endpoint-auth-key",
        "name": "my-endpoint",
        "model_alias": "gemini-flash",
        "model_id": "my-model",
        "provider": "custom",
        "api_base": "http://endpoint.local/v1",
    }


async def _run(path, *, rate_ok, pool_mode):
    """Drive one call with a custom reservation and a controlled rate gate.

    Returns True if the provider was reached, False if the rate gate stopped it.
    """
    reached = []

    async def resolve_model(*a, **k):
        return "gemini-flash", "my-model", "endpoint-auth-key", "my-model", _custom_reservation()

    async def gate(model_id):
        return rate_ok

    async def provider(*a, **k):
        reached.append(True)
        if k.get("stream"):
            async def gen():
                if False:
                    yield None
            return gen()
        return {"ok": True}

    async def quota_ok(*a, **k):
        return True

    pool = _FakePool() if pool_mode else None
    patches = [
        patch.object(pm, "_resolve_model", resolve_model),
        patch.object(pm, "check_custom_pool_rate", gate),
        patch.object(pm, "acompletion", provider),
        patch.object(router, "resolve_pool", lambda alias: pool),
        patch.object(router, "acquire_quota", quota_ok),
        patch.object(router, "release_key", lambda *a, **k: None),
        patch.object(router, "freeze_key", lambda *a, **k: None),
        patch.object(router, "update_model_health", lambda *a, **k: None),
        patch.object(pm, "apply_error_penalty", lambda *a, **k: None),
        patch.object(pm, "count_transient_error", lambda *a, **k: None),
        patch.object(pm, "_retry_delay", lambda n: 0.0),
    ]
    for p in patches:
        p.start()
    try:
        try:
            if path == "stream":
                async for _ in pm.pool_manager.call_stream(
                    "gemini-flash", [{"role": "user", "content": "hi"}], max_tokens=16
                ):
                    pass
            else:
                await pm.pool_manager.call_nonstream(
                    "gemini-flash", [{"role": "user", "content": "hi"}], max_tokens=16
                )
        except Exception:
            pass
        return bool(reached)
    finally:
        for p in patches:
            p.stop()


ALL_PATHS = [
    ("nonstream", False),
    ("stream", True),
    ("stream", False),
]


@pytest.mark.anyio
@pytest.mark.parametrize("path,pool_mode", ALL_PATHS)
async def test_custom_pool_model_is_rate_limited_on_every_path(path, pool_mode):
    """Over the per-model RPM limit, no path may reach the provider."""
    reached = await _run(path, rate_ok=False, pool_mode=pool_mode)
    assert reached is False, (
        f"{path} (pool_mode={pool_mode}) reached the provider with the rate gate closed — "
        "custom pool models are exempt from their own RPM limit"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("path,pool_mode", ALL_PATHS)
async def test_custom_pool_model_passes_when_under_the_limit(path, pool_mode):
    """Under the limit, the gate must not block — otherwise the fix over-corrects."""
    reached = await _run(path, rate_ok=True, pool_mode=pool_mode)
    assert reached is True, f"{path} (pool_mode={pool_mode}) was blocked while under the limit"


@pytest.mark.anyio
async def test_the_rate_check_runs_before_the_provider():
    """Ordering matters: a gate consulted after acompletion would be decorative."""
    order = []

    async def resolve_model(*a, **k):
        return "gemini-flash", "my-model", "k", "my-model", _custom_reservation()

    async def gate(model_id):
        order.append("gate")
        return False

    async def provider(*a, **k):
        order.append("provider")
        if k.get("stream"):
            async def gen():
                if False:
                    yield None
            return gen()
        return {"ok": True}

    pool = _FakePool()
    patches = [
        patch.object(pm, "_resolve_model", resolve_model),
        patch.object(pm, "check_custom_pool_rate", gate),
        patch.object(pm, "acompletion", provider),
        patch.object(router, "resolve_pool", lambda alias: pool),
        patch.object(router, "acquire_quota", lambda *a, **k: _true()),
        patch.object(router, "release_key", lambda *a, **k: None),
        patch.object(pm, "_retry_delay", lambda n: 0.0),
    ]
    for p in patches:
        p.start()
    try:
        async for _ in pm.pool_manager.call_stream(
            "gemini-flash", [{"role": "user", "content": "hi"}], max_tokens=16
        ):
            pass
    except Exception:
        pass
    finally:
        for p in patches:
            p.stop()

    assert "provider" not in order, (
        f"the provider was reached with the rate gate closed: {order}"
    )
    assert order, "the rate gate was never consulted at all"


async def _true():
    return True


@pytest.mark.anyio
async def test_gemini_keys_are_not_gated_by_the_custom_limit():
    """The gate is per custom model. A Gemini reservation must not touch it."""
    calls = []

    async def resolve_model(*a, **k):
        calls.append("resolve")
        return "gemini-flash", "gemini-3.5-flash", "gkey", "gemini-3.5-flash", {
            "key": "gkey", "name": "m1", "model_alias": "gemini-flash",
            "model_id": "gemini-3.5-flash", "provider": "gemini",
        }

    async def gate(model_id):
        calls.append("gate")
        return False

    async def provider(*a, **k):
        if k.get("stream"):
            async def gen():
                if False:
                    yield None
            return gen()
        return {"ok": True}

    patches = [
        patch.object(pm, "_resolve_model", resolve_model),
        patch.object(pm, "check_custom_pool_rate", gate),
        patch.object(pm, "acompletion", provider),
        patch.object(router, "resolve_pool", lambda alias: _FakePool()),
        patch.object(router, "acquire_quota", lambda *a, **k: _true()),
        patch.object(router, "release_key", lambda *a, **k: None),
        patch.object(router, "update_model_health", lambda *a, **k: None),
    ]
    for p in patches:
        p.start()
    try:
        async for _ in pm.pool_manager.call_stream(
            "gemini-flash", [{"role": "user", "content": "hi"}], max_tokens=16
        ):
            pass
    except Exception:
        pass
    finally:
        for p in patches:
            p.stop()

    assert "gate" not in calls, "a Gemini key was run through the custom endpoint rate limit"


def test_the_limit_itself_is_ten_per_minute():
    """Pins the number the tests above stand in for."""
    assert custom_endpoint_client._CUSTOM_POOL_RPM == 10

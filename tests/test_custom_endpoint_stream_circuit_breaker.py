"""A failing custom endpoint must cool down on the standalone stream path.

A standalone stream reported `Standalone stream failed after retries` while the
same endpoint was simultaneously tripping the circuit breaker hundreds of times
-- "790 consecutive" -- from the pool path. Both paths call the same provider,
so that was only possible if the standalone path never told the breaker at all:

    mark_endpoint_failure  is called from pool_manager.py:223 (non-stream) and
                           :438 (pool stream). The standalone branch -- the
                           `else` at :479, ending in the raise at :548 -- has no
                           call, so it retried a dead endpoint MAX_RETRIES times
                           per request, forever, and every one of those attempts
                           re-checked a rate limiter that had already said no.

Two things follow from that, and both are pinned below: the endpoint has to be
frozen so other requests stop picking it, and the retries have to stop early
instead of burning the full budget on an endpoint that is already known bad.
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import src.core.orchestration.pool_manager as pm
from src.core.providers import _custom_endpoint_manager as endpoint_manager
from src.core.router import router


def _max_retries():
    from src.core.config_n_logg import config
    return config.MAX_RETRIES


async def _run_standalone(*, provider_raises):
    """Drive one standalone stream against a custom reservation that fails.

    Returns (attempts, endpoint_name_or_None) so a test can assert both how many
    times the provider was hit and which endpoint got blamed.
    """
    attempts = []

    async def resolve_model(*a, **k):
        return ("gemini-flash", "my-model", "endpoint-auth-key", "my-model",
                {"key": "endpoint-auth-key", "name": "flaky-endpoint",
                 "model_alias": "gemini-flash", "model_id": "my-model",
                 "provider": "custom", "api_base": "http://endpoint.local/v1"})

    async def gate(model_id, *args, **kwargs):
        return True

    async def provider(*a, **k):
        attempts.append(True)
        # Returns a generator that fails on first pull, so `acompletion` itself
        # succeeds and the failure surfaces inside the `async for`, the way a
        # stream that dies mid-flight does.
        async def gen():
            raise RuntimeError("Custom endpoint HTTP 502: upstream exploded")
            yield  # pragma: no cover - unreachable, marks this a generator
        return gen()

    async def quota_ok(*a, **k):
        return True

    patches = [
        patch.object(pm, "_resolve_model", resolve_model),
        patch.object(pm, "check_custom_pool_rate", gate),
        patch.object(pm, "acompletion", provider),
        patch.object(router, "resolve_pool", lambda alias: None),  # standalone
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
        with pytest.raises(RuntimeError):
            async for _ in pm.pool_manager.call_stream(
                "gemini-flash", [{"role": "user", "content": "hi"}], max_tokens=16
            ):
                pass
        return len(attempts), "flaky-endpoint" in endpoint_manager._circuit_breaker
    finally:
        for p in patches:
            p.stop()


@pytest.mark.anyio
async def test_a_failing_custom_endpoint_is_frozen_on_the_standalone_path():
    """The breaker is the whole point: the endpoint must be marked failed."""
    _attempts, blamed = await _run_standalone(provider_raises=True)

    assert blamed, (
        "standalone stream never called mark_endpoint_failure, so the endpoint "
        "stayed pickable and every request retried it from scratch"
    )


@pytest.mark.anyio
async def test_a_frozen_custom_endpoint_is_not_retried_for_the_full_budget():
    """Retrying a known-dead endpoint MAX_RETRIES times per request is the
    thing that turned one bad endpoint into hundreds of log lines a second."""
    attempts, _blamed = await _run_standalone(provider_raises=True)

    assert attempts < _max_retries(), (
        f"provider hit {attempts} times, the full retry budget; the breaker "
        f"exists precisely so a dead endpoint is not retried like this"
    )


@pytest.mark.anyio
async def test_the_endpoint_is_released_once_the_breaker_has_taken_over():
    """Success clears the blame -- otherwise one failure would freeze it for
    the rest of the process's life once the cooldown is short enough."""
    name = "flaky-endpoint"
    endpoint_manager._circuit_breaker.pop(name, None)

    async def resolve_model(*a, **k):
        return ("gemini-flash", "my-model", "endpoint-auth-key", "my-model",
                {"key": "endpoint-auth-key", "name": name,
                 "model_alias": "gemini-flash", "model_id": "my-model",
                 "provider": "custom", "api_base": "http://endpoint.local/v1"})

    async def gate(model_id, *args, **kwargs):
        return True

    async def provider(*a, **k):
        if k.get("stream"):
            async def gen():
                # A successful stream must carry payload AND a finish signal:
                # _drain_and_validate_stream rejects a turn that never
                # finished, so a bare {"ok": True} is not a success fixture.
                yield {"choices": [{"delta": {"content": "ok"}}]}
                yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            return gen()
        return {"ok": True}

    async def quota_ok(*a, **k):
        return True

    patches = [
        patch.object(pm, "_resolve_model", resolve_model),
        patch.object(pm, "check_custom_pool_rate", gate),
        patch.object(pm, "acompletion", provider),
        patch.object(router, "resolve_pool", lambda alias: None),
        patch.object(router, "acquire_quota", quota_ok),
        patch.object(router, "release_key", lambda *a, **k: None),
        patch.object(router, "freeze_key", lambda *a, **k: None),
        patch.object(pm, "_retry_delay", lambda n: 0.0),
    ]
    for p in patches:
        p.start()
    try:
        for _ in range(3):
            endpoint_manager.mark_endpoint_failure(name)
        assert endpoint_manager.is_endpoint_frozen(name)
        frozen_before = dict(endpoint_manager._circuit_breaker)

        async for _ in pm.pool_manager.call_stream(
            "gemini-flash", [{"role": "user", "content": "hi"}], max_tokens=16
        ):
            pass

        assert not endpoint_manager.is_endpoint_frozen(name), (
            "a successful stream must clear the blame, or the endpoint stays "
            "frozen after recovering and never gets picked again"
        )
    finally:
        for p in patches:
            p.stop()
        endpoint_manager._circuit_breaker.pop(name, None)


@pytest.mark.anyio
async def test_a_failing_custom_endpoint_marks_failure_on_standalone_nonstream_path():
    """Standalone nonstream must mark failure and fail fast without burning retry budget."""
    name = "flaky-endpoint-nonstream"
    endpoint_manager._circuit_breaker.pop(name, None)
    attempts = []

    async def resolve_model(*a, **k):
        return ("gemini-flash", "my-model", "endpoint-auth-key", "my-model",
                {"key": "endpoint-auth-key", "name": name,
                 "model_alias": "gemini-flash", "model_id": "my-model",
                 "provider": "custom", "api_base": "http://endpoint.local/v1"})

    async def gate(model_id, *args, **kwargs):
        return True

    async def provider(*a, **k):
        attempts.append(True)
        raise RuntimeError("Custom endpoint HTTP 500: internal server error")

    patches = [
        patch.object(pm, "_resolve_model", resolve_model),
        patch.object(pm, "check_custom_pool_rate", gate),
        patch.object(pm, "acompletion", provider),
        patch.object(router, "resolve_pool", lambda alias: None),
        patch.object(router, "acquire_quota", lambda *a, **k: True),
        patch.object(router, "release_key", lambda *a, **k: None),
        patch.object(pm, "_retry_delay", lambda n: 0.0),
    ]
    for p in patches:
        p.start()
    try:
        with pytest.raises(RuntimeError):
            await pm.pool_manager.call_nonstream(
                "gemini-flash", [{"role": "user", "content": "hi"}], max_tokens=16
            )
        assert len(attempts) == 1, "custom endpoint should fail fast and not retry full budget"
        assert name in endpoint_manager._circuit_breaker, "custom endpoint failure must be recorded in circuit breaker"
    finally:
        for p in patches:
            p.stop()
        endpoint_manager._circuit_breaker.pop(name, None)


@pytest.mark.anyio
async def test_a_successful_custom_endpoint_clears_breaker_on_nonstream_path():
    """Standalone nonstream success must call mark_endpoint_success to clear circuit breaker."""
    name = "flaky-endpoint-nonstream-success"
    endpoint_manager._circuit_breaker.pop(name, None)

    async def resolve_model(*a, **k):
        return ("gemini-flash", "my-model", "endpoint-auth-key", "my-model",
                {"key": "endpoint-auth-key", "name": name,
                 "model_alias": "gemini-flash", "model_id": "my-model",
                 "provider": "custom", "api_base": "http://endpoint.local/v1"})

    async def gate(model_id, *args, **kwargs):
        return True

    async def provider(*a, **k):
        return {"choices": [{"message": {"content": "ok"}}]}

    patches = [
        patch.object(pm, "_resolve_model", resolve_model),
        patch.object(pm, "check_custom_pool_rate", gate),
        patch.object(pm, "acompletion", provider),
        patch.object(router, "resolve_pool", lambda alias: None),
        patch.object(router, "acquire_quota", lambda *a, **k: True),
        patch.object(router, "release_key", lambda *a, **k: None),
        patch.object(pm, "_retry_delay", lambda n: 0.0),
    ]
    for p in patches:
        p.start()
    try:
        for _ in range(3):
            endpoint_manager.mark_endpoint_failure(name)
        assert endpoint_manager.is_endpoint_frozen(name)

        await pm.pool_manager.call_nonstream(
            "gemini-flash", [{"role": "user", "content": "hi"}], max_tokens=16
        )

        assert not endpoint_manager.is_endpoint_frozen(name), (
            "successful nonstream must clear the circuit breaker"
        )
    finally:
        for p in patches:
            p.stop()
        endpoint_manager._circuit_breaker.pop(name, None)
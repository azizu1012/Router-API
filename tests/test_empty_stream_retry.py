import asyncio
from types import SimpleNamespace

import pytest


def _empty_chunk():
    return SimpleNamespace(candidates=[], usage_metadata=None)


@pytest.mark.anyio
async def test_gemini_empty_stream_is_retried_without_recording_success(monkeypatch):
    import src.core.providers.gemini.manager as manager_module

    api_manager = manager_module.GeminiAPIManager.__new__(manager_module.GeminiAPIManager)
    api_manager.pool = SimpleNamespace(
        pool_size=1,
        resolve_tier=lambda account: "free",
        throttle=lambda *args: asyncio.sleep(0),
        record_key_usage=lambda key: None,
        get_key_last_used=lambda key: 0.0,
        is_key_frozen_by_project=lambda *args: False,
    )
    monkeypatch.setattr(api_manager, "_acquire_quota", lambda *args: asyncio.sleep(0, result=True))
    monkeypatch.setattr(
        api_manager,
        "_get_best_key",
        lambda *args, **kwargs: asyncio.sleep(
            0,
            result=("key", "gemini-flash-38", "gemini-flash", {}),
        ),
    )
    monkeypatch.setattr(api_manager, "_commit_key", lambda *args: None)
    monkeypatch.setattr(manager_module, "wait_global_cooldown", lambda *args: asyncio.sleep(0))
    monkeypatch.setattr(manager_module, "backoff", lambda *args: asyncio.sleep(0))
    monkeypatch.setattr(manager_module.config, "GEMINI_UNAVAILABLE_DELAY_SEC", 0)
    handled = []

    async def fake_handle_error(exc, *args):
        handled.append(str(exc))

    monkeypatch.setattr(manager_module, "handle_error", fake_handle_error)
    monkeypatch.setattr(manager_module.router, "release_key", lambda key: None)
    monkeypatch.setattr(manager_module.router, "reset_429_counter", lambda: None)
    successes = []
    monkeypatch.setattr(
        manager_module.router,
        "record_success",
        lambda *args: successes.append(args),
    )
    monkeypatch.setattr(
        manager_module,
        "extract_stream_usage",
        lambda *args: (0, 0),
    )
    monkeypatch.setattr(
        "src.core.providers.gemini.response_dump.dump_generation_response",
        lambda chunk: {},
    )

    calls = 0

    async def fake_stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return
            yield  # Keep this function an async generator.
        yield _empty_chunk()

    monkeypatch.setattr(manager_module.caller, "generate_content_stream", fake_stream)

    output = []
    async for item in api_manager.call_gemini_stream(
        "gemini-flash",
        "system",
        [],
        128,
    ):
        output.append(item)

    assert calls == 2
    assert handled == ["empty_stream_response"]
    assert len(output) == 1
    assert len(successes) == 1


@pytest.mark.anyio
async def test_pool_stream_retries_an_empty_upstream_before_committing(monkeypatch):
    import src.core.orchestration.pool_manager as pool_module

    pm = pool_module.PoolManager()
    monkeypatch.setattr(pool_module.router, "resolve_pool", lambda alias: None)
    monkeypatch.setattr(
        pool_module,
        "_resolve_model",
        lambda *args, **kwargs: asyncio.sleep(
            0,
            result=(
                "gemini-flash",
                "gemini-flash-38",
                "key",
                "gemini-flash-38",
                {"provider": "gemini", "model_alias": "gemini-flash"},
            ),
        ),
    )
    monkeypatch.setattr(pool_module.router, "acquire_quota", lambda *args: asyncio.sleep(0, result=True))
    monkeypatch.setattr(pool_module.router, "release_key", lambda key: None)
    monkeypatch.setattr(pool_module.router, "freeze_key", lambda *args: None)
    monkeypatch.setattr(pool_module, "apply_error_penalty", lambda *args: None)
    monkeypatch.setattr(pool_module, "count_transient_error", lambda *args: None)
    monkeypatch.setattr(pool_module.router, "record_429", lambda: False)
    monkeypatch.setattr(pool_module, "_retry_delay", lambda attempt: 0)

    calls = 0

    async def fake_acompletion(**kwargs):
        nonlocal calls
        calls += 1

        async def stream():
            if calls > 1:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="ok", reasoning_content=None, tool_calls=[]), finish_reason="stop")]
                )

        return stream()

    monkeypatch.setattr(pool_module, "acompletion", fake_acompletion)

    items = []
    async for item in pm.call_stream(
        "gemini-flash",
        [{"role": "user", "content": "hello"}],
    ):
        items.append(item)

    assert calls == 2
    assert len(items) == 1


@pytest.mark.anyio
async def test_pool_stream_retries_role_only_frames(monkeypatch):
    import src.core.orchestration.pool_manager as pool_module

    pm = pool_module.PoolManager()
    monkeypatch.setattr(pool_module.router, "resolve_pool", lambda alias: None)
    monkeypatch.setattr(
        pool_module,
        "_resolve_model",
        lambda *args, **kwargs: asyncio.sleep(
            0,
            result=("gemini-flash", "gemini-flash-38", "key", "gemini-flash-38", {"provider": "gemini"}),
        ),
    )
    monkeypatch.setattr(pool_module.router, "acquire_quota", lambda *args: asyncio.sleep(0, result=True))
    monkeypatch.setattr(pool_module.router, "release_key", lambda key: None)
    monkeypatch.setattr(pool_module.router, "freeze_key", lambda *args: None)
    monkeypatch.setattr(pool_module, "apply_error_penalty", lambda *args: None)
    monkeypatch.setattr(pool_module, "count_transient_error", lambda *args: None)
    monkeypatch.setattr(pool_module.router, "record_429", lambda: False)
    monkeypatch.setattr(pool_module, "_retry_delay", lambda attempt: 0)

    calls = 0

    async def fake_acompletion(**kwargs):
        nonlocal calls
        calls += 1

        async def stream():
            if calls == 1:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=None, reasoning_content=None, tool_calls=[]))]
                )
            else:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="ok", reasoning_content=None, tool_calls=[]), finish_reason="stop")]
                )

        return stream()

    monkeypatch.setattr(pool_module, "acompletion", fake_acompletion)

    items = []
    async for item in pm.call_stream("gemini-flash", [{"role": "user", "content": "hello"}]):
        items.append(item)

    assert calls == 2
    assert len(items) == 1


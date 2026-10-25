import asyncio

import pytest

from src.core.runtime.request_session import RequestSessionManager


@pytest.mark.anyio
async def test_disconnect_does_not_cancel_provider_and_reconnect_replays():
    manager = RequestSessionManager()
    started = asyncio.Event()

    async def producer():
        started.set()
        yield b"event: message_start\ndata: {}\n\n"
        await asyncio.sleep(0.01)
        yield b"event: message_stop\ndata: {}\n\n"

    session = await manager.get_or_start("req-test", producer)
    await started.wait()
    first = [frame async for frame in session.stream(0)]
    assert len(first) == 2
    assert b"id: req-test:1\n" in first[0]
    assert session.state == "completed"

    replay = [frame async for frame in session.stream(1)]
    assert len(replay) == 1
    assert b"message_stop" in replay[0]
    assert "req-test" not in manager._sessions


@pytest.mark.anyio
async def test_same_request_id_reuses_running_provider_task():
    manager = RequestSessionManager()
    calls = 0

    async def producer():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        yield b"event: message_stop\ndata: {}\n\n"

    first = await manager.get_or_start("req-same", producer)
    second = await manager.get_or_start("req-same", producer)
    assert first is second
    await asyncio.sleep(0.05)
    assert calls == 1
    assert "req-same" not in manager._sessions


@pytest.mark.anyio
async def test_completed_session_is_not_retained_but_existing_stream_can_drain():
    manager = RequestSessionManager()

    async def producer():
        yield b"event: message_start\ndata: {}\n\n"
        yield b"event: message_stop\ndata: {}\n\n"

    session = await manager.get_or_start("req-drain", producer)
    # Consume after the producer is done, as can happen when a client reads
    # more slowly than the provider produces frames.
    await session.task

    frames = [frame async for frame in session.stream(0)]
    assert len(frames) == 2
    assert "req-drain" not in manager._sessions

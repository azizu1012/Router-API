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

    session, is_reconnect = await manager.get_or_start("req-test", producer)
    assert not is_reconnect
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

    first, first_reconnect = await manager.get_or_start("req-same", producer)
    second, second_reconnect = await manager.get_or_start(
        "req-same", producer, resume_after=1,
    )
    assert not first_reconnect
    assert second_reconnect
    assert first is second
    await asyncio.sleep(0.05)
    assert calls == 1
    assert "req-same" not in manager._sessions


@pytest.mark.anyio
async def test_same_request_id_with_different_body_starts_a_separate_stream():
    manager = RequestSessionManager()
    started = asyncio.Event()
    release = asyncio.Event()

    async def first_producer():
        started.set()
        yield b"event: message_start\ndata: {\"turn\":1}\n\n"
        await release.wait()
        yield b"event: message_stop\ndata: {}\n\n"

    async def second_producer():
        yield b"event: message_start\ndata: {\"turn\":2}\n\n"
        yield b"event: message_stop\ndata: {}\n\n"

    first, first_reconnect = await manager.get_or_start(
        "req-reused", first_producer, request_fingerprint="body-1",
    )
    assert not first_reconnect
    await started.wait()
    second, second_reconnect = await manager.get_or_start(
        "req-reused", second_producer, request_fingerprint="body-2",
        resume_after=1,
    )

    assert not second_reconnect
    assert second is not first
    frames = [frame async for frame in second.stream(0)]
    assert len(frames) == 2
    assert b'"turn":2' in frames[0]
    assert b'"turn":1' not in b"".join(frames)

    release.set()
    await first.task


@pytest.mark.anyio
async def test_new_session_ignores_previous_request_last_event_offset():
    manager = RequestSessionManager()
    release = asyncio.Event()

    async def old_producer():
        yield b"event: message_start\ndata: {\"turn\":1}\n\n"
        await release.wait()

    async def new_producer():
        yield b"event: message_start\ndata: {\"turn\":2}\n\n"
        yield b"event: message_stop\ndata: {}\n\n"

    old, _ = await manager.get_or_start(
        "req-reused", old_producer, request_fingerprint="body-1",
    )
    await asyncio.sleep(0)
    new, is_reconnect = await manager.get_or_start(
        "req-reused", new_producer, request_fingerprint="body-2",
        resume_after=100,
    )
    assert not is_reconnect

    # Route behavior: Last-Event-ID only applies when the manager reused the
    # same producer; new requests must start at the beginning of their stream.
    frames = [frame async for frame in new.stream(0)]
    assert any(b'"turn":2' in frame for frame in frames)

    release.set()
    await old.task


@pytest.mark.anyio
async def test_same_id_and_body_without_resume_marker_starts_new_turn():
    manager = RequestSessionManager()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def producer():
        nonlocal calls
        calls += 1
        started.set()
        yield b"event: message_start\ndata: {}\n\n"
        await release.wait()

    first, first_reconnect = await manager.get_or_start(
        "req-no-resume", producer, request_fingerprint="same-body",
    )
    await started.wait()
    second, second_reconnect = await manager.get_or_start(
        "req-no-resume", producer, request_fingerprint="same-body",
    )
    assert not first_reconnect
    assert not second_reconnect
    assert second is not first
    await asyncio.sleep(0)
    assert calls == 2
    release.set()
    await asyncio.gather(first.task, second.task)


@pytest.mark.anyio
async def test_completed_session_is_not_retained_but_existing_stream_can_drain():
    manager = RequestSessionManager()

    async def producer():
        yield b"event: message_start\ndata: {}\n\n"
        yield b"event: message_stop\ndata: {}\n\n"

    session, is_reconnect = await manager.get_or_start("req-drain", producer)
    assert not is_reconnect
    # Consume after the producer is done, as can happen when a client reads
    # more slowly than the provider produces frames.
    await session.task

    frames = [frame async for frame in session.stream(0)]
    assert len(frames) == 2
    assert "req-drain" not in manager._sessions

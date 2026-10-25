"""In-memory resumable sessions for long-running streamed requests."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Dict, Optional

SESSION_TTL = 900.0
REPLAY_LIMIT = 500


@dataclass
class RequestSession:
    request_id: str
    state: str = "running"
    last_event_id: int = 0
    updated_at: float = field(default_factory=time.monotonic)
    events: list[tuple[int, bytes]] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: Optional[asyncio.Task] = None

    def alive(self) -> bool:
        return time.monotonic() - self.updated_at < SESSION_TTL

    async def publish(self, frame: bytes) -> None:
        self.last_event_id += 1
        frame = f"id: {self.request_id}:{self.last_event_id}\n".encode() + frame
        self.events.append((self.last_event_id, frame))
        del self.events[:-REPLAY_LIMIT]
        self.updated_at = time.monotonic()
        for queue in tuple(self.subscribers):
            await queue.put((self.last_event_id, frame))

    async def stream(self, after: int) -> AsyncIterator[bytes]:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(queue)
        try:
            sent = after
            for event_id, frame in self.events:
                if event_id > sent:
                    sent = event_id
                    yield frame
            while self.state == "running" or not queue.empty():
                item = await queue.get()
                if item is None:
                    break
                event_id, frame = item
                if event_id > sent:
                    sent = event_id
                    yield frame
        finally:
            self.subscribers.discard(queue)


class RequestSessionManager:
    def __init__(self) -> None:
        self._sessions: Dict[str, RequestSession] = {}
        self._lock = asyncio.Lock()

    async def get_or_start(
        self, request_id: str, producer: Callable[[], AsyncIterator[bytes]]
    ) -> RequestSession:
        async with self._lock:
            current = self._sessions.get(request_id)
            if current and current.alive() and current.task and not current.task.done():
                return current
            session = RequestSession(request_id=request_id)
            self._sessions[request_id] = session
            session.task = asyncio.create_task(self._run(session, producer))
            return session

    async def _run(
        self, session: RequestSession, producer: Callable[[], AsyncIterator[bytes]]
    ) -> None:
        try:
            async for frame in producer():
                await session.publish(frame)
            session.state = "completed"
        except asyncio.CancelledError:
            session.state = "cancelled"
            raise
        except Exception:
            session.state = "failed"
        finally:
            session.updated_at = time.monotonic()
            for queue in tuple(session.subscribers):
                await queue.put(None)
            # The manager only reuses sessions while their producer is still
            # running. A completed session is never selected for reconnect by
            # get_or_start(); retaining it here only pins its replay frames and
            # task for the lifetime of the process. Active stream consumers
            # hold their own reference, so removing the registry entry does not
            # affect frames they are still draining.
            async with self._lock:
                if self._sessions.get(session.request_id) is session:
                    self._sessions.pop(session.request_id, None)


request_sessions = RequestSessionManager()

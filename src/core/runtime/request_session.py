"""In-memory resumable sessions for long-running streamed requests."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Dict, Optional

SESSION_TTL = 900.0
REPLAY_LIMIT = 500
logger = logging.getLogger(__name__)


def _frame_metadata(frame: bytes) -> tuple[str, Optional[int], Optional[str], int]:
    """Extract safe SSE metadata without retaining/logging response content."""
    event_name = "unknown"
    block_index = None
    block_type = None
    text_chars = 0
    try:
        decoded = frame.decode("utf-8", errors="replace")
        for line in decoded.splitlines():
            if line.startswith("event: "):
                event_name = line[7:].strip()
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                if isinstance(payload, dict):
                    block_index = payload.get("index")
                    delta = payload.get("delta") or {}
                    block = payload.get("content_block") or {}
                    block_type = delta.get("type") or block.get("type")
                    if block_type == "text_delta":
                        text_chars = len(str(delta.get("text") or ""))
                    elif block_type == "text":
                        text_chars = len(str(block.get("text") or ""))
    except (ValueError, TypeError):
        pass
    return event_name, block_index, block_type, text_chars


@dataclass
class RequestSession:
    request_id: str
    request_fingerprint: str = ""
    state: str = "running"
    last_event_id: int = 0
    updated_at: float = field(default_factory=time.monotonic)
    events: list[tuple[int, bytes]] = field(default_factory=list)
    event_counts: Dict[str, int] = field(default_factory=dict)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: Optional[asyncio.Task] = None

    def alive(self) -> bool:
        return time.monotonic() - self.updated_at < SESSION_TTL

    async def publish(self, frame: bytes) -> None:
        event_name, block_index, block_type, text_chars = _frame_metadata(frame)
        self.last_event_id += 1
        self.event_counts[event_name] = self.event_counts.get(event_name, 0) + 1
        frame = f"id: {self.request_id}:{self.last_event_id}\n".encode() + frame
        self.events.append((self.last_event_id, frame))
        del self.events[:-REPLAY_LIMIT]
        self.updated_at = time.monotonic()
        logger.info(
            "[Claude Trace] stage=publish request_id=%s event_id=%d event=%s "
            "block_index=%s block_type=%s text_chars=%d frame_bytes=%d subscribers=%d",
            self.request_id, self.last_event_id, event_name, block_index,
            block_type, text_chars, len(frame), len(self.subscribers),
        )
        for queue in tuple(self.subscribers):
            await queue.put((self.last_event_id, frame))

    async def stream(self, after: int) -> AsyncIterator[bytes]:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(queue)
        logger.info(
            "[Claude Trace] stage=client_subscribe request_id=%s after=%d "
            "state=%s buffered=%d subscribers=%d",
            self.request_id, after, self.state, self.last_event_id,
            len(self.subscribers),
        )
        try:
            sent = after
            for event_id, frame in self.events:
                if event_id > sent:
                    sent = event_id
                    event_name, block_index, block_type, text_chars = _frame_metadata(frame)
                    logger.info(
                        "[Claude Trace] stage=client_yield request_id=%s event_id=%d "
                        "event=%s block_index=%s block_type=%s text_chars=%d "
                        "frame_bytes=%d source=replay",
                        self.request_id, event_id, event_name, block_index,
                        block_type, text_chars, len(frame),
                    )
                    yield frame
            while self.state == "running" or not queue.empty():
                item = await queue.get()
                if item is None:
                    break
                event_id, frame = item
                if event_id > sent:
                    sent = event_id
                    event_name, block_index, block_type, text_chars = _frame_metadata(frame)
                    logger.info(
                        "[Claude Trace] stage=client_yield request_id=%s event_id=%d "
                        "event=%s block_index=%s block_type=%s text_chars=%d "
                        "frame_bytes=%d source=live",
                        self.request_id, event_id, event_name, block_index,
                        block_type, text_chars, len(frame),
                    )
                    yield frame
        finally:
            self.subscribers.discard(queue)


class RequestSessionManager:
    def __init__(self) -> None:
        self._sessions: Dict[str, RequestSession] = {}
        self._lock = asyncio.Lock()

    async def get_or_start(
        self, request_id: str, producer: Callable[[], AsyncIterator[bytes]],
        request_fingerprint: str = "",
        resume_after: Optional[int] = None,
    ) -> tuple[RequestSession, bool]:
        async with self._lock:
            current = self._sessions.get(request_id)
            if (resume_after is not None
                    and current and current.request_fingerprint == request_fingerprint
                    and current.alive() and current.task and not current.task.done()):
                return current, True
            session = RequestSession(
                request_id=request_id,
                request_fingerprint=request_fingerprint,
            )
            self._sessions[request_id] = session
            session.task = asyncio.create_task(self._run(session, producer))
            return session, False

    async def _run(
        self, session: RequestSession, producer: Callable[[], AsyncIterator[bytes]]
    ) -> None:
        try:
            async for frame in producer():
                await session.publish(frame)
            session.state = "completed"
            logger.info(
                "[Claude Trace] stage=producer_end request_id=%s state=%s events=%d event_counts=%s",
                session.request_id, session.state, session.last_event_id,
                session.event_counts,
            )
        except asyncio.CancelledError:
            session.state = "cancelled"
            raise
        except Exception:
            session.state = "failed"
            logger.exception(
                "[Claude Trace] stage=producer_end request_id=%s state=failed published_events=%d event_counts=%s",
                session.request_id,
                session.last_event_id,
                session.event_counts,
            )
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

"""Shared vocabulary for provider stream events.

This module is types only: nothing produces these events yet and nothing
consumes them. It exists so a single naming of "what a stream can say" is
pinned before the three stream paths (pool, standalone, custom endpoint) start
speaking it — inventing a second vocabulary later is how the same frame ends up
with two shapes.

Why a dataclass and not a union of payload classes: the frames that exist today
already carry a flat dict (``chunk`` / ``api_key`` / ``model_id`` /
``input_tokens`` / ``reservation``). A frozen flat event lets a producer fill
only the field its event type owns, and lets ``is_terminal`` stay a pure
function over the type instead of an isinstance ladder.

No imports beyond the standard library, no config, no I/O. Same rule as
``sse_format`` and ``sub_agent_detect``: a leaf both ``src/core/`` and
``src/api/`` may import without creating a layering violation.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class StreamEventType(str, Enum):
    """Every kind of thing a provider stream can report.

    ``str`` mixin so a frame can be serialised without a separate encoder.
    """

    DATA = "data"
    """A content delta the client should render."""

    TOOL_CALL = "tool_call"
    """A complete tool/function call, emitted only once fully assembled.

    Fragments are never forwarded: the producer buffers argument shards until
    id + name + JSON-valid arguments are all present, then emits exactly one
    event. A consumer that receives TOOL_CALL may execute it without
    re-validating the arguments.
    """

    USAGE = "usage"
    """Token accounting for the turn (prompt/completion counts)."""

    HEARTBEAT = "heartbeat"
    """Liveness only. Carries no content; a consumer that ignores it is correct."""

    FINISH = "finish"
    """The turn completed and ``reason`` says how (``stop``, ``length``, ...)."""

    ERROR = "error"
    """The provider reported a failure. ``data`` holds the detail."""

    ABNORMAL_EOF = "abnormal_eof"
    """The stream ended without a finish reason.

    Distinct from ``ERROR`` on purpose: no provider error was raised, the
    iterator simply stopped. Today that distinction lives in the string
    ``stream_ended_without_finish_reason``, which callers match by text.
    """


@dataclass(frozen=True)
class StreamEvent:
    """One frame of a provider stream.

    Exactly one payload field is meaningful per ``type``; the rest stay ``None``
    rather than forcing a class per event. Frozen because a frame describes
    something that already happened — mutating it would be a bug, not a feature.
    """

    type: StreamEventType
    data: Optional[Any] = None
    """Raw chunk or error detail, per ``type``."""

    tool_call: Optional[dict] = None
    """Tool call delta for ``TOOL_CALL``."""

    usage: Optional[dict] = None
    """Prompt/completion token counts for ``USAGE``."""

    reason: Optional[str] = None
    """Finish reason for ``FINISH``."""


def is_terminal(event: StreamEvent) -> bool:
    """Return ``True`` when nothing may follow ``event`` on the stream.

    Heartbeats, data and usage are all non-terminal: a client that stops reading
    on one of those truncates a turn that had more to say.
    """
    return event.type in (
        StreamEventType.FINISH,
        StreamEventType.ERROR,
        StreamEventType.ABNORMAL_EOF,
    )

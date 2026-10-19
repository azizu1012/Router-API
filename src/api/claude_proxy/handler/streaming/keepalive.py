"""Keepalive wrapper for slow provider streams."""

import asyncio
import time
from typing import Any, AsyncIterator, Optional

from src.core.config_n_logg.logger import logger_api as logger


KEEPALIVE_INTERVAL = 3.0
_KEEPALIVE = object()
_POLL_TIMEOUT = 1.0

# A provider that stops sending anything is not slow, it is gone. Without a
# bound the loop pings the client forever: the connection stays healthy, so the
# client keeps waiting for content that will never arrive, and it holds a
# coroutine on the event loop while it does. That is a whole-process stall, not
# one bad request.
#
# This counts *silence*, not duration. Every chunk resets the clock; only a
# window in which not one item arrives counts against it. The pool allows thinking
# models and multi-key retry rotations, so 120s avoids premature stream disconnects
# while keeping sockets alive via periodic pings.
STREAM_STALL_TIMEOUT = 120.0


class StreamStalled(asyncio.TimeoutError):
    """No item arrived from the provider within ``STREAM_STALL_TIMEOUT``.

    A ``TimeoutError`` subclass so the pool's existing classifier already reads
    it as ``timeout`` — a transient error that rotates the member instead of
    being reported to the client as a failure.
    """


async def iter_with_keepalive(
    stream: Any,
    keepalive_interval: float,
    stall_timeout: Optional[float] = None,
) -> AsyncIterator[Any]:
    """Yield stream items and a sentinel while one pull remains in flight.

    A keepalive is a promise that something is still coming. It must stop
    promising once nothing is: otherwise a provider that has stopped responding
    is indistinguishable from a slow one, and the client is told to keep waiting
    on a stream that will never deliver.
    """
    limit = STREAM_STALL_TIMEOUT if stall_timeout is None else stall_timeout
    aiter_ = stream.__aiter__()
    pending: asyncio.Future = asyncio.ensure_future(aiter_.__anext__())
    last_emit = time.monotonic()
    last_item = time.monotonic()
    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    asyncio.shield(pending), timeout=_POLL_TIMEOUT
                )
            except asyncio.TimeoutError:
                now = time.monotonic()
                if now - last_item >= limit:
                    raise StreamStalled(
                        f"provider sent nothing for {now - last_item:.0f}s"
                    ) from None
                if now - last_emit >= keepalive_interval:
                    last_emit = now
                    yield _KEEPALIVE
                continue
            except StopAsyncIteration:
                return
            yield item
            last_emit = time.monotonic()
            last_item = last_emit
            pending = asyncio.ensure_future(aiter_.__anext__())
    finally:
        # `shield` intentionally keeps the pull alive across polling timeouts,
        # so cancellation must explicitly stop it or the pool slot can leak.
        if pending is not None:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

        close = getattr(aiter_, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                # Provider cleanup is best effort; preserve the original
                # cancellation or stream exception for the caller.
                pass


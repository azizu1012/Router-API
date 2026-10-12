"""Keepalive wrapper for slow provider streams."""

import asyncio
import time
from typing import Any, AsyncIterator


KEEPALIVE_INTERVAL = 4.0
_KEEPALIVE = object()
_POLL_TIMEOUT = 1.0


async def iter_with_keepalive(
    stream: Any, keepalive_interval: float
) -> AsyncIterator[Any]:
    """Yield stream items and a sentinel while one pull remains in flight."""
    aiter_ = stream.__aiter__()
    pending: asyncio.Future = asyncio.ensure_future(aiter_.__anext__())
    last_emit = time.monotonic()
    while True:
        try:
            item = await asyncio.wait_for(
                asyncio.shield(pending), timeout=_POLL_TIMEOUT
            )
        except asyncio.TimeoutError:
            if time.monotonic() - last_emit >= keepalive_interval:
                last_emit = time.monotonic()
                yield _KEEPALIVE
            continue
        except StopAsyncIteration:
            return
        yield item
        last_emit = time.monotonic()
        pending = asyncio.ensure_future(aiter_.__anext__())


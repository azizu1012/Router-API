import asyncio
import json
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket

# A subscriber that has not accepted a frame within this long is not keeping
# up. It is disconnected rather than waited on: the log feed pushes one event
# per line, so a stalled reader would otherwise stall every other reader too.
_SEND_TIMEOUT = 5.0


class ConnectionManager:
    def __init__(self):
        self._channels: Dict[str, Set[WebSocket]] = {}
        self._ws_channels: Dict[WebSocket, Set[str]] = {}
        self._ws_account: Dict[WebSocket, str] = {}
        self._ws_info: Dict[WebSocket, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, account_id: str, info: Optional[Dict[str, Any]] = None) -> None:
        await ws.accept()
        async with self._lock:
            self._ws_channels[ws] = set()
            self._ws_account[ws] = account_id
            self._ws_info[ws] = info or {}

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            channels = self._ws_channels.pop(ws, set())
            self._ws_account.pop(ws, None)
            self._ws_info.pop(ws, None)
            for ch in channels:
                subs = self._channels.get(ch)
                if subs:
                    subs.discard(ws)
                    if not subs:
                        del self._channels[ch]

    async def subscribe(self, ws: WebSocket, channel: str) -> None:
        async with self._lock:
            self._channels.setdefault(channel, set()).add(ws)
            self._ws_channels.setdefault(ws, set()).add(channel)

    async def unsubscribe(self, ws: WebSocket, channel: str) -> None:
        async with self._lock:
            ws_chs = self._ws_channels.get(ws)
            if ws_chs:
                ws_chs.discard(channel)
            subs = self._channels.get(channel)
            if subs:
                subs.discard(ws)
                if not subs:
                    del self._channels[channel]

    async def broadcast(self, channel: str, message: dict) -> int:  # noqa: D401
        """Fan a message out to every subscriber of a channel.

        Sends run concurrently and under a timeout. Awaiting them one at a time
        meant a single slow reader held the loop for as long as its TCP window
        stayed open, and because the log watcher awaits broadcast per line it
        could not read another line meanwhile — one slow dashboard tab could
        stop the whole feed and, with it, the event loop every request shares.
        A subscriber that cannot keep up is dropped rather than allowed to
        decide the latency for everyone else.
        """
        payload = json.dumps(message, ensure_ascii=False, default=str)
        sent = 0
        async with self._lock:
            subs = list(self._channels.get(channel, set()))
        if not subs:
            return 0

        results = await asyncio.gather(
            *(self._send_one(ws, payload) for ws in subs),
            return_exceptions=True,
        )
        for ws, ok in zip(subs, results):
            if ok is True:
                sent += 1
            elif ok is not True:
                await self.disconnect(ws)
        return sent

    async def _send_one(self, ws: WebSocket, payload: str) -> bool:
        try:
            await asyncio.wait_for(ws.send_text(payload), timeout=_SEND_TIMEOUT)
            return True
        except Exception:
            return False

    async def send_to(self, ws: WebSocket, message: dict) -> bool:
        payload = json.dumps(message, ensure_ascii=False, default=str)
        return await self._send_one(ws, payload)

    def get_subscribers(self, channel: str) -> List[WebSocket]:
        return list(self._channels.get(channel, set()))

    def get_account_id(self, ws: WebSocket) -> Optional[str]:
        return self._ws_account.get(ws)

    def get_channel_count(self) -> int:
        return len(self._channels)

    def get_connection_count(self) -> int:
        return len(self._ws_channels)


ws_manager = ConnectionManager()


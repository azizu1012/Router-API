import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

from src.core.config_n_logg.logger import logger_system as logger


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

    async def broadcast(self, channel: str, message: dict) -> int:
        payload = json.dumps(message, ensure_ascii=False, default=str)
        sent = 0
        async with self._lock:
            subs = list(self._channels.get(channel, set()))
        for ws in subs:
            try:
                await ws.send_text(payload)
                sent += 1
            except Exception:
                await self.disconnect(ws)
        return sent

    async def send_to(self, ws: WebSocket, message: dict) -> bool:
        try:
            await ws.send_text(json.dumps(message, ensure_ascii=False, default=str))
            return True
        except Exception:
            await self.disconnect(ws)
            return False

    def get_subscribers(self, channel: str) -> List[WebSocket]:
        return list(self._channels.get(channel, set()))

    def get_account_id(self, ws: WebSocket) -> Optional[str]:
        return self._ws_account.get(ws)

    def get_channel_count(self) -> int:
        return len(self._channels)

    def get_connection_count(self) -> int:
        return len(self._ws_channels)


ws_manager = ConnectionManager()

import asyncio
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.core.config_n_logg.logger import logger_system as logger
from src.server.websocket_manager import ws_manager


_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"

_DEFAULT_FILES = {
    "proxy": "proxy.log",
    "system": "system.log",
    "api": "api_calls.log",
    "keys": "keys.log",
    "web": "web.log",
}


class LogWatcher:
    def __init__(self, buffer_size: int = 10000):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._buffers: Dict[str, deque] = {}
        self._buffer_size = buffer_size

    async def watch_file(self, logical_name: str, filename: str) -> None:
        filepath = _LOG_DIR / filename
        if not filepath.exists():
            logger.warning("[LogWatcher] File not found: %s, skipping", filepath)
            return

        channel = f"log:{logical_name}"
        buffer = deque(maxlen=self._buffer_size)
        self._buffers[channel] = buffer

        logger.info("[LogWatcher] Watching %s → channel %s", filepath, channel)

        with open(filepath, "r", encoding="utf-8") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    await asyncio.sleep(0.2)
                    continue
                line = line.rstrip("\n\r")
                buffer.append(line)
                await ws_manager.broadcast(channel, {
                    "type": "log",
                    "channel": channel,
                    "file": logical_name,
                    "ts": datetime.now().isoformat(),
                    "line": line,
                })

    def start_all(self) -> None:
        for name, fname in _DEFAULT_FILES.items():
            task = asyncio.create_task(self.watch_file(name, fname))
            self._tasks[name] = task

    def stop_all(self) -> None:
        for name, task in self._tasks.items():
            task.cancel()
        self._tasks.clear()

    def get_history(self, channel: str, lines: int = 200) -> List[str]:
        buffer = self._buffers.get(channel)
        if not buffer:
            return []
        return list(buffer)[-lines:]


log_watcher = LogWatcher()

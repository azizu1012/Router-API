import asyncio
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List

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


def normalize_channel(channel: str) -> str:
    """Normalize 'log:proxy', 'proxy.log', or 'proxy' into 'proxy'."""
    raw = str(channel or "").strip().lower()
    if raw.startswith("log:"):
        raw = raw[4:]
    if raw.endswith(".log"):
        raw = raw[:-4]
    if ":" in raw:
        prefix = raw.split(":", 1)[0]
        if prefix in _DEFAULT_FILES:
            return prefix
    return raw.strip()


def _read_tail(filepath: Path, max_lines: int = 1000) -> List[str]:
    """Read the last N lines from a log file on disk."""
    if not filepath.exists() or not filepath.is_file():
        return []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            return [line.rstrip("\r\n") for line in lines[-max_lines:]]
    except Exception as e:
        logger.warning("[LogWatcher] Failed to read tail of %s: %s", filepath, e)
        return []


class LogWatcher:
    def __init__(self, buffer_size: int = 10000, log_dir: Path = _LOG_DIR):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._buffers: Dict[str, deque] = {}
        self._buffer_size = buffer_size
        self._log_dir = log_dir

    async def watch_file(self, logical_name: str, filename: str) -> None:
        filepath = self._log_dir / filename
        if not filepath.exists():
            logger.warning("[LogWatcher] File not found: %s, skipping", filepath)
            return

        channel = f"log:{logical_name}"
        buffer = deque(maxlen=self._buffer_size)
        # Pre-populate history from disk so recent logs are immediately available
        initial_lines = _read_tail(filepath, max_lines=min(self._buffer_size, 1000))
        buffer.extend(initial_lines)
        self._buffers[channel] = buffer
        self._buffers[logical_name] = buffer

        logger.info("[LogWatcher] Watching %s (pre-loaded %d lines) → channel %s",
                    filepath, len(initial_lines), channel)

        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
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
        norm = normalize_channel(channel)
        buffer = self._buffers.get(f"log:{norm}") or self._buffers.get(norm)
        if buffer and len(buffer) >= lines:
            return list(buffer)[-lines:]
        # Fallback to reading disk if buffer has fewer lines or watcher not yet started
        fname = _DEFAULT_FILES.get(norm)
        if fname:
            tail = _read_tail(self._log_dir / fname, lines)
            if tail:
                return tail
        return list(buffer)[-lines:] if buffer else []


log_watcher = LogWatcher()


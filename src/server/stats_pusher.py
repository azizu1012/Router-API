import asyncio
import time
from typing import Optional

from src.core.config_n_logg.logger import logger_system as logger
from src.core.limits.gemini_rate_limiter import _rate_limiters, _key_model_requests, _score_penalties
from src.server.websocket_manager import ws_manager


class StatsPusher:
    def __init__(self, interval: float = 2.0):
        self._interval = interval
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def _snapshot(self) -> dict:
        now = time.time()
        model_stats = {}
        for alias, limiter in dict(_rate_limiters).items():
            rpm_remaining = max(0, limiter.rpm_limit - len(limiter._minute_req_ts))
            tpm_used = sum(t for ts, t in limiter._minute_tokens if now - ts < 60)
            tpm_remaining = max(0, limiter.tpm_limit - tpm_used)
            model_stats[alias] = {
                "rpm_remaining": rpm_remaining,
                "rpm_limit": limiter.rpm_limit,
                "tpm_remaining": tpm_remaining,
                "tpm_limit": limiter.tpm_limit,
                "rpd_used": limiter._rpd_count,
                "rpd_limit": limiter.rpd_limit,
                "active_requests": len(limiter._minute_req_ts),
            }

        active_keys = 0
        for km, reqs in dict(_key_model_requests).items():
            cutoff = now - 60
            active = sum(1 for t in reqs if t > cutoff - 5)
            active_keys += active

        penalty_count = 0
        for pk, pdata in dict(_score_penalties).items():
            if pdata.get("expires", 0) > now:
                penalty_count += 1

        return {
            "type": "stats_snapshot",
            "timestamp": int(now * 1000),
            "models": model_stats,
            "connections": ws_manager.get_connection_count(),
            "channels": ws_manager.get_channel_count(),
            "active_keys": active_keys,
            "penalties": penalty_count,
        }

    async def _loop(self):
        self._running = True
        logger.info("[StatsPusher] Started (interval=%.1fs)", self._interval)
        while self._running:
            try:
                data = await self._snapshot()
                await ws_manager.broadcast("stats:overview", data)
            except Exception as e:
                logger.warning("[StatsPusher] Snapshot error: %s", e)
            await asyncio.sleep(self._interval)

    def start(self):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


stats_pusher = StatsPusher()

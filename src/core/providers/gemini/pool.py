import asyncio
import random
import time
from typing import Any, Dict, Optional

from google import genai

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_keys as logger


class ClientPool:
    """Manages GenAI client instances, per-tier concurrency semaphores,
    per-key throttling intervals, and project-level freeze state.

    Responsibilities:
    - Cache GenAI ``Client(api_key=…)`` instances — reuse, LRU eviction.
    - Enforce per-tier concurrency cap via ``asyncio.Semaphore``.
    - Pace API calls with global + per-key jittered intervals.
    - Track project-level freeze (all keys sharing a GCP project).

    Tier limits (concurrent requests):
    - admin: 6
    - premium: 4
    - free: 2
    """

    _semaphores: Dict[str, asyncio.Semaphore] = {}
    TIER_LIMITS = {"admin": 6, "premium": 4, "free": 2}

    def __init__(self) -> None:
        self._clients: Dict[str, Any] = {}
        self._client_lock: Optional[asyncio.Lock] = None
        self._max_clients = max(100, len(config.GEMINI_API_KEYS) * 2)

        self._last_api_time: float = 0.0
        self._key_last_used: Dict[str, float] = {}
        self._throttle_lock = asyncio.Lock()

        self._project_id_of_key: Dict[str, str] = {}
        self._project_frozen_until: Dict[tuple[str, str], float] = {}
        self._pool_size: int = len(config.GEMINI_API_KEYS) if config.GEMINI_API_KEYS else 101

    # ── Semaphore (class-level, shared across instances) ──────────

    @classmethod
    def get_semaphore(cls, tier: str = "free") -> asyncio.Semaphore:
        tier = tier if tier in cls.TIER_LIMITS else "free"
        if tier not in cls._semaphores:
            cls._semaphores[tier] = asyncio.Semaphore(cls.TIER_LIMITS[tier])
        return cls._semaphores[tier]

    # ── Client cache ─────────────────────────────────────────────

    async def get_client(self, api_key: str) -> Any:
        if self._client_lock is None:
            self._client_lock = asyncio.Lock()
        async with self._client_lock:
            if api_key not in self._clients:
                if len(self._clients) >= self._max_clients:
                    self._clients.pop(next(iter(self._clients)))
                self._clients[api_key] = genai.Client(api_key=api_key)
            return self._clients[api_key]

    async def close_all(self) -> None:
        if self._client_lock:
            async with self._client_lock:
                self._clients.clear()

    def refresh_pool_size(self) -> None:
        self._pool_size = len(config.GEMINI_API_KEYS) if config.GEMINI_API_KEYS else 10
        ClientPool._semaphores.clear()
        logger.info("Gemini pool size refreshed: %d", self._pool_size)

    @property
    def pool_size(self) -> int:
        return self._pool_size

    # ── API throttling / pacing ──────────────────────────────────

    async def throttle(self, api_key: str, last_used: float) -> None:
        """Apply global + per-key jittered interval before the next API call."""
        async with self._throttle_lock:
            now = time.time()

            global_interval = max(0.2, config.GEMINI_API_GLOBAL_INTERVAL)
            jitter = random.uniform(-global_interval * 0.4, global_interval * 0.4)
            target_global = max(0.2, global_interval + jitter)
            since_last_global = now - self._last_api_time
            if since_last_global < target_global:
                await asyncio.sleep(target_global - since_last_global)
            self._last_api_time = time.time()
            # Lưu lại thời gian gốc cho key-interval bên dưới
            now = time.time()

        key_interval = max(0.3, config.GEMINI_API_KEY_INTERVAL)
        key_jitter = random.uniform(-key_interval * 0.3, key_interval * 0.3)
        target_key = max(0.3, key_interval + key_jitter)
        if now - last_used < target_key:
            await asyncio.sleep(target_key - (now - last_used))

    def record_key_usage(self, api_key: str) -> None:
        self._key_last_used[api_key] = time.time()

    def get_key_last_used(self, api_key: str) -> float:
        return self._key_last_used.get(api_key, 0.0)

    # ── Project freeze (GCP project-level quota) ─────────────────

    def is_key_frozen_by_project(self, api_key: str, model_id: str) -> bool:
        proj = self._project_id_of_key.get(api_key)
        if not proj:
            return False
        until = self._project_frozen_until.get((proj, model_id), 0.0)
        return time.time() < until

    def freeze_project(self, project_id: str, model_id: str, duration: float) -> None:
        until = time.time() + duration
        key = (project_id, model_id)
        old = self._project_frozen_until.get(key, 0.0)
        if until > old:
            self._project_frozen_until[key] = until
        frozen_count = sum(
            1 for k in self._project_id_of_key if self._project_id_of_key[k] == project_id
        )
        logger.warning(
            "Project %s frozen for model %s, %ds (%d keys)",
            project_id, model_id, duration, frozen_count,
        )

    def set_key_project(self, api_key: str, project_id: str) -> None:
        self._project_id_of_key[api_key] = project_id

    # ── Utility ──────────────────────────────────────────────────

    @staticmethod
    def resolve_tier(account: Optional[Dict[str, Any]] = None) -> str:
        tier = (account or {}).get("tier", "free")
        return tier if tier in ("admin", "premium", "free") else "free"

import asyncio
import time
from typing import Dict, Optional, Set


class ModelPool:
    _instances: Dict[str, 'ModelPool'] = {}

    @classmethod
    def get_or_create(cls, pool_name: str, pool_config: dict, custom_endpoint_members: Optional[Set[str]] = None) -> 'ModelPool':
        if pool_name not in cls._instances:
            cls._instances[pool_name] = cls(pool_config, custom_endpoint_members)
        return cls._instances[pool_name]

    @classmethod
    def reset_instances(cls) -> None:
        """Drop cached pools so the next get_or_create rebuilds from current config.

        ``get_or_create`` keys on the pool name alone, so a member the admin just
        switched off on the dashboard stays in the cached ``_locks`` map and keeps
        being handed out — the toggle would save to the DB and change nothing at
        runtime. Pools are cheap to rebuild (one lock per member) and any slot
        still held belongs to a request already in flight, which finishes on its
        own.
        """
        cls._instances.clear()

    def __init__(self, pool_config: dict, custom_endpoint_members: Optional[Set[str]] = None):
        self.members = list(pool_config["members"])
        self.swap_failures = int(pool_config["swap_failures"])
        # Do not hold every request for two minutes when a pool is exhausted.
        # Callers can raise this explicitly for slow providers.
        self.max_retry_seconds = max(1, int(pool_config.get("max_retry_seconds", 30)))
        self.acquire_poll_interval = max(0.01, float(pool_config.get("acquire_poll_interval", 0.05)))
        self.acquire_deadline = max(1, int(pool_config.get("acquire_deadline", self.max_retry_seconds)))
        self._custom_members: Set[str] = custom_endpoint_members or set()
        self._gemini_members: Set[str] = {m for m in self.members if m not in self._custom_members}
        self._locks: Dict[str, asyncio.Lock] = {m: asyncio.Lock() for m in self.members}

    def sync_custom_members(self, custom_endpoint_members: Set[str]) -> None:
        for ep_name in custom_endpoint_members:
            if ep_name not in self._custom_members:
                self._custom_members.add(ep_name)
                self._gemini_members.discard(ep_name)
                if ep_name not in self.members:
                    self.members.append(ep_name)
                if ep_name not in self._locks:
                    self._locks[ep_name] = asyncio.Lock()

    async def acquire(
        self,
        skip: Optional[Set[str]] = None,
        timeout: Optional[float] = None,
        lock_gemini: bool = True,
    ) -> str:
        """Select a pool member and optionally reserve its member slot.

        Gemini pool streams use per-key active-request accounting, so they must
        not reserve one shared member slot before key selection. Custom endpoint
        members retain the original one-request-per-member behavior.
        """
        skip = skip or set()
        timeout = self.acquire_deadline if timeout is None else max(0.01, timeout)

        # A wall-clock adjustment must not extend a request's pool deadline.
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            # Priority 1: custom endpoint members (free ones only)
            for member in self._custom_members:
                if member in skip:
                    continue
                if self._locks[member].locked():
                    continue
                await self._locks[member].acquire()
                return member
            # Priority 2: Gemini members (fallback when custom endpoints busy).
            # Gemini concurrency is enforced by the selected API key's
            # active_requests limit. Do not serialize every key on one member
            # lock: a slow stream on key A must not block key B for this model.
            for member in self._gemini_members:
                if member in skip:
                    continue
                if lock_gemini:
                    if self._locks[member].locked():
                        continue
                    await self._locks[member].acquire()
                return member
            await asyncio.sleep(self.acquire_poll_interval)
        raise TimeoutError(f"Pool {self.members}: no free member after {timeout}s")

    def release(self, member: str) -> None:
        """Return a member's slot to the pool.

        Idempotent: releasing a member that is not held is a no-op. asyncio.Lock
        raises on a second release, and pool_manager releases the member both
        from its error handlers and again in the finally block — with the
        handlers unguarded, one surplus release anywhere in the retry path
        would abort the request with "Lock is not acquired" instead of the
        error being handled.

        The end state is the same either way: the lock is unlocked and the next
        caller acquires it normally.
        """
        lock = self._locks.get(member)
        if lock is not None and lock.locked():
            lock.release()


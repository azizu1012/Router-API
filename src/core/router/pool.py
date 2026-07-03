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

    def __init__(self, pool_config: dict, custom_endpoint_members: Optional[Set[str]] = None):
        self.members = list(pool_config["members"])
        self.swap_failures = int(pool_config["swap_failures"])
        self.max_retry_seconds = int(pool_config.get("max_retry_seconds", 120))
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

    async def acquire(self, skip: Optional[Set[str]] = None, timeout: float = 120) -> str:
        skip = skip or set()
        start = time.time()
        while time.time() - start < timeout:
            # Priority 1: custom endpoint members (free ones only)
            for member in self._custom_members:
                if member in skip:
                    continue
                if self._locks[member].locked():
                    continue
                await self._locks[member].acquire()
                return member
            # Priority 2: Gemini members (fallback when custom endpoints busy)
            for member in self._gemini_members:
                if member in skip:
                    continue
                if self._locks[member].locked():
                    continue
                await self._locks[member].acquire()
                return member
            await asyncio.sleep(0.05)
        raise TimeoutError(f"Pool {self.members}: no free member after {timeout}s")

    def release(self, member: str) -> None:
        self._locks[member].release()

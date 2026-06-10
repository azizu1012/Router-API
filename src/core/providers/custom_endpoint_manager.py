import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_api as logger
from src.backend.endpoints import (
    list_endpoints_db,
    get_endpoint_db,
    get_endpoint_by_account_db,
    add_endpoint_db,
    remove_endpoint_db,
    enable_endpoint_db,
    disable_endpoint_db,
    update_endpoint_db,
    set_fallback_db,
    assign_endpoint_to_account_db,
)


_DEFAULT_PATH = str(Path(__file__).resolve().parents[2] / "custom_endpoints.json")
_CACHE_TTL = 5.0


class CustomEndpointManager:
    _cache: Optional[Dict[str, Dict[str, Any]]] = None
    _account_map: Optional[Dict[str, str]] = None
    _cache_ts: float = 0.0

    def __init__(self, path: str = _DEFAULT_PATH) -> None:
        self.path = path
        self._load_cache()

    def _cache_fresh(self) -> bool:
        return self._cache is not None and (time.time() - self._cache_ts) < _CACHE_TTL

    def _load_cache(self) -> None:
        eps = {ep["name"]: ep for ep in list_endpoints_db()}
        am = {}
        for name, ep in eps.items():
            if ep.get("enabled", True):
                aid = ep.get("account_id") or ""
                if aid:
                    am[aid] = name
        self.__class__._cache = eps
        self.__class__._account_map = am
        self.__class__._cache_ts = time.time()

    def _invalidate_cache(self) -> None:
        self.__class__._cache_ts = 0.0
        self._load_cache()

    # ── CRUD ─────────────────────────────────────────────────────

    def add(self, name: str, base_url: str, auth_key: str) -> Dict[str, Any]:
        r = add_endpoint_db(name, base_url, auth_key)
        self._invalidate_cache()
        return r

    def remove(self, name: str) -> Optional[Dict[str, Any]]:
        r = remove_endpoint_db(name)
        if r:
            self._invalidate_cache()
        return r

    def enable(self, name: str) -> Optional[Dict[str, Any]]:
        r = enable_endpoint_db(name)
        if r:
            self._invalidate_cache()
        return r

    def disable(self, name: str) -> Optional[Dict[str, Any]]:
        r = disable_endpoint_db(name)
        if r:
            self._invalidate_cache()
        return r

    def list_endpoints(self) -> List[Dict[str, Any]]:
        if not self._cache_fresh():
            self._load_cache()
        return list((self._cache or {}).values())

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        if not self._cache_fresh():
            self._load_cache()
        return (self._cache or {}).get(name)

    # ── Account-based lookup ─────────────────────────────────────
    def get_endpoint_for_account(self, account: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not account:
            return None
        aid = account.get("account_id") or ""
        if not aid:
            return None
        if not self._cache_fresh():
            self._load_cache()
        ep_name = (self._account_map or {}).get(aid)
        if ep_name and self._cache is not None:
            return self._cache.get(ep_name)
        return None

    def assign_to_account(self, name: str, account_id: str) -> Optional[Dict[str, Any]]:
        r = assign_endpoint_to_account_db(name, account_id)
        if r:
            self._invalidate_cache()
        return r

    def get_endpoint_model_ids(self, name: str) -> List[str]:
        ep = self.get(name)
        if ep:
            all_models = ep.get("models", [])
            enabled = ep.get("enabled_models", [])
            return [m for m in all_models if m in enabled]
        return []

    # ── Fallback ─────────────────────────────────────────────────

    def set_fallback(self, name: str, enabled: bool) -> Optional[Dict[str, Any]]:
        r = set_fallback_db(name, enabled)
        if r:
            self._invalidate_cache()
        return r

    def get_fallback_endpoints(self) -> List[Dict[str, Any]]:
        if not self._cache_fresh():
            self._load_cache()
        return [ep for ep in (self._cache or {}).values()
                if ep.get("enabled", True) and ep.get("fallback") and ep.get("models")]

    def get_first_fallback_model(self) -> Optional[Dict[str, Any]]:
        for ep in self.get_fallback_endpoints():
            enabled = ep.get("enabled_models", [])
            for mid in ep.get("models", []):
                if mid in enabled:
                    return {"model_id": mid, "endpoint": ep}
        return None

    def toggle_model(self, name: str, model_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
        ep = self.get(name)
        if not ep:
            return None
        enabled_list = list(ep.get("enabled_models", []))
        if enabled:
            if model_id not in enabled_list:
                enabled_list.append(model_id)
        else:
            if model_id in enabled_list:
                enabled_list.remove(model_id)
        r = update_endpoint_db(name, enabled_models=enabled_list)
        if r:
            self._invalidate_cache()
        return r

    # ── Pool assignments ─────────────────────────────────────────

    def assign_pool_model(self, name: str, pool_name: str, model_id: str) -> Optional[Dict[str, Any]]:
        ep = self.get(name)
        if not ep:
            return None
        pool_assignments = dict(ep.get("pool_assignments", {}))
        pool_assignments[pool_name] = model_id
        r = update_endpoint_db(name, pool_assignments=pool_assignments)
        if r:
            self._invalidate_cache()
        return r

    def remove_pool_model(self, name: str, pool_name: str) -> Optional[Dict[str, Any]]:
        ep = self.get(name)
        if not ep:
            return None
        pool_assignments = dict(ep.get("pool_assignments", {}))
        if pool_name in pool_assignments:
            del pool_assignments[pool_name]
        r = update_endpoint_db(name, pool_assignments=pool_assignments)
        if r:
            self._invalidate_cache()
        return r

    def get_pool_assignments(self, name: str) -> Dict[str, str]:
        ep = self.get(name)
        if not ep:
            return {}
        return ep.get("pool_assignments", {})

    # ── Model fetching ───────────────────────────────────────────

    async def _try_fetch_models(self, base_url: str, auth_key: str) -> dict:
        candidates = [
            f"{base_url}/models",
            f"{base_url}/v1/models",
            f"{base_url}/api/v1/models",
        ]
        headers = {"Authorization": f"Bearer {auth_key}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            for url in candidates:
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            base = url[:url.rfind("/models")]
                            logger.info("Fetched models from %s for endpoint %s", base, base_url)
                            return {"base": base, "data": data}
                        logger.warning("Fetch models %s returned HTTP %d", url, resp.status)
                except asyncio.TimeoutError:
                    logger.warning("Fetch models timeout for %s", url)
                except aiohttp.ClientConnectorError as e:
                    logger.warning("Fetch models connection refused for %s: %s", url, e)
                except Exception as e:
                    logger.warning("Fetch models error for %s: %s", url, e)
        raise ValueError(f"Cannot reach models endpoint at {base_url}")

    async def fetch_models(self, name: str) -> List[str]:
        ep = get_endpoint_db(name)
        if not ep:
            raise ValueError(f"Endpoint '{name}' not found")

        result = await self._try_fetch_models(ep["base_url"], ep["auth_key"])
        data = result["data"]

        raw_models = []
        if isinstance(data, dict):
            raw_models = data.get("data") or data.get("models") or []
        elif isinstance(data, list):
            raw_models = data

        free_models = []
        for m in raw_models:
            mid = m.get("id") or m.get("name") or ""
            mid = str(mid).strip()
            if not mid:
                continue
            if ":free" in mid.lower() or "free" in m.get("name", "").lower():
                free_models.append(mid)
            elif not any(x in mid.lower() for x in ["paid", "premium"]):
                free_models.append(mid)

        update_endpoint_db(name, base_url=result["base"],
                           models=sorted(set(free_models)),
                           updated_at=datetime.utcnow().isoformat())
        self._invalidate_cache()
        return sorted(set(free_models))


_custom_endpoint_manager = CustomEndpointManager()

"""DuckDuckGo retrieval adapter; no ranking or response formatting lives here."""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None


class DuckDuckGoProviderMixin:
    async def _search_duckduckgo_records(
        self,
        query: str,
        index: int = 0,
        timelimit: Optional[str] = None,
        query_effective: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        if DDGS is None:
            self.logger.warning("DuckDuckGo search library is unavailable.")
            return []
        start_ts = datetime.now().timestamp()
        try:
            fetch_limit = max(5, self.search_top_results_limit)

            def _do_search():
                with DDGS() as ddgs:
                    kwargs = {"max_results": fetch_limit}
                    if timelimit:
                        kwargs["timelimit"] = timelimit
                    return list(ddgs.text(query, **kwargs))

            async with self.search_semaphore:
                results = await asyncio.wait_for(
                    asyncio.to_thread(_do_search), timeout=self.search_timeout_seconds
                )
            items: List[Dict[str, str]] = []
            for item in results[:fetch_limit]:
                url_raw = item.get("href") or item.get("url") or ""
                normalized_url = self._normalize_url(url_raw)
                if not normalized_url or self._is_blocked_domain(normalized_url):
                    continue
                items.append({
                    "provider": "duckduckgo",
                    "title": item.get("title") or "Không có tiêu đề",
                    "snippet": (item.get("body") or "").strip(),
                    "url": normalized_url,
                    "normalized_url": normalized_url,
                    "domain": self._normalize_domain(normalized_url),
                    "query": query,
                    "query_effective": query_effective or query,
                    "query_index": str(index),
                })
            latency_ms = int((datetime.now().timestamp() - start_ts) * 1000)
            self.logger.info("[Search Primary] Query: '%s' -> Retrieved %d items in %dms", query, len(items), latency_ms)
            return items
        except Exception as exc:
            latency_ms = int((datetime.now().timestamp() - start_ts) * 1000)
            self.logger.warning("[Search Primary Failed] Query: '%s' in %dms | Error: %s", query, latency_ms, exc)
            return []

    async def _run_fallback_search_records(self, query: str) -> List[Dict[str, str]]:
        return []


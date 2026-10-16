"""Fetch and extract readable page evidence without ranking concerns."""

import asyncio
from typing import Any, Dict, Tuple

import requests


class SearchEvidenceMixin:
    async def _fetch_page_data(self, url: str) -> Tuple[str, Dict[str, Any]]:
        cached = self._get_deep_read_cache(url)
        if cached is not None:
            return cached, {}

        def _fetch_once(timeout_sec: float) -> Tuple[str, Dict[str, Any]]:
            from src.core.runtime.url_safety import is_safe_url
            safe, _ = is_safe_url(url, allow_private=False)
            if not safe:
                return "", {}

            target_url = url
            response = None
            for _ in range(3):
                response = requests.get(
                    target_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (ChadGibitiBot/1.0)",
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "en-US,en;q=0.8,vi;q=0.7",
                    },
                    timeout=timeout_sec,
                    allow_redirects=False,
                )
                if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
                    loc = response.headers.get("Location", "")
                    import urllib.parse
                    target_url = urllib.parse.urljoin(target_url, loc)
                    safe, _ = is_safe_url(target_url, allow_private=False)
                    if not safe:
                        return "", {}
                    continue
                break

            if not response or response.status_code != 200 or not response.text:
                return "", {}
            if not response.encoding:
                response.encoding = response.apparent_encoding or "utf-8"
            meta = self._extract_page_metadata(response.text)
            parsed = self._extract_main_text(response.text)
            if len(parsed) < 120:
                return "", meta
            return parsed[:self.deep_read_max_chars].strip(), meta

        for timeout_sec in (3.0, 5.0):
            try:
                text, meta = await asyncio.to_thread(_fetch_once, timeout_sec)
                if text:
                    self._set_deep_read_cache(url, text, ttl_seconds=7200)
                    return text, meta
            except Exception:
                continue
        self._set_deep_read_cache(url, "", ttl_seconds=self.search_empty_evidence_cache_ttl_seconds)
        return "", {}

    async def _fetch_page_evidence(self, url: str) -> str:
        text, _ = await self._fetch_page_data(url)
        return text


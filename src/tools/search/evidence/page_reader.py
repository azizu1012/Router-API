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
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (ChadGibitiBot/1.0)",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.8,vi;q=0.7",
                },
                timeout=timeout_sec,
            )
            if response.status_code != 200 or not response.text:
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


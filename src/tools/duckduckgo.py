import asyncio
import re
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Union

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

import requests
from src.core.config_n_logg.logger import logger_system as logger

from src.tools.ddg_ranking import SearchRankingMixin


class AdvancedSearchManager(SearchRankingMixin):

    def __init__(self):
        self.logger = logger
        self.web_search_cache = {}
        self.deep_read_cache = {}
        self.search_lock = asyncio.Lock()
        self.cache_lock = asyncio.Lock()
        self.inflight_search_tasks: Dict[str, asyncio.Task] = {}
        self.failed_search_cooldowns: Dict[str, float] = {}

        self.fallback_provider_limit = int(os.getenv("SEARCH_FALLBACK_PROVIDER_LIMIT", "2"))
        self.intent_batch_size = int(os.getenv("SEARCH_INTENT_BATCH_MAX", "3"))
        self.min_quality_sources = int(os.getenv("SEARCH_MIN_QUALITY_SOURCES", "1"))
        self.time_sensitive_min_quality_sources = int(os.getenv("SEARCH_TIME_SENSITIVE_MIN_QUALITY_SOURCES", "2"))
        self.search_web_mode = (os.getenv("SEARCH_WEB_MODE", "grounded")).strip().lower()
        if self.search_web_mode not in {"grounded", "fast"}:
            self.search_web_mode = "grounded"
        self.search_grounded_top_links = int(os.getenv("SEARCH_GROUNDED_TOP_LINKS", "3"))
        self.search_top_results_limit = int(os.getenv("SEARCH_TOP_RESULTS_LIMIT", "5"))
        self.deep_read_top_links = int(os.getenv("SEARCH_DEEP_READ_TOP_LINKS", "2"))
        self.deep_read_max_chars = int(os.getenv("SEARCH_DEEP_READ_MAX_CHARS", "1800"))
        self.exa_use_autoprompt = os.getenv("SEARCH_EXA_AUTOPROMPT", "false").lower() == "true"
        self.search_semantic_cache_enabled = os.getenv("SEARCH_SEMANTIC_CACHE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
        self.search_general_cache_ttl_seconds = int(os.getenv("SEARCH_GENERAL_CACHE_TTL_SEC", "21600"))
        self.search_time_sensitive_cache_ttl_seconds = int(os.getenv("SEARCH_TIME_SENSITIVE_CACHE_TTL_SEC", "1800"))
        self.search_failed_query_cooldown_seconds = int(os.getenv("SEARCH_FAILED_QUERY_COOLDOWN_SEC", "15"))
        self.search_empty_evidence_cache_ttl_seconds = int(os.getenv("SEARCH_EMPTY_EVIDENCE_CACHE_TTL_SEC", "600"))

    async def _fetch_page_evidence(self, url: str) -> str:
        cached = self._get_deep_read_cache(url)
        if cached is not None:
            return cached

        def _fetch_once(timeout_sec: float) -> str:
            headers = {
                "User-Agent": "Mozilla/5.0 (ChadGibitiBot/1.0)",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.8,vi;q=0.7",
            }
            response = requests.get(url, headers=headers, timeout=timeout_sec)
            if response.status_code != 200 or not response.text:
                return ""

            if not response.encoding:
                response.encoding = response.apparent_encoding or "utf-8"

            parsed = self._extract_main_text(response.text)
            if len(parsed) < 120:
                return ""
            return parsed[:self.deep_read_max_chars].strip()

        for attempt in range(2):
            timeout_sec = 3.0 if attempt == 0 else 5.0
            try:
                text = await asyncio.to_thread(_fetch_once, timeout_sec)
                if text:
                    self._set_deep_read_cache(url, text, ttl_seconds=7200)
                    return text
            except Exception:
                continue

        self._set_deep_read_cache(url, "", ttl_seconds=self.search_empty_evidence_cache_ttl_seconds)
        return ""

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
            def _do_search():
                with DDGS() as ddgs:
                    if timelimit:
                        return list(ddgs.text(query, max_results=5, timelimit=timelimit))
                    return list(ddgs.text(query, max_results=5))

            results = await asyncio.to_thread(_do_search)
            items: List[Dict[str, str]] = []
            for item in results[:5]:
                url_raw = item.get("href") or item.get("url") or ""
                normalized_url = self._normalize_url(url_raw)
                if not normalized_url or self._is_blocked_domain(normalized_url):
                    continue
                domain = self._normalize_domain(normalized_url)
                snippet = (item.get("body") or "").strip()
                items.append({
                    "provider": "duckduckgo",
                    "title": item.get("title") or "Không có tiêu đề",
                    "snippet": snippet,
                    "url": normalized_url,
                    "normalized_url": normalized_url,
                    "domain": domain,
                    "query": query,
                    "query_effective": query_effective or query,
                    "query_index": str(index),
                })

            latency_ms = int((datetime.now().timestamp() - start_ts) * 1000)
            self.logger.info(
                f"[Search Primary] Query: '{query}' -> Retrieved {len(items)} items in {latency_ms}ms"
            )
            return items
        except Exception as e:
            latency_ms = int((datetime.now().timestamp() - start_ts) * 1000)
            self.logger.warning(
                f"[Search Primary Failed] Query: '{query}' in {latency_ms}ms | Error: {e}"
            )
            return []

    async def _run_fallback_search_records(self, query: str) -> List[Dict[str, str]]:
        return []

    def _required_quality_sources(self, query: str) -> int:
        return self.time_sensitive_min_quality_sources if self._is_time_sensitive_query(query) else self.min_quality_sources

    def _split_multi_intents(self, query: str) -> List[str]:
        base = (query or "").strip()
        if not base:
            return []

        parts = re.split(r"\s*(?:\n+|;|\?|\.(?=\s)|,|\band\b|\bvà\b|\bva\b)\s*", base, flags=re.IGNORECASE)
        intents = [p.strip() for p in parts if len(p.strip()) > 2]
        if not intents:
            return [base]

        if len(intents) == 1:
            return intents

        if any(len(intent) < 14 for intent in intents):
            return [base]

        return intents[:3]

    def _determine_batch_size(self, intents: List[str]) -> int:
        if not intents:
            return 2
        avg_len = sum(len(x) for x in intents) / len(intents)
        if avg_len < 45 and len(intents) >= 3:
            return self.intent_batch_size
        return 2

    def _query_contains_suffix_intent(self, query: str, suffix: str) -> bool:
        query_lower = f" {query.lower()} "
        suffix_lower = suffix.strip().lower()
        if not suffix_lower:
            return True
        if f" {suffix_lower} " in query_lower:
            return True

        suffix_tokens = [tok for tok in re.split(r"\s+", suffix_lower) if tok]
        if suffix_tokens and all(f" {tok} " in query_lower for tok in suffix_tokens):
            return True

        return False

    def _build_secondary_query(self, q1: str, suffixes: List[str]) -> str:
        q1_clean = q1.strip()
        if not q1_clean:
            return q1_clean

        for suffix in suffixes:
            suffix_clean = (suffix or "").strip()
            if not suffix_clean:
                continue
            if self._query_contains_suffix_intent(q1_clean, suffix_clean):
                continue
            return f"{q1_clean} {suffix_clean}"

        return q1_clean

    async def _search_single_intent(self, q_sub: str, force_fallback: bool = False) -> str:
        selected_topic = self._classify_topic(q_sub)
        q1 = q_sub.strip()

        self.logger.info(f"Classified: {selected_topic.upper()}. Searching for: '{q_sub}'")
        transformed_query, timelimit = self._transform_temporal_query(q1)
        primary_queries = [transformed_query or q1]

        primary_tasks = [
            asyncio.create_task(self._search_duckduckgo_records(p_query, idx, timelimit=timelimit, query_effective=transformed_query))
            for idx, p_query in enumerate(primary_queries)
        ]
        primary_results = await asyncio.gather(*primary_tasks, return_exceptions=True)

        records: List[Dict[str, str]] = []
        for result in primary_results:
            if isinstance(result, Exception):
                continue
            records.extend(result)

        records = self._dedupe_records(records)
        required_sources = self._required_quality_sources(q_sub)
        has_enough = self._is_search_result_sufficient(records, selected_topic, q_sub, required_sources)

        should_fallback = force_fallback or not has_enough
        if should_fallback:
            fallback_records = await self._run_fallback_search_records(q_sub)
            records.extend(fallback_records)
            records = self._dedupe_records(records)

        scored = []
        for rec in records:
            rec["score"] = str(self._score_record(selected_topic, q_sub, rec))
            scored.append(rec)

        ranked = self._lightweight_rerank(selected_topic, q_sub, scored)

        if self.search_web_mode == "grounded":
            top_records = ranked[:self.search_grounded_top_links]
            if top_records:
                evidence_results = await asyncio.gather(
                    *(self._fetch_page_evidence(rec.get("url", "")) for rec in top_records),
                    return_exceptions=True,
                )
                for rec, evidence in zip(top_records, evidence_results):
                    rec["evidence"] = evidence if isinstance(evidence, str) else ""

        final = self._format_final_search_result(selected_topic, q_sub, ranked, required_sources)
        quality_count = self._count_quality_sources(ranked, selected_topic, q_sub)

        if quality_count < required_sources:
            final += "\n\n⚠️ Chưa đủ nguồn chất lượng theo ngưỡng cho truy vấn này; nên xem kết quả như thông tin tham khảo."
        return final

    async def _execute_search_pipeline(self, clean_query: str, force_fallback: bool) -> str:
        intents = self._split_multi_intents(clean_query)
        if not intents:
            return ""

        if len(intents) > 1:
            self.logger.info(f"Subquery fanout enabled. Running {len(intents)} search intents fully in parallel.")

        tasks = [self._search_single_intent(intent, force_fallback) for intent in intents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        final_sections = []
        for intent, res in zip(intents, results):
            if isinstance(res, Exception):
                self.logger.error(f"Search intent error for '{intent[:60]}': {res}")
                final_sections.append(f"### 🔍 [Chủ đề: GENERAL] Kết quả cho '{intent}':\n(Không thể truy xuất dữ liệu cho intent này.)")
            elif res:
                final_sections.append(res)

        return "\n\n".join(final_sections).strip() if final_sections else ""

    async def run_search_apis(self, query: str, mode: str = "general"):
        raw_query = query or ""
        force_fallback = "[FORCE FALLBACK]" in raw_query.upper()
        clean_query = raw_query.replace("[FORCE FALLBACK]", "").strip()
        if not clean_query:
            return ""

        cache_key = f"{mode}|{clean_query}"
        normalized_key = self._normalize_search_cache_key(cache_key)
        time_sensitive = self._is_time_sensitive_query(clean_query)
        bypass_cache = time_sensitive and not force_fallback

        inflight_task: Optional[asyncio.Task] = None
        async with self.cache_lock:
            cached_result = None if (force_fallback or bypass_cache) else self.get_web_search_cache(cache_key)
            if not cached_result:
                inflight_task = self.inflight_search_tasks.get(normalized_key)

        if cached_result:
            self.logger.info(f"Web search result from cache for query: {clean_query[:50]}...")
            return cached_result

        if inflight_task:
            self.logger.info(f"Web search joined inflight task for key={normalized_key[:80]}")
            return await inflight_task

        if not force_fallback and not time_sensitive and self.search_failed_query_cooldown_seconds > 0:
            async with self.cache_lock:
                cooldown_until = self.failed_search_cooldowns.get(normalized_key, 0)
            if cooldown_until > time.time():
                self.logger.info(f"Search cooldown active for key={normalized_key[:80]}")
                return "⚠️ Nguồn tìm kiếm đang tạm quá tải, vui lòng thử lại sau ít giây."

        task = asyncio.create_task(self._execute_search_pipeline(clean_query, force_fallback))
        async with self.cache_lock:
            self.inflight_search_tasks[normalized_key] = task

        try:
            output = await task
            if not output and time_sensitive and not force_fallback:
                absolute_date = datetime.now().strftime("%d/%m/%Y")
                forced_query = f"{clean_query} ngay {absolute_date}"
                self.logger.info(
                    f"Time-sensitive query produced empty result. Retrying forced fallback query='{forced_query[:80]}'."
                )
                output = await self._execute_search_pipeline(forced_query, True)

            if output:
                async with self.cache_lock:
                    if not bypass_cache:
                        self.set_web_search_cache(cache_key, output, time_sensitive=time_sensitive)
                    self.failed_search_cooldowns.pop(normalized_key, None)
                return output

            if not time_sensitive and self.search_failed_query_cooldown_seconds > 0:
                async with self.cache_lock:
                    self.failed_search_cooldowns[normalized_key] = time.time() + self.search_failed_query_cooldown_seconds
            self.logger.error("All search providers failed for all intents.")
            return ""
        except Exception as e:
            if not time_sensitive and self.search_failed_query_cooldown_seconds > 0:
                async with self.cache_lock:
                    self.failed_search_cooldowns[normalized_key] = time.time() + self.search_failed_query_cooldown_seconds
            self.logger.error(f"Search pipeline exception: {e}")
            return ""
        finally:
            async with self.cache_lock:
                current = self.inflight_search_tasks.get(normalized_key)
                if current is task:
                    del self.inflight_search_tasks[normalized_key]

    async def search_with_citations(self, query: str) -> Tuple[str, List[Dict[str, str]]]:
        raw_query = query or ""
        clean_query = raw_query.replace("[FORCE FALLBACK]", "").strip()
        if not clean_query:
            return "", []

        intents = self._split_multi_intents(clean_query)
        if not intents:
            return "", []

        all_formatted = []
        all_citations = []
        seen_urls = set()

        for intent in intents:
            selected_topic = self._classify_topic(intent)
            q1 = intent.strip()

            transformed_query, timelimit = self._transform_temporal_query(q1)
            primary_queries = [transformed_query or q1]

            primary_tasks = [
                asyncio.create_task(self._search_duckduckgo_records(p_query, idx, timelimit=timelimit, query_effective=transformed_query))
                for idx, p_query in enumerate(primary_queries)
            ]
            primary_results = await asyncio.gather(*primary_tasks, return_exceptions=True)

            records: List[Dict[str, str]] = []
            for result in primary_results:
                if isinstance(result, Exception):
                    continue
                records.extend(result)

            records = self._dedupe_records(records)
            required_sources = self._required_quality_sources(intent)
            has_enough = self._is_search_result_sufficient(records, selected_topic, intent, required_sources)

            should_fallback = not has_enough
            if should_fallback:
                fallback_records = await self._run_fallback_search_records(intent)
                records.extend(fallback_records)
                records = self._dedupe_records(records)

            scored = []
            for rec in records:
                rec["score"] = str(self._score_record(selected_topic, intent, rec))
                scored.append(rec)

            ranked = self._lightweight_rerank(selected_topic, intent, scored)

            if self.search_web_mode == "grounded":
                top_records = ranked[:self.search_grounded_top_links]
                if top_records:
                    evidence_results = await asyncio.gather(
                        *(self._fetch_page_evidence(rec.get("url", "")) for rec in top_records),
                        return_exceptions=True,
                    )
                    for rec, evidence in zip(top_records, evidence_results):
                        rec["evidence"] = evidence if isinstance(evidence, str) else ""

            formatted = self._format_final_search_result(selected_topic, intent, ranked, required_sources)
            all_formatted.append(formatted)

            display_records = self._domain_diversify_records(ranked, self.search_top_results_limit, True)
            for rec in display_records:
                title = rec.get("title") or "Source"
                url = rec.get("url") or ""
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_citations.append({"title": title, "url": url})

        return "\n\n".join(all_formatted), all_citations


search_manager = AdvancedSearchManager()


async def web_search(queries: Union[str, List[str]], max_results: int = 4) -> str:
    if isinstance(queries, str):
        return await search_manager.run_search_apis(queries)

    if not queries:
        return "No queries provided."

    tasks = [search_manager.run_search_apis(q) for q in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    formatted_results = []
    for q, res in zip(queries, results):
        if isinstance(res, Exception):
            formatted_results.append(f"### 🔍 Kết quả cho '{q}':\n(Lỗi: {res})")
        elif res:
            formatted_results.append(res)

    return "\n\n".join(formatted_results).strip() if formatted_results else "No results found."


async def search_with_citations(query: str) -> Tuple[str, List[Dict[str, str]]]:
    return await search_manager.search_with_citations(query)

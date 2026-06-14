# pyright: reportAttributeAccessIssue=false

import re
import unicodedata
import dateparser
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from src.tools.ddg_data import SearchDataMixin


class SearchUtilsMixin(SearchDataMixin):

    def _extract_date(self, text: str) -> Optional[datetime]:
        if not text:
            return None
        try:
            return dateparser.parse(text, languages=['vi', 'en'])
        except Exception as e:
            self.logger.warning(f"Error parsing date in _extract_date: {e}")
            return None

    def _extract_main_text(self, html_text: str) -> str:
        soup = BeautifulSoup(html_text, 'lxml')
        
        # Loại bỏ các thẻ không cần thiết
        for tag in soup(["script", "style", "noscript", "svg", "form", "button", "header", "footer", "nav", "aside"]):
            tag.decompose()
            
        # Tìm nội dung chính
        content = soup.find("article") or soup.find("main") or soup.body
        if not content:
            return ""
            
        text = content.get_text(separator=' ')
        text = re.sub(r"\b(cookie policy|accept cookies|subscribe|advertisement|all rights reserved)\b", " ", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip()

    def _normalize_domain(self, url: str) -> str:
        try:
            host = (urlparse(url).netloc or "").lower().strip()
            if host.startswith("www."):
                host = host[4:]
            return host
        except Exception:
            return ""

    def _organization_domain(self, domain: str) -> str:
        d = (domain or "").strip().lower()
        if not d:
            return ""
        labels = [x for x in d.split(".") if x]
        if len(labels) < 2:
            return d

        if len(labels) >= 3 and ".".join(labels[-2:]) in {"com.vn", "gov.vn", "org.vn", "edu.vn", "net.vn"}:
            return ".".join(labels[-3:])

        return ".".join(labels[-2:])

    def _normalize_query_tokens(self, text: str) -> List[str]:
        normalized = unicodedata.normalize("NFKD", (text or "").lower())
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
        tokens = [t for t in normalized.split() if len(t) > 2]
        return tokens

    def _normalize_text_for_match(self, text: str) -> str:
        normalized = unicodedata.normalize("NFKD", (text or "").lower())
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _query_overlap_count(self, query: str, text: str) -> int:
        q_tokens = set(self._normalize_query_tokens(query))
        if not q_tokens:
            return 0
        t_tokens = set(self._normalize_query_tokens(text))
        return len(q_tokens.intersection(t_tokens))

    def _query_coverage_score(self, query: str, text: str) -> float:
        q_tokens = self._normalize_query_tokens(query)
        if not q_tokens:
            return 0.0

        normalized_text = self._normalize_text_for_match(text)
        text_tokens = set(normalized_text.split())
        unique_query_tokens = list(dict.fromkeys(q_tokens))

        token_hits = sum(1 for token in unique_query_tokens if token in text_tokens)
        token_ratio = token_hits / len(unique_query_tokens)

        score = 0.0
        if token_ratio >= 0.9:
            score += 1.45
        elif token_ratio >= 0.75:
            score += 1.1
        elif token_ratio >= 0.55:
            score += 0.8
        elif token_ratio >= 0.35:
            score += 0.45
        elif token_ratio > 0:
            score += 0.2

        phrase_hits = 0
        phrase_total = 0
        max_n = min(4, len(q_tokens))
        padded_text = f" {normalized_text} "

        for n in range(max_n, 1, -1):
            for i in range(0, len(q_tokens) - n + 1):
                phrase = " ".join(q_tokens[i:i + n]).strip()
                if len(phrase) < 7:
                    continue
                phrase_total += 1
                if f" {phrase} " in padded_text:
                    phrase_hits += 1

        if phrase_total > 0:
            phrase_ratio = phrase_hits / phrase_total
            if phrase_ratio >= 0.5:
                score += 1.25
            elif phrase_ratio >= 0.3:
                score += 0.85
            elif phrase_hits > 0:
                score += 0.45

        if phrase_hits > 0 and token_ratio >= 0.6:
            score += 0.35

        return score

    def _normalize_url(self, url: str) -> str:
        try:
            parsed = urlparse(url.strip())
            if parsed.scheme not in {"http", "https"}:
                return ""
            query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
            filtered = []
            for k, v in query_pairs:
                lk = (k or "").lower()
                if lk.startswith("utm_") or lk in {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid"}:
                    continue
                filtered.append((k, v))
            normalized = parsed._replace(
                scheme="https",
                netloc=(parsed.netloc or "").lower(),
                query=urlencode(filtered, doseq=True),
                fragment=""
            )
            clean = urlunparse(normalized)
            return clean[:-1] if clean.endswith("/") else clean
        except Exception:
            return ""

    def _is_blocked_domain(self, url: str) -> bool:
        domain = self._normalize_domain(url)
        if not domain:
            return True
        blocked = {'shopee', 'lazada', 'amazon', 'tiki'}
        return any(token in domain for token in blocked)

    def _is_time_sensitive_query(self, query: str) -> bool:
        q = self._normalize_text_for_match(query)
        markers = [
            "latest", "new", "current", "today", "now", "update", "moi", "hien tai", "hom nay", "vua",
            "patch", "version", "banner", "gia xang", "ron95", "diesel", "fuel", "endfield"
        ]
        return any(m in q for m in markers)

    def _contains_year(self, query: str) -> bool:
        return bool(re.search(r"\b(20\d{2})\b", query or ""))

    def _time_sensitive_timelimit(self, query: str) -> Optional[str]:
        q = self._normalize_text_for_match(query)
        urgent_markers = ["today", "now", "hom nay", "vua", "latest", "current", "update", "patch", "banner"]
        if any(m in q for m in urgent_markers):
            return "m"
        return "w"

    def _transform_temporal_query(self, query: str) -> Tuple[str, Optional[str]]:
        clean = (query or "").strip()
        if not clean:
            return clean, None

        time_sensitive = self._is_time_sensitive_query(clean)
        timelimit = self._time_sensitive_timelimit(clean) if time_sensitive else None

        if time_sensitive and not self._contains_year(clean):
            current_year = str(datetime.now().year)
            clean = f"{clean} {current_year}".strip()

        return clean, timelimit

    def _format_source_line(self, rec: Dict[str, str]) -> str:
        title = rec.get("title") or "Không có tiêu đề"
        snippet = (rec.get("snippet") or "").strip()
        if len(snippet) > 330:
            snippet = snippet[:330] + "..."
        url = rec.get("url") or ""
        domain = rec.get("domain") or ""
        return f"**{title}** [{domain}](<{url}>): {snippet}"

    def _dedupe_records(self, records: List[Dict[str, str]]) -> List[Dict[str, str]]:
        unique = []
        seen = set()
        for rec in records:
            key = rec.get("normalized_url") or rec.get("url")
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(rec)
        return unique

    def _get_deep_read_cache(self, url: str) -> Optional[str]:
        item = self.deep_read_cache.get(url)
        if not item:
            return None

        ttl_seconds = int(item.get("ttl_seconds", 7200))
        if datetime.now() - item["timestamp"] > timedelta(seconds=ttl_seconds):
            del self.deep_read_cache[url]
            return None

        return item.get("text", "")

    def _set_deep_read_cache(self, url: str, text: str, ttl_seconds: int = 7200):
        self.deep_read_cache[url] = {
            "text": text,
            "timestamp": datetime.now(),
            "ttl_seconds": max(60, ttl_seconds),
        }


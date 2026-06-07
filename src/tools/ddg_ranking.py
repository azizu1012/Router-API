import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Union

from src.tools.ddg_utils import SearchUtilsMixin


class SearchRankingMixin(SearchUtilsMixin):

    def _classify_topic(self, query: str) -> str:
        query_lower = (query or "").lower()
        generic_tokens = {"update", "news", "latest", "new", "information", "tin tức", "thông tin"}

        best_topic = "general"
        best_score = 0
        for topic, data in self.SEARCH_TOPICS.items():
            if topic == "general":
                continue

            score = 0
            for keyword in data["keywords"]:
                kw = (keyword or "").strip().lower()
                if not kw:
                    continue
                if kw in generic_tokens and topic != "general":
                    continue
                if kw in query_lower:
                    score += 1

            if score > best_score:
                best_score = score
                best_topic = topic

        return best_topic

    def _calculate_time_decay_penalty(self, topic: str, pub_date: Optional[datetime], is_time_sensitive: bool) -> float:
        if not is_time_sensitive:
            return 0.0
        try:
            if not pub_date:
                return -0.6

            now = datetime.now()
            delta = now - pub_date
            D = delta.days + (delta.seconds / 86400.0)

            if D < 0:
                return 0.0

            grace_window = 7.0
            if topic == "gaming":
                grace_window = 21.0
            elif topic in {"finance", "tech"}:
                grace_window = 3.0
            elif topic in {"movies_tv", "anime_manga"}:
                grace_window = 14.0
            elif topic == "weather":
                grace_window = 1.0

            excess_days = max(0.0, D - grace_window)
            decay_penalty = -0.1 * excess_days
            return round(max(-1.5, decay_penalty), 3)

        except Exception as e:
            self.logger.warning(f"Error calculating time decay penalty: {e}")
            return 0.0

    def _dynamic_reputation_score(self, topic: str, query: str, record: Dict[str, str]) -> float:
        domain = (record.get("domain") or "").strip().lower()
        title = record.get("title") or ""
        snippet = record.get("snippet") or ""
        evidence = record.get("evidence") or ""

        score = 0.0
        score += self._query_coverage_score(query, f"{title} {snippet}")
        if evidence:
            score += 0.75 * self._query_coverage_score(query, f"{title} {evidence}")

        overlap_snippet = self._query_overlap_count(query, f"{title} {snippet}")
        if overlap_snippet >= 4:
            score += 1.0
        elif overlap_snippet == 3:
            score += 0.75
        elif overlap_snippet == 2:
            score += 0.5
        elif overlap_snippet == 1:
            score += 0.2

        if evidence:
            overlap_evidence = self._query_overlap_count(query, f"{title} {evidence}")
            if overlap_evidence >= 3:
                score += 0.75
            elif overlap_evidence == 2:
                score += 0.45
            elif overlap_evidence == 1:
                score += 0.2

        snippet_len = len(snippet.strip())
        if snippet_len >= 80:
            score += 0.3
        if snippet_len >= 140:
            score += 0.25

        evidence_len = len(evidence.strip())
        if evidence_len >= 180:
            score += 0.75

        if any(tag in domain for tag in [".gov", ".edu", ".ac.", ".int", "gov.vn", "edu.vn"]):
            score += 0.35

        title_match = any(token for token in re.findall(r"\w+", query.lower()) if len(token) > 3 and token in title.lower())
        if title_match:
            score += 0.35

        return score

    def _is_quality_record(self, topic: str, query: str, record: Dict[str, str]) -> bool:
        _ = topic
        dynamic_score = self._dynamic_reputation_score(topic, query, record)

        threshold = 2.25
        if self.search_web_mode == "grounded" and len((record.get("evidence") or "").strip()) >= 160:
            threshold -= 0.2
        if len((record.get("snippet") or "").strip()) >= 120:
            threshold -= 0.1

        return dynamic_score >= threshold

    def _score_record(self, topic: str, query: str, record: Dict[str, str]) -> float:
        title = (record.get("title") or "").lower()
        snippet = (record.get("snippet") or "").lower()
        q = (query or "").lower()

        score = self._dynamic_reputation_score(topic, query, record)

        if any(token for token in re.findall(r"\w+", q) if len(token) > 3 and token in f"{title} {snippet}"):
            score += 1.1

        provider = (record.get("provider") or "").lower()
        if provider == "duckduckgo":
            score += 0.4

        if len(snippet) > 120:
            score += 0.35

        return score

    def _lightweight_rerank(self, topic: str, query: str, records: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if not records:
            return []

        record_tokens = []
        for rec in records:
            text = f"{rec.get('title', '')} {rec.get('snippet', '')}".lower()
            tokens = set(self._normalize_query_tokens(text))
            record_tokens.append(tokens)

        consensus_scores = [0.0] * len(records)
        for i in range(len(records)):
            for j in range(len(records)):
                if i != j:
                    overlap = len(record_tokens[i].intersection(record_tokens[j]))
                    if overlap > 3:
                        consensus_scores[i] += min(1.0, overlap * 0.1)

        current_year = str(datetime.now().year)
        prev_year = str(datetime.now().year - 1)
        freshness_markers = ["mới nhất", "cập nhật", "hôm nay", "vừa qua", "mới đây", "ngày trước", "giờ trước", "latest", "update", "yesterday", "ago"]

        domain_counts = {}

        reranked = []
        for idx, rec in enumerate(records):
            base_score = float(rec.get("score", "0"))

            c_boost = min(1.5, consensus_scores[idx] * 0.2)

            is_time_sensitive = self._is_time_sensitive_query(query)
            f_boost = 0.0
            decay_penalty = 0.0
            title_snippet = f"{rec.get('title', '')} {rec.get('snippet', '')}".lower()

            if is_time_sensitive:
                boost_curr = 1.2
                boost_prev = 0.6
                boost_marker = 0.5
            else:
                boost_curr = 0.8
                boost_prev = 0.4
                boost_marker = 0.3

            if current_year in title_snippet:
                f_boost += boost_curr
            elif prev_year in title_snippet:
                f_boost += boost_prev
            if any(marker in title_snippet for marker in freshness_markers):
                f_boost += boost_marker

            if is_time_sensitive:
                pub_date = self._extract_date(rec.get("snippet", "")[:40])
                if not pub_date:
                    pub_date = self._extract_date(rec.get("title", "")[:40])
                decay_penalty = self._calculate_time_decay_penalty(topic, pub_date, is_time_sensitive)

            d_penalty = 0.0
            domain = (rec.get("domain") or "").strip().lower()
            org_domain = self._organization_domain(domain) or domain
            if org_domain:
                domain_counts[org_domain] = domain_counts.get(org_domain, 0) + 1
                if domain_counts[org_domain] > 1:
                    d_penalty = -0.5 * (domain_counts[org_domain] - 1)

            final_score = base_score + c_boost + f_boost + decay_penalty + d_penalty
            rec["score"] = str(round(final_score, 3))
            reranked.append(rec)

        return sorted(reranked, key=lambda x: float(x.get("score", "0")), reverse=True)

    def _count_quality_sources(self, records: List[Dict[str, str]], topic: str, query: str = "") -> int:
        quality_domains = set()
        for rec in records:
            domain = rec.get("domain", "")
            if self._is_quality_record(topic, query, rec):
                quality_domains.add(self._organization_domain(domain) or domain)
        return len(quality_domains)

    def _is_search_result_sufficient(self, records: List[Dict[str, str]], topic: str, query: str, required_sources: int, min_chars: int = 220) -> bool:
        if not records:
            return False

        quality_count = self._count_quality_sources(records, topic, query)
        top_window = records[:self.search_top_results_limit]
        total_chars = sum(len((x.get("snippet") or "").strip()) for x in top_window)
        return quality_count >= required_sources and total_chars >= min_chars

    def _domain_diversify_records(self, records: List[Dict[str, str]], limit: int, enforce_diversity: bool) -> List[Dict[str, str]]:
        if not enforce_diversity:
            return records[:limit]

        diverse: List[Dict[str, str]] = []
        used_domains: Set[str] = set()

        for rec in records:
            domain = (rec.get("domain") or "").strip().lower()
            org_domain = self._organization_domain(domain) or domain
            if not domain or org_domain in used_domains:
                continue
            diverse.append(rec)
            used_domains.add(org_domain)
            if len(diverse) >= limit:
                return diverse

        if len(diverse) < limit:
            for rec in records:
                if rec in diverse:
                    continue
                diverse.append(rec)
                if len(diverse) >= limit:
                    break

        return diverse[:limit]

    def _format_final_search_result(self, topic: str, query: str, ranked_records: List[Dict[str, str]], required_sources: int) -> str:
        top_lines = []
        additional_lines = []
        quality_domains = set()
        display_records = self._domain_diversify_records(ranked_records, self.search_top_results_limit, True)

        top_target = min(self.search_top_results_limit, max(required_sources, 3))
        for idx, rec in enumerate(display_records):
            line = self._format_source_line(rec)
            if idx < top_target:
                top_lines.append(line)
            else:
                additional_lines.append(line)

            if self._is_quality_record(topic, query, rec):
                domain = rec.get("domain", "")
                quality_domains.add(self._organization_domain(domain) or domain)

        deep_lines = []
        if self.search_web_mode == "grounded":
            for rec in display_records[:self.search_grounded_top_links]:
                evidence = rec.get("evidence", "")
                if not evidence:
                    continue
                title = rec.get("title") or "Không có tiêu đề"
                url = rec.get("url") or ""
                snippet = evidence[:360].strip()
                if snippet:
                    deep_lines.append(f"- {title} ([đọc nội dung](<{url}>)): {snippet}")

        unique_domains = {rec.get("domain") or "" for rec in display_records if rec.get("domain")}
        parts = [
            f"### 🔍 [Chủ đề: {topic.upper()}] Kết quả cho '{query}':",
            f"- Mode: {self.search_web_mode}",
            f"- Top results: {len(display_records)}",
            f"- Unique domains: {len(unique_domains)}",
            f"- Grounded reads: {self.search_grounded_top_links if self.search_web_mode == 'grounded' else 0}",
            f"- Required quality sources: {required_sources}",
            f"- Quality sources found: {len(quality_domains)}",
            "",
            "**Top ranked sources:**",
            "\n".join(top_lines) if top_lines else "(Không có nguồn top phù hợp từ lượt tìm kiếm này)",
            "",
            "**Additional corroborating sources:**",
            "\n".join(additional_lines) if additional_lines else "(Không có nguồn bổ sung)",
        ]

        if self.search_web_mode == "grounded":
            if deep_lines:
                parts.extend(["", "**Evidence excerpts (grounded read):**", "\n".join(deep_lines)])
            else:
                parts.extend([
                    "",
                    "⚠️ Grounded read chưa lấy được đoạn evidence rõ ràng; kết quả đang dựa nhiều vào snippet từ nguồn đã truy xuất.",
                ])

        return "\n".join(parts).strip()

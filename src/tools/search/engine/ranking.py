# pyright: reportAttributeAccessIssue=false

import re
from datetime import datetime
from typing import Dict, List, Optional, Set

from src.tools.search.engine.text import SearchUtilsMixin
from src.tools.search.languages.registry import detect_query_languages, iter_language_topic_keywords


class SearchRankingMixin(SearchUtilsMixin):

    def _classify_topic(self, query: str) -> str:
        """Pick the topic whose keyword list best matches `query`.

        Matching is accent-insensitive and word-bounded, because the search is
        driven by Vietnamese and English queries mixed freely. "bong da" and
        "bóng đá" must land on the same topic, and a keyword must never match
        because it happens to sit inside an unrelated longer word.
        """
        raw_query = (query or "").strip().lower()
        # CJK and other non-Latin scripts are intentionally kept in small
        # language modules; the Latin normalizer below would discard them.
        script_topic_scores = {}
        for topic, keywords in iter_language_topic_keywords(detect_query_languages(raw_query)):
            score = sum(1 for keyword in keywords if keyword.lower() in raw_query)
            if score:
                script_topic_scores[topic] = script_topic_scores.get(topic, 0) + score

        if script_topic_scores:
            return max(script_topic_scores, key=script_topic_scores.get)

        tokens = self._normalize_text_for_match(query).split()
        generic_tokens = {
            self._normalize_text_for_match(token)
            for token in ("update", "news", "latest", "new", "information", "tin tức", "thông tin")
        }

        best_topic = "general"
        best_score = 0
        for topic, data in self.SEARCH_TOPICS.items():
            if topic == "general":
                continue

            score = 0
            for keyword in data["keywords"]:
                kw = self._normalize_text_for_match(keyword)
                if not kw or kw in generic_tokens:
                    continue
                if self._contains_phrase(tokens, kw):
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

    def _detect_query_profile(self, query: str) -> str:
        q = (query or "").lower()
        if re.search(r"\b(v\d|\d+\.\d+|\d{4}-\d{2}-\d{2}|benchmark|spec|specification|protocol|rfc|changelog|release notes|model card)\b", q):
            return "tech_spec"
        if any(w in q for w in ["giá", "price", "hôm nay", "today", "vừa qua", "mới nhất", "breaking", "bão", "weather", "ron95", "xăng"]):
            return "realtime_news"
        if any(w in q for w in ["how to", "làm sao", "cách dùng", "example", "syntax", "error", "exception", "cú pháp", "fix bug"]):
            return "code_howto"
        return "general"

    def _domain_authority_score(self, domain: str, profile: str) -> float:
        d = (domain or "").strip().lower()
        if not d:
            return 0.0

        # Tier 4 SEO / Aggregator Spam penalty
        if d in getattr(self, "TIER4_SPAM_SEO_DOMAINS", set()) or any(spam in d for spam in ["tokencalc", "whichllm", "projedefteri", "hackaigc", "shattered", "prompeteer", "lorka.ai", "distk.in"]):
            return -3.0

        # Tier 1 Primary Authority bonus
        is_tier1 = d in getattr(self, "TIER1_AUTHORITY_DOMAINS", set()) or any(d.startswith(pat) or pat in d for pat in getattr(self, "TIER1_AUTHORITY_PATTERNS", ()))
        if is_tier1:
            return 2.5 if profile in ("tech_spec", "code_howto") else 2.0

        # Tier 2 Reputable Tech Publications & Media
        if d in getattr(self, "TIER2_REPUTABLE_DOMAINS", set()):
            return 1.0

        return 0.0

    def _information_density_score(self, text: str) -> float:
        """Measure technical information density (code, tables, numbers, specs)."""
        if not text:
            return 0.0
        score = 0.0
        if "```" in text or re.search(r"\b(def |class |import |async def |curl |pip install )\b", text):
            score += 0.8
        if re.search(r"\|.+\|.+\|", text):
            score += 0.8
        if re.search(r"\b\d+(\.\d+)?(%|ms|s|gb|mb|m|k tokens)\b", text, re.IGNORECASE):
            score += 0.5
        return score

    def _calculate_coupled_freshness(self, query: str, text: str, pub_date: Optional[datetime], is_time_sensitive: bool) -> float:
        """Calculate freshness score coupled with query terms in the same local window.

        Avoids boosting clickbait SEO articles that merely append '2026' in titles
        without actual temporal facts related to the query.
        """
        if not text:
            return 0.0

        q_tokens = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2]
        if not q_tokens:
            return 0.0

        current_year = str(datetime.now().year)
        prev_year = str(datetime.now().year - 1)
        freshness_markers = ["mới nhất", "cập nhật", "hôm nay", "vừa qua", "mới đây", "ngày trước", "giờ trước", "latest", "update", "yesterday", "ago"]

        sentences = re.split(r"[.\n!?]+", text.lower())
        best_coupled_score = 0.0

        for s in sentences:
            s_clean = s.strip()
            if not s_clean:
                continue
            hits = sum(1 for t in q_tokens if t in s_clean)
            if hits < 2 and len(q_tokens) >= 2:
                continue
            if hits < 1:
                continue

            has_curr_year = current_year in s_clean
            has_prev_year = prev_year in s_clean
            has_fresh_marker = any(m in s_clean for m in freshness_markers)

            s_boost = 0.0
            if has_curr_year:
                s_boost += 1.2
            elif has_prev_year:
                s_boost += 0.5

            if has_fresh_marker:
                s_boost += 0.4

            match_ratio = hits / len(q_tokens)
            coupled_val = s_boost * match_ratio
            if coupled_val > best_coupled_score:
                best_coupled_score = coupled_val

        if pub_date:
            pub_naive = pub_date.replace(tzinfo=None) if getattr(pub_date, "tzinfo", None) is not None else pub_date
            days = max(0.0, (datetime.now() - pub_naive).total_seconds() / 86400.0)
            if days <= 7:
                best_coupled_score += 1.0
            elif days <= 30:
                best_coupled_score += 0.6
            elif days <= 90:
                best_coupled_score += 0.3

        return round(best_coupled_score, 3)

    def _score_record(self, topic: str, query: str, record: Dict[str, str]) -> float:
        profile = self._detect_query_profile(query)
        domain = (record.get("domain") or "").strip().lower()
        title = (record.get("title") or "").lower()
        snippet = (record.get("snippet") or "").lower()
        evidence = (record.get("evidence") or "").lower()
        full_text = f"{title} {snippet} {evidence}"

        score = self._dynamic_reputation_score(topic, query, record)
        score += self._domain_authority_score(domain, profile)
        score += self._information_density_score(full_text)

        pub_date = record.get("published_date") or self._extract_date(snippet[:40]) or self._extract_date(title[:40])
        score += self._calculate_coupled_freshness(query, full_text, pub_date, self._is_time_sensitive_query(query))

        if any(token for token in re.findall(r"\w+", query.lower()) if len(token) > 3 and token in f"{title} {snippet}"):
            score += 1.1

        provider = (record.get("provider") or "").lower()
        if provider == "duckduckgo":
            score += 0.4

        if len(snippet) > 120:
            score += 0.35

        return round(score, 3)

    def _lightweight_rerank(self, topic: str, query: str, records: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if not records:
            return []

        profile = self._detect_query_profile(query)
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

        domain_counts = {}
        reranked = []

        for idx, rec in enumerate(records):
            base_score = float(rec.get("score", "0"))
            c_boost = min(1.5, consensus_scores[idx] * 0.2)

            domain = (rec.get("domain") or "").strip().lower()
            org_domain = self._organization_domain(domain) or domain
            d_penalty = 0.0
            if org_domain:
                domain_counts[org_domain] = domain_counts.get(org_domain, 0) + 1
                if domain_counts[org_domain] > 1:
                    d_penalty = -0.5 * (domain_counts[org_domain] - 1)

            final_score = base_score + c_boost + d_penalty
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
        profile = self._detect_query_profile(query)
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
            evidence_max = getattr(self, "search_evidence_max_chars", 2500)
            for rec in display_records[:self.search_grounded_top_links]:
                evidence = rec.get("evidence", "")
                if not evidence:
                    continue
                title = rec.get("title") or "Không có tiêu đề"
                url = rec.get("url") or ""
                snippet = self._extract_query_highlight(evidence, query, evidence_max)
                if snippet:
                    date_badge = ""
                    if rec.get("pub_date_str"):
                        date_badge = f" [📅 {rec.get('pub_date_str')} — {rec.get('relative_age')}]"
                    deep_lines.append(f"- {title}{date_badge} ([đọc nội dung](<{url}>)):\n{snippet}")

        unique_domains = {rec.get("domain") or "" for rec in display_records if rec.get("domain")}
        now_dt = datetime.now()
        server_anchor = now_dt.strftime("%A, %d %B %Y — %I:%M %p")
        parts = [
            f"[Mỏ neo thời gian thực: {server_anchor}]",
            f"### 🔍 [Chủ đề: {topic.upper()} | Profile: {profile.upper()}] Kết quả cho '{query}':",
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


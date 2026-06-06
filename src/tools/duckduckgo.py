import asyncio
import re
import os
import time
import unicodedata
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from typing import Dict, List, Any, Optional, Set, Tuple, Union

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

import requests
from src.core.config_n_logg.logger import logger_system as logger


class AdvancedSearchManager:
    """Manager for the advanced AI web search and grounding pipeline."""

    CACHE_TTL_SECONDS = 3600
    MAX_CACHE_SIZE = 1000
    MAX_TEXT_LENGTH = 10000

    # Search Topics from Bot
    SEARCH_TOPICS = {
        "gaming": {
            "keywords": ['game', 'patch', 'banner', 'update', 'release date', 'roadmap', 'leak', 'speculation', 'gacha', 'reroll', 'tier list', 'build', 'nhân vật', 'honkai', 'hsr', 'star rail', 'genshin', 'zzz', 'zenless', 'wuwa', 'wuthering waves', 'arknights', 'fgo', 'phiên bản', 'sự kiện'],
            "suffixes": ["update", "release date", "patch notes", "roadmap", "leaks", "speculation", "official", "tin tức"]
        },
        "tech": {
            "keywords": ['tech', 'công nghệ', 'ai', 'ios', 'android', 'app', 'software', 'hardware', 'card màn hình', 'cpu', 'laptop', 'phone'],
            "suffixes": ["review", "release date", "news", "vs", "benchmark", "specs", "đánh giá", "tin tức"]
        },
        "science": {
            "keywords": ['science', 'khoa học', 'space', 'vũ trụ', 'nasa', 'discovery', 'research', 'nghiên cứu', 'y tế'],
            "suffixes": ["new discovery", "latest research", "breakthrough", "study finds", "công bố", "nghiên cứu mới"]
        },
        "finance": {
            "keywords": ['finance', 'tài chính', 'stock', 'cổ phiếu', 'market', 'thị trường', 'investment', 'đầu tư', 'economy', 'kinh tế', 'lãi suất', 'ngân hàng'],
            "suffixes": ["stock price", "market analysis", "forecast", "news", "earnings report", "phân tích", "dự báo"]
        },
        "movies_tv": {
            "keywords": ['movie', 'phim', 'tv show', 'series', 'netflix', 'disney+', 'trailer', 'actor', 'diễn viên', 'đạo diễn', 'lịch chiếu'],
            "suffixes": ["review", "release date", "trailer", "cast", "ending explained", "season 2", "lịch chiếu phim", "đánh giá"]
        },
        "anime_manga": {
            "keywords": ['anime', 'manga', 'light novel', 'manhwa', 'manhua', 'chapter', 'episode', 'season', 'ova', 'phần mới', 'tập mới'],
            "suffixes": ["release date", "new season", "chapter review", "discussion", "spoiler", "tin tức anime"]
        },
        "sports": {
            "keywords": ['sports', 'thể thao', 'bóng đá', 'football', 'basketball', 'tennis', 'cầu lông', 'f1', 'đội tuyển', 'cầu thủ', 'trận đấu'],
            "suffixes": ["match result", "highlights", "live score", "news", "transfer", "lịch thi đấu", "kết quả"]
        },
        "music": {
            "keywords": ['music', 'âm nhạc', 'bài hát', 'ca sĩ', 'album', 'mv', 'concert', 'lyrics', 'lời bài hát', 'spotify', 'apple music'],
            "suffixes": ["new song", "album review", "music video", "tour dates", "lyrics meaning", "bài hát mới"]
        },
        "celebrity_gossip": {
            "keywords": ['celebrity', 'người nổi tiếng', 'showbiz', 'tin đồn', 'scandal', 'drama', 'diễn viên', 'ca sĩ'],
            "suffixes": ["scandal", "news", "gossip", "drama", "phốt", "tin đồn"]
        },
        "books_literature": {
            "keywords": ['book', 'sách', 'tiểu thuyết', 'tác giả', 'văn học', 'truyện', 'poetry', 'author', 'novel', 'đọc sách'],
            "suffixes": ["review", "summary", "recommendations", "new releases", "đánh giá sách", "tóm tắt"]
        },
        "photography_video": {
            "keywords": ['photography', 'nhiếp ảnh', 'quay phim', 'máy ảnh', 'camera', 'lens', 'drone', 'chụp ảnh', 'edit video'],
            "suffixes": ["tutorial", "gear review", "best settings", "tips and tricks", "hướng dẫn", "đánh giá thiết bị"]
        },
        "diy_crafts": {
            "keywords": ['diy', 'tự làm', 'thủ công', 'handmade', 'craft', 'tutorial', 'hướng dẫn', 'đồ handmade'],
            "suffixes": ["how to", "tutorial", "ideas", "project", "hướng dẫn làm", "ý tưởng"]
        },
        "social_media_trends": {
            "keywords": ['social media', 'mạng xã hội', 'tiktok', 'instagram', 'facebook', 'twitter', 'viral', 'meme', 'trend', 'xu hướng'],
            "suffixes": ["new trend", "viral video", "meme explained", "challenge", "xu hướng mới", "trào lưu"]
        },
        "food_cooking": {
            "keywords": ['food', 'cooking', 'recipe', 'công thức', 'nấu ăn', 'nhà hàng', 'quán ăn', 'ẩm thực', 'món ngon'],
            "suffixes": ["recipe", "how to make", "best restaurants", "review", "cách làm", "địa chỉ"]
        },
        "travel": {
            "keywords": ['travel', 'du lịch', 'phượt', 'khách sạn', 'resort', 'vé máy bay', 'địa điểm', 'kinh nghiệm'],
            "suffixes": ["travel guide", "things to do", "best places to visit", "flight deals", "kinh nghiệm du lịch", "giá vé"]
        },
        "health_wellness": {
            "keywords": ['health', 'wellness', 'sức khỏe', 'fitness', 'gym', 'yoga', 'meditation', 'dinh dưỡng', 'bệnh'],
            "suffixes": ["benefits", "how to", "symptoms", "treatment", "healthy diet", "lợi ích", "cách tập"]
        },
        "mental_health": {
            "keywords": ['mental health', 'sức khỏe tinh thần', 'tâm lý', 'stress', 'anxiety', 'therapy', 'trị liệu', 'tâm sự'],
            "suffixes": ["how to cope", "symptoms of", "self-care tips", "therapy options", "cách đối phó", "lời khuyên"]
        },
        "fashion_beauty": {
            "keywords": ['fashion', 'thời trang', 'làm đẹp', 'beauty', 'mỹ phẩm', 'quần áo', 'brand', 'style', 'makeup', 'phối đồ'],
            "suffixes": ["trends", "style guide", "product review", "tutorial", "xu hướng", "cách phối đồ"]
        },
        "home_garden": {
            "keywords": ['home', 'garden', 'nhà cửa', 'sân vườn', 'trang trí', 'nội thất', 'diy', 'gardening', 'cây cảnh'],
            "suffixes": ["decor ideas", "gardening tips", "diy project", "organization hacks", "ý tưởng trang trí", "mẹo làm vườn"]
        },
        "pets_animals": {
            "keywords": ['pet', 'animal', 'thú cưng', 'chó', 'mèo', 'dog', 'cat', 'động vật', 'chăm sóc thú cưng'],
            "suffixes": ["care tips", "breeds", "funny videos", "health problems", "cách chăm sóc", "giống loài"]
        },
        "education": {
            "keywords": ['education', 'giáo dục', 'học tập', 'school', 'university', 'trường học', 'đại học', 'khóa học', 'online course'],
            "suffixes": ["best courses", "how to learn", "study tips", "admission requirements", "khóa học tốt nhất", "mẹo học tập"]
        },
        "career_development": {
            "keywords": ['career', 'sự nghiệp', 'phát triển bản thân', 'job search', 'tìm việc', 'resume', 'cv', 'interview', 'phỏng vấn'],
            "suffixes": ["job search tips", "resume template", "interview questions", "career path", "mẹo tìm việc", "câu hỏi phỏng vấn"]
        },
        "business_entrepreneurship": {
            "keywords": ['business', 'kinh doanh', 'khởi nghiệp', 'startup', 'marketing', 'sales', 'doanh nghiệp'],
            "suffixes": ["business ideas", "how to start", "marketing strategy", "case study", "ý tưởng kinh doanh", "chiến lược marketing"]
        },
        "automotive": {
            "keywords": ['automotive', 'xe hơi', 'ô tô', 'xe máy', 'car', 'motorcycle', 'vehicle', 'xe điện', 'vinfast'],
            "suffixes": ["review", "specs", "price", "release date", "vs", "đánh giá xe", "giá bán"]
        },
        "law_politics": {
            "keywords": ['law', 'politics', 'luật', 'chính trị', 'chính phủ', 'government', 'policy', 'election', 'bầu cử', 'quy định'],
            "suffixes": ["new law", "policy explained", "election results", "legal advice", "luật mới", "giải thích chính sách"]
        },
        "real_estate": {
            "keywords": ['real estate', 'bất động sản', 'nhà đất', 'housing market', 'apartment', 'căn hộ', 'lịch sử giá nhà'],
            "suffixes": ["market trends", "how to buy", "investment tips", "apartment tour", "xu hướng thị trường", "kinh nghiệm mua nhà"]
        },
        "cryptocurrency_blockchain": {
            "keywords": ['crypto', 'bitcoin', 'ethereum', 'blockchain', 'nft', 'defi', 'web3', 'tiền ảo', 'tiền điện tử'],
            "suffixes": ["price prediction", "news", "how to buy", "wallet", "dự đoán giá", "tin tức crypto"]
        },
        "local_events": {
            "keywords": ['event', 'sự kiện', 'lễ hội', 'concert', 'workshop', 'hội thảo', 'gần đây', 'quanh đây'],
            "suffixes": ["events near me", "tickets", "schedule", "local festivals", "sự kiện sắp tới", "lịch trình"]
        },
        "shopping_deals": {
            "keywords": ['shopping', 'mua sắm', 'deal', 'giảm giá', 'khuyến mãi', 'sale', 'discount', 'black friday', 'shopee', 'lazada'],
            "suffixes": ["best deals", "discount codes", "sale on", "product review", "mã giảm giá", "đánh giá sản phẩm"]
        },
        "history": {
            "keywords": ['history', 'lịch sử', 'chiến tranh', 'ancient', 'medieval', 'modern history', 'lịch sử việt nam'],
            "suffixes": ["history of", "explained", "documentary", "key events", "lịch sử về", "giải thích"]
        },
        "environment_sustainability": {
            "keywords": ['environment', 'môi trường', 'biến đổi khí hậu', 'climate change', 'sustainability', 'năng lượng tái tạo', 'ô nhiễm'],
            "suffixes": ["latest news", "solutions", "impact of", "how to help", "tin tức môi trường", "giải pháp"]
        },
        "general": {
            "keywords": [],
            "suffixes": ["news", "latest", "update", "information", "tin tức", "thông tin", "mới nhất"]
        }
    }

    SEARCH_CACHE_STOPWORDS = {
        "the", "a", "an", "of", "for", "to", "and", "or", "in", "on", "at", "is", "are", "be",
        "toi", "la", "va", "cua", "cho", "ve", "trong", "tai", "duoc", "khong", "nao", "gi", "bao", "khi",
        "news", "information", "thong", "tin", "xem", "hoi", "giup",
    }

    SEARCH_CACHE_PHRASE_ALIASES = [
        ("moi nhat", "latest"),
        ("hien tai", "current"),
        ("cap nhat", "update"),
        ("khi nao", "when"),
        ("bao gio", "when"),
        ("ket thuc", "end"),
        ("thoi gian", "schedule"),
        ("lich", "schedule"),
    ]

    SEARCH_CACHE_TOKEN_ALIASES = {
        "hsr": "honkai_star_rail",
        "starrail": "honkai_star_rail",
        "banner": "banner",
        "latest": "latest",
        "current": "current",
        "update": "update",
        "patch": "patch",
        "schedule": "schedule",
    }

    def __init__(self):
        self.logger = logger
        self.web_search_cache = {}
        self.deep_read_cache = {}
        self.search_lock = asyncio.Lock()
        self.cache_lock = asyncio.Lock()
        self.inflight_search_tasks: Dict[str, asyncio.Task] = {}
        self.failed_search_cooldowns: Dict[str, float] = {}

        # Load configurations
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

        # Precompiled regex patterns for date extraction
        self._date_min_pattern = re.compile(r'\b(\d+)\s*(?:phút|phút trước|minute|minutes|min|mins)\b', re.IGNORECASE)
        self._date_hour_pattern = re.compile(r'\b(\d+)\s*(?:giờ|giờ trước|hour|hours|hr|hrs)\b', re.IGNORECASE)
        self._date_day_pattern = re.compile(r'\b(\d+)\s*(?:ngày|ngày trước|day|days)\b', re.IGNORECASE)
        self._date_week_pattern = re.compile(r'\b(\d+)\s*(?:tuần|tuần trước|week|weeks)\b', re.IGNORECASE)
        self._date_month_pattern = re.compile(r'\b(\d+)\s*(?:tháng|tháng trước|month|months)\b', re.IGNORECASE)
        self._date_year_pattern = re.compile(r'\b(\d+)\s*(?:năm|năm trước|year|years|yr|yrs)\b', re.IGNORECASE)
        self._date_today_pattern = re.compile(r'\b(?:hôm nay|today)\b', re.IGNORECASE)
        self._date_yesterday_pattern = re.compile(r'\b(?:hôm qua|yesterday)\b', re.IGNORECASE)

        self._date_yyyy_mm_dd_pattern = re.compile(r'\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b', re.IGNORECASE)
        self._date_dd_mm_yyyy_pattern = re.compile(r'\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b', re.IGNORECASE)
        self._date_month_day_pattern = re.compile(r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})\b', re.IGNORECASE)
        self._date_day_month_pattern = re.compile(r'\b(\d{1,2})\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b', re.IGNORECASE)
        self._date_year_only_pattern = re.compile(r'\b(20\d{2})\b', re.IGNORECASE)

    def _remove_diacritics(self, text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text or "")
        return "".join(ch for ch in normalized if not unicodedata.combining(ch))

    def _canonicalize_search_query(self, query: str) -> str:
        lowered = (query or "").strip().lower()
        lowered = lowered.replace("[force fallback]", " ")
        lowered = self._remove_diacritics(lowered)

        for src, dst in self.SEARCH_CACHE_PHRASE_ALIASES:
            lowered = re.sub(rf"\b{re.escape(src)}\b", dst, lowered)

        lowered = re.sub(r"[^a-z0-9_\s]", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered).strip()
        if not lowered:
            return ""

        tokens = []
        for token in lowered.split(" "):
            normalized_token = self.SEARCH_CACHE_TOKEN_ALIASES.get(token, token)
            if not normalized_token or normalized_token in self.SEARCH_CACHE_STOPWORDS:
                continue
            if len(normalized_token) <= 1:
                continue
            tokens.append(normalized_token)

        if not tokens:
            return lowered

        canonical_tokens = sorted(set(tokens))
        return " ".join(canonical_tokens[:32])

    def _normalize_search_cache_key(self, query: str) -> str:
        normalized = (query or "").strip().lower()
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = re.sub(r"\s*\|\s*", "|", normalized)

        mode = "general"
        payload = normalized
        if "|" in normalized:
            maybe_mode, maybe_payload = normalized.split("|", 1)
            mode = maybe_mode or "general"
            payload = maybe_payload or ""

        if self.search_semantic_cache_enabled:
            canonical_payload = self._canonicalize_search_query(payload)
            if canonical_payload:
                payload = canonical_payload

        return f"{mode}|{payload}"

    def get_web_search_cache(self, query: str):
        key = self._normalize_search_cache_key(query)
        if key in self.web_search_cache:
            cached_item = self.web_search_cache[key]
            expires_at = cached_item.get("expires_at")
            if isinstance(expires_at, datetime):
                if datetime.now() <= expires_at:
                    return cached_item.get("data")
                del self.web_search_cache[key]
                return None

            if datetime.now() - cached_item['timestamp'] < timedelta(hours=6):
                return cached_item['data']
            del self.web_search_cache[key]
        return None

    def set_web_search_cache(self, query: str, data: str, time_sensitive: bool = False):
        key = self._normalize_search_cache_key(query)
        if len(self.web_search_cache) >= self.MAX_CACHE_SIZE:
            oldest_key = min(self.web_search_cache, key=lambda k: self.web_search_cache[k]['timestamp'])
            del self.web_search_cache[oldest_key]

        now = datetime.now()
        ttl_seconds = self.search_time_sensitive_cache_ttl_seconds if time_sensitive else self.search_general_cache_ttl_seconds
        self.web_search_cache[key] = {
            'data': data,
            'timestamp': now,
            'expires_at': now + timedelta(seconds=ttl_seconds),
            'ttl_seconds': ttl_seconds,
            'time_sensitive': time_sensitive,
        }

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

    def _extract_date(self, text: str) -> Optional[datetime]:
        if not text:
            return None
        try:
            text_lower = text.lower().strip()
            now = datetime.now()

            if self._date_today_pattern.search(text_lower):
                return now
            if self._date_yesterday_pattern.search(text_lower):
                return now - timedelta(days=1)

            m = self._date_min_pattern.search(text_lower)
            if m:
                return now

            m = self._date_hour_pattern.search(text_lower)
            if m:
                return now

            m = self._date_day_pattern.search(text_lower)
            if m:
                return now - timedelta(days=int(m.group(1)))

            m = self._date_week_pattern.search(text_lower)
            if m:
                return now - timedelta(days=int(m.group(1)) * 7)

            m = self._date_month_pattern.search(text_lower)
            if m:
                return now - timedelta(days=int(m.group(1)) * 30)

            m = self._date_year_pattern.search(text_lower)
            if m:
                return now - timedelta(days=int(m.group(1)) * 365)

            m = self._date_yyyy_mm_dd_pattern.search(text_lower)
            if m:
                try:
                    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    pass

            m = self._date_dd_mm_yyyy_pattern.search(text_lower)
            if m:
                try:
                    return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                except ValueError:
                    try:
                        return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
                    except ValueError:
                        pass

            months = {
                "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
                "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
                "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
                "nov": 11, "november": 11, "dec": 12, "december": 12
            }

            m = self._date_month_day_pattern.search(text_lower)
            if m:
                month_val = months[m.group(1)]
                day_val = int(m.group(2))
                text_after = text_lower[m.end():m.end() + 15]
                year_match = self._date_year_only_pattern.search(text_after)
                year_val = int(year_match.group(1)) if year_match else now.year
                try:
                    return datetime(year_val, month_val, day_val)
                except ValueError:
                    pass

            m = self._date_day_month_pattern.search(text_lower)
            if m:
                day_val = int(m.group(1))
                month_val = months[m.group(2)]
                text_after = text_lower[m.end():m.end() + 15]
                year_match = self._date_year_only_pattern.search(text_after)
                year_val = int(year_match.group(1)) if year_match else now.year
                try:
                    return datetime(year_val, month_val, day_val)
                except ValueError:
                    pass

            m = self._date_year_only_pattern.search(text_lower)
            if m:
                return datetime(int(m.group(1)), 6, 1)

        except Exception as e:
            self.logger.warning(f"Error parsing date in _extract_date: {e}")

        return None

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

        return intents[:3]  # Limit to max 3 intents to avoid abuse

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

        # 1. Consensus / Information overlap
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

        # 2. Freshness Boost
        current_year = str(datetime.now().year)
        prev_year = str(datetime.now().year - 1)
        freshness_markers = ["mới nhất", "cập nhật", "hôm nay", "vừa qua", "mới đây", "ngày trước", "giờ trước", "latest", "update", "yesterday", "ago"]

        # 3. Domain duplication tracking
        domain_counts = {}

        reranked = []
        for idx, rec in enumerate(records):
            base_score = float(rec.get("score", "0"))

            # (a) Consensus boost
            c_boost = min(1.5, consensus_scores[idx] * 0.2)

            # (b) Freshness boost
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

            # (c) Time-decay penalty
            if is_time_sensitive:
                pub_date = self._extract_date(rec.get("snippet", "")[:40])
                if not pub_date:
                    pub_date = self._extract_date(rec.get("title", "")[:40])
                decay_penalty = self._calculate_time_decay_penalty(topic, pub_date, is_time_sensitive)

            # (d) Penalize domain duplication
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

    def _extract_main_text(self, html_text: str) -> str:
        text = html_text or ""
        text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<noscript[\s\S]*?</noscript>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<(svg|form|button)[\s\S]*?</\1>", " ", text, flags=re.IGNORECASE)

        article_match = re.search(r"<(article|main)[^>]*>([\s\S]*?)</\1>", text, flags=re.IGNORECASE)
        if article_match:
            text = article_match.group(2)

        for block_tag in ("header", "footer", "nav", "aside"):
            text = re.sub(rf"<{block_tag}[^>]*>[\s\S]*?</{block_tag}>", " ", text, flags=re.IGNORECASE)

        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\b(cookie policy|accept cookies|subscribe|advertisement|all rights reserved)\b", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
        return text

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


# Instantiate singleton manager
search_manager = AdvancedSearchManager()


async def web_search(queries: Union[str, List[str]], max_results: int = 4) -> str:
    """Perform advanced web search for one or multiple queries.
    
    If queries is a single string, it splits and searches intents.
    If it is a list of strings, it runs them in parallel.
    """
    if isinstance(queries, str):
        return await search_manager.run_search_apis(queries)

    if not queries:
        return "No queries provided."

    # Process multiple queries in parallel
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
    """Perform advanced web search for a query and return formatted results and citations."""
    return await search_manager.search_with_citations(query)

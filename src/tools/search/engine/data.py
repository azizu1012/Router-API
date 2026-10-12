

class SearchDataMixin:
    CACHE_TTL_SECONDS = 3600
    MAX_CACHE_SIZE = 1000
    MAX_TEXT_LENGTH = 10000

    # Topic vocabulary lives in src/tools/search/languages; the core only
    # consumes the merged registry and remains language agnostic.
    from src.tools.search.languages.registry import build_search_topics

    SEARCH_TOPICS = build_search_topics()

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

    # ponytail: tiered domain authority weights; extend when adding topic-specific curated lists
    TIER1_AUTHORITY_DOMAINS = {
        "github.com", "gitlab.com", "arxiv.org", "ietf.org", "w3.org",
        "modelcontextprotocol.io", "python.org", "rust-lang.org", "go.dev", "golang.org",
        "pypi.org", "npmjs.com", "crates.io", "huggingface.co",
        "google.com", "deepmind.google", "blog.google", "ai.google.dev", "cloud.google.com",
        "anthropic.com", "openai.com", "microsoft.com", "apple.com", "meta.com", "aws.amazon.com",
        "developer.mozilla.org", "docs.python.org", "fastapi.tiangolo.com",
    }

    TIER1_AUTHORITY_PATTERNS = (
        "docs.", "api.", "developer.", "spec.", "specification.",
        ".gov", ".edu", ".ac.", "gov.vn", "edu.vn"
    )

    TIER2_REPUTABLE_DOMAINS = {
        "theverge.com", "arstechnica.com", "techcrunch.com", "reuters.com", "bloomberg.com",
        "wired.com", "stackoverflow.com", "stackexchange.com", "wikipedia.org", "zdnet.com",
        "tomshardware.com", "anandtech.com", "venturebeat.com", "infoworld.com"
    }

    TIER4_SPAM_SEO_DOMAINS = {
        "tokencalc.dev", "whichllm.io", "projedefteri.com", "hackaigc.com", "shattered.io",
        "prompeteer.ai", "lorka.ai", "distk.in",
    }


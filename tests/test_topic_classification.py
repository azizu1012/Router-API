"""Topic classification for DuckDuckGo search.

The classifier is the only thing standing between a query and the topic it is
scored against, and it used to match raw substrings. That produced two bugs
that no HTTP test could see, because the endpoint answered 200 either way:

1. "ai" matched inside "hai", so "thứ hai" scored tech a point.
2. Diacritics mattered, so "bong da" and "bóng đá" landed on different topics.

Both are silent ranking failures, so they are pinned here rather than through
the endpoint.
"""

import pytest

from src.tools.ddg_ranking import SearchRankingMixin
from src.tools.ddg_utils import SearchUtilsMixin


@pytest.fixture
def classify():
    ranking = SearchRankingMixin.__new__(SearchRankingMixin)
    return ranking._classify_topic


@pytest.fixture
def utils():
    return SearchUtilsMixin.__new__(SearchUtilsMixin)


class TestProgrammingQueriesReachTech:
    """The original report: three developer queries all came back GENERAL."""

    @pytest.mark.parametrize("query", [
        "tin tức Rust mới nhất",
        "latest Python programming language news",
        "PyTorch torch.compile official documentation",
        "tin tức React 19 ra mắt",
        "hướng dẫn cài FastAPI trên Ubuntu",
        "tin tức bản đồng Rust 1.90",
        "best kubernetes operator for postgres",
        "so sánh Django và FastAPI",
    ])
    def test_a_programming_query_is_tech(self, classify, query):
        assert classify(query) == "tech"


class TestMatchingIgnoresDiacritics:
    """Vietnamese queries arrive with and without diacritics, interchangeably."""

    @pytest.mark.parametrize("plain,accented,topic", [
        ("tin tuc bong da Viet Nam", "tin tức bóng đá Việt Nam", "sports"),
        ("tin tuc cong nghe moi nhat", "tin tức công nghệ mới nhất", "tech"),
        ("gia Bitcoin hom nay", "giá Bitcoin hôm nay", "cryptocurrency_blockchain"),
        ("lich su chien tranh the gioi", "lịch sử chiến tranh thế giới", "history"),
    ])
    def test_the_same_query_lands_on_the_same_topic(self, classify, plain, accented, topic):
        assert classify(plain) == topic
        assert classify(accented) == topic


class TestTheLetterDIsNotDeleted:
    """Regression: "đ" survives normalisation as "d".

    NFKD does not decompose "đ" because it is its own letter, not a base plus a
    combining mark. The [^a-z0-9] filter then dropped it as punctuation, which
    split "bóng đá" into "bong a" -- two tokens, matching nothing. Every keyword
    containing "đ" was unreachable: đá, đội tuyển, đại học, địa điểm.
    """

    @pytest.mark.parametrize("text,expected", [
        ("bóng đá", "bong da"),
        ("đội tuyển", "doi tuyen"),
        ("đại học", "dai hoc"),
        ("Điểm", "diem"),
        ("hướng dẫn", "huong dan"),
    ])
    def test_the_stroke_letter_folds_instead_of_being_deleted(self, utils, text, expected):
        assert utils._normalize_text_for_match(text) == expected

    def test_a_keyword_containing_the_stroke_letter_can_still_match(self, utils, classify):
        """A word that normalises to a single token must match as one token."""
        assert utils._normalize_text_for_match("đá").split() == ["da"]
        assert classify("bóng đá") == "sports"


class TestMatchingRespectsWordBoundaries:
    """The bug the naive substring match produced: short keywords matched
    inside completely unrelated longer words."""

    def test_ai_does_not_match_inside_hai(self, classify):
        # "thứ hai" contains the letters "ai". Only "lịch sử"/"chiến tranh"
        # should give history a score here.
        assert classify("lịch sử chiến tranh thế giới thứ hai") == "history"

    def test_api_does_not_match_inside_a_longer_word(self, classify):
        assert classify("decor ideas for the living room") != "tech"

    def test_the_c_keyword_does_not_match_inside_cable(self, classify):
        # "c++" and "c#" both normalise to the bare token "c".
        assert classify("cable giá rẻ nhất") != "tech"

    def test_a_bare_word_still_matches(self, classify):
        assert classify("mua sắm online") == "shopping_deals"


class TestMultiWordKeywords:
    """A keyword may be a phrase, so matching has to work on token runs."""

    def test_a_two_word_keyword_matches(self, classify):
        assert classify("light novel hay mới") == "anime_manga"

    def test_words_of_a_phrase_must_be_adjacent(self, utils, classify):
        assert utils._contains_phrase(["light", "novel"], "light novel")
        assert not utils._contains_phrase(["novel", "light"], "light novel")
        assert not utils._contains_phrase(["light", "and", "novel"], "light novel")

    def test_punctuation_inside_a_keyword_is_normalised(self, utils, classify):
        # torch.compile -> "torch compile", so the query has to carry both words.
        assert utils._normalize_text_for_match("torch.compile") == "torch compile"
        assert classify("torch.compile benchmark") == "tech"


class TestGenericTokensDoNotPickATopic:
    """Words like "tin tức" appear in every topic's suffix list. They may never
    become the reason a topic wins."""

    @pytest.mark.parametrize("query", [
        "tin tức mới nhất",
        "latest news update",
        "thông tin mới",
    ])
    def test_a_query_of_only_generic_words_stays_general(self, classify, query):
        assert classify(query) == "general"


class TestTheClassifierAlwaysAnswers:
    """`general` is the fallback for "nothing matched", not an error path."""

    @pytest.mark.parametrize("query", [
        "",
        "   ",
        "asdkjh qwe zxc",
        "?!?!",
        "tin tuc Rust moi nhat",
    ])
    def test_no_input_raises(self, classify, query):
        assert isinstance(classify(query), str)

    def test_general_is_a_declared_topic(self):
        assert "general" in SearchRankingMixin.SEARCH_TOPICS
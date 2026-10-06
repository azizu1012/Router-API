"""Which search engine a request lands on.

The precedence is the whole design, so it is pinned here rather than described in
prose. Three inputs exist and only three:

    1. search_engine in the request body   — the client asked, honour it
    2. a hosted-search request             — grounding first, DuckDuckGo behind
    3. everything else                     — DuckDuckGo, touching no Gemini key

An account-level override used to sit between 1 and 2. Nothing in the dashboard
wrote it, so a row left at 'disabled' silently overrode a client that had
explicitly asked for web search — a fourth input that only stale configuration
could move. These tests hold that it is gone.
"""
import pytest

from src.api.opencode_proxy.handler.websearch import (
    resolve_search_engine,
    should_enable_web_search,
)


def _acct(**kw):
    base = {"name": "dev", "tier": "free", "search_engine": "auto"}
    base.update(kw)
    return base


class TestBodyWins:
    @pytest.mark.parametrize("engine", [
        "auto", "duckduckgo", "google_grounding", "disabled",
    ])
    def test_an_explicit_engine_is_honoured(self, engine):
        body = {"search_engine": engine, "web_search": True, "_hosted_search": True}
        assert resolve_search_engine(body, _acct()) == engine

    def test_case_and_whitespace_do_not_matter(self):
        assert resolve_search_engine({"search_engine": "  DuckDuckGo "}, _acct()) == "duckduckgo"

    def test_an_unknown_engine_falls_through_instead_of_erroring(self):
        assert resolve_search_engine({"search_engine": "bing"}, _acct()) == "duckduckgo"


class TestDialectDecides:
    def test_a_responses_hosted_request_gets_grounding_first(self):
        body = {"web_search": True, "_hosted_search": True}
        assert resolve_search_engine(body, _acct()) == "auto"

    def test_an_ordinary_chat_request_gets_duckduckgo(self):
        """The expensive case. Grounding here spends a Gemini call on every
        search, which is what the account override used to force on people."""
        assert resolve_search_engine({"web_search": True}, _acct()) == "duckduckgo"

    def test_a_request_without_search_still_resolves(self):
        """Resolution happens before the enable check; it must not raise."""
        assert resolve_search_engine({}, _acct()) == "duckduckgo"

    def test_hosted_search_only_counts_when_web_search_is_on(self):
        body = {"_hosted_search": True}
        assert resolve_search_engine(body, _acct()) == "duckduckgo"


class TestTheAccountOverrideIsGone:
    @pytest.mark.parametrize("stale", ["disabled", "auto", "google_grounding", "duckduckgo"])
    def test_no_stored_account_value_changes_the_outcome(self, stale):
        """Whatever an old row says, the dialect decides."""
        acct = _acct(search_engine=stale)

        assert resolve_search_engine({"web_search": True}, acct) == "duckduckgo"
        assert resolve_search_engine({"web_search": True, "_hosted_search": True}, acct) == "auto"

    def test_a_disabled_account_can_still_search_when_the_client_asks(self):
        """The behaviour change, stated plainly. This used to return False."""
        acct = _acct(search_engine="disabled")
        assert should_enable_web_search({"web_search": True}, acct) is True

    def test_the_account_argument_is_still_accepted(self):
        """Every caller passes it. Changing the signature would mean touching
        five call sites to delete a parameter, which is not worth the churn."""
        assert resolve_search_engine({}, _acct()) == "duckduckgo"
        assert resolve_search_engine({}, None) == "duckduckgo"


class TestEnableRulesUnchanged:
    def test_web_search_true_enables(self):
        assert should_enable_web_search({"web_search": True}, _acct()) is True

    def test_no_flag_stays_off(self):
        """Search is opt-in. The engine is decided by dialect, not by whether
        search happens to be available."""
        assert should_enable_web_search({}, _acct()) is False

    def test_an_explicit_disable_is_respected(self):
        assert should_enable_web_search({"web_search": False}, _acct()) is False

    def test_an_explicit_disabled_engine_still_blocks(self):
        """The body can still say no. Removing the account override did not make
        disabling impossible, it moved the decision to somewhere visible."""
        assert should_enable_web_search(
            {"web_search": True, "search_engine": "disabled"}, _acct()
        ) is False

    def test_none_account_is_handled(self):
        assert should_enable_web_search({"web_search": True}, None) is True
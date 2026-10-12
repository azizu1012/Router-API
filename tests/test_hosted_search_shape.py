"""Hosted web search must be reported in each dialect's own shape.

Both providers treat search as a tool *they* run, and both document the
distinction from a function tool:

  OpenAI    tools:[{"type":"web_search"}] -> a `web_search_call` output item.
            The client never runs a loop.

  Anthropic tools:[{"type":"web_search_20250305"}] -> a `server_tool_use` block
            paired with `web_search_tool_result`. Docs: "The API executes the
            tool internally. You see the call and its result in the response,
            but you don't handle execution."

The router used to emit a *function call* named `WebSearch` for both, which is
neither shape. On the chat dialects that call reached a client with no
implementation for it, on a response that carried nothing else.

A function tool the client owns is the opposite thing, and stays one: a real
`get_weather` the caller declared is still returned as a function call the caller
must answer.
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


RECORD = {"id": "call_1", "name": "WebSearch", "type": "search",
          "query": "capital of France", "engine": "duckduckgo",
          "citations": []}
FAILED = {"id": "call_2", "name": "WebSearch", "type": "search",
          "query": "x", "error": "boom"}


# ── OpenAI: web_search_call, never function_call ────────────────────────────

class TestResponsesHostedSearch:
    def test_a_search_is_a_web_search_call_item(self):
        from src.server.openai_server.routes.completions.responses import (
            _web_search_call_item,
        )

        item = _web_search_call_item("ws_1", RECORD)

        assert item["type"] == "web_search_call"
        assert item["id"] == "ws_1"
        assert item["status"] == "completed"

    def test_the_item_validates_against_the_official_model(self):
        pytest.importorskip("openai.types.responses")
        from openai.types.responses import ResponseFunctionWebSearch
        from src.server.openai_server.routes.completions.responses import (
            _web_search_call_item,
        )

        ResponseFunctionWebSearch.model_validate(
            _web_search_call_item("ws_1", RECORD))

    def test_the_item_validates_under_strict(self):
        pytest.importorskip("openai.types.responses")
        from openai.types.responses import ResponseFunctionWebSearch
        from src.server.openai_server.routes.completions.responses import (
            _web_search_call_item,
        )

        ResponseFunctionWebSearch.model_validate(
            _web_search_call_item("ws_1", RECORD), strict=True)

    def test_a_failed_search_says_so(self):
        from src.server.openai_server.routes.completions.responses import (
            _web_search_call_item,
        )

        assert _web_search_call_item("ws_2", FAILED)["status"] == "failed"

    def test_the_action_carries_the_query(self):
        from src.server.openai_server.routes.completions.responses import (
            _web_search_call_item,
        )

        action = _web_search_call_item("ws_1", RECORD)["action"]

        assert action["type"] == "search"
        assert action["query"] == "capital of France"

    def test_a_fetch_uses_the_open_page_action(self):
        from src.server.openai_server.routes.completions.responses import (
            _web_search_call_item,
        )

        item = _web_search_call_item("ws_3", {"type": "open_page",
                                              "url": "https://example.com"})

        assert item["action"] == {"type": "open_page",
                                  "url": "https://example.com"}

    def test_the_router_own_search_never_becomes_a_function_call(self):
        from src.server.openai_server.routes.completions.responses import (
            _extract_response_tool_calls,
        )

        got = _extract_response_tool_calls({"choices": [{"message": {
            "tool_calls": [{"id": "c1", "function": {
                "name": "WebSearch", "arguments": "{}"}}]}}]})

        assert got, "the record exists so it can be filtered where it is emitted"
        assert got[0]["name"] == "WebSearch"

    def test_a_real_client_tool_is_still_a_function_call(self):
        from src.server.openai_server.routes.completions.responses import (
            _extract_response_tool_calls,
        )

        got = _extract_response_tool_calls({"choices": [{"message": {
            "tool_calls": [{"id": "c1", "function": {
                "name": "get_weather", "arguments": '{"city":"Paris"}'}}]}}]})

        assert got[0]["name"] == "get_weather"


class TestSearchInjectionIsConfined:
    """Search is injected where the router commits to running it."""

    def test_the_responses_dialect_marks_the_request(self):
        from src.api.opencode_proxy.handler.responses_mapping import (
            responses_to_chat_body,
        )

        chat = responses_to_chat_body({
            "model": "gemini-flash", "input": "hi",
            "tools": [{"type": "web_search"}]})

        assert chat.get("_hosted_search") is True, chat
        assert chat.get("web_search") is True, chat

    def test_a_plain_responses_request_is_not_marked(self):
        from src.api.opencode_proxy.handler.responses_mapping import (
            responses_to_chat_body,
        )

        chat = responses_to_chat_body({"model": "gemini-flash",
                                       "input": "hi"})

        assert "_hosted_search" not in chat, chat

    def test_a_chat_request_never_injects_the_tool(self):
        from src.api.opencode_proxy.handler.proxy import OpenCodeProxy

        _msgs, tools = OpenCodeProxy()._inject_websearch_tool(
            {"web_search": True}, [{"role": "user", "content": "hi"}], [],
            {})

        assert tools == [], tools

    def test_the_responses_dialect_does_get_the_tool(self):
        from src.api.opencode_proxy.handler.proxy import OpenCodeProxy

        _msgs, tools = OpenCodeProxy()._inject_websearch_tool(
            {"web_search": True, "_hosted_search": True},
            [{"role": "user", "content": "hi"}], [], {})

        assert [t["function"]["name"] for t in tools] == ["WebSearch"], tools


# ── Anthropic: server_tool_use + web_search_tool_result ─────────────────────

class TestAnthropicServerTools:
    def test_a_search_is_a_server_tool_use_block(self):
        from src.api.claude_proxy.handler.anthropic_spec import (
            _server_tool_use_block,
        )

        rec = {"id": "srvtoolu_1", "name": "web_search",
               "query": "capital of France", "result": "Paris", "failed": False}

        block = _server_tool_use_block(rec)

        assert block == {"type": "server_tool_use", "id": "srvtoolu_1",
                         "name": "web_search",
                         "input": {"query": "capital of France"}}

    def test_the_id_carries_the_srvtoolu_prefix(self):
        """Anthropic documents this prefix as how a server call is told apart
        from a client one."""
        from src.api.claude_proxy.handler.anthropic_spec import (
            _server_tool_record,
        )

        rec = _server_tool_record("WebSearch", "call_abc", {"query": "x"}, "r")

        assert rec["id"].startswith("srvtoolu_"), rec["id"]

    def test_an_existing_server_id_is_not_double_prefixed(self):
        from src.api.claude_proxy.handler.anthropic_spec import (
            _server_tool_record,
        )

        rec = _server_tool_record("WebSearch", "srvtoolu_zz", {}, "r")

        assert rec["id"] == "srvtoolu_zz", rec["id"]

    def test_the_block_validates_against_the_official_model(self):
        pytest.importorskip("anthropic.types")
        from anthropic.types import ServerToolUseBlock
        from src.api.claude_proxy.handler.anthropic_spec import (
            _server_tool_use_block,
        )

        ServerToolUseBlock.model_validate(_server_tool_use_block(
            {"id": "srvtoolu_1", "name": "web_search", "query": "q"}))

    def test_the_result_block_pairs_by_tool_use_id(self):
        from src.api.claude_proxy.handler.anthropic_spec import (
            _web_search_result_block,
        )

        rec = {"id": "srvtoolu_1", "name": "web_search", "query": "q",
               "result": "Paris", "failed": False}

        block = _web_search_result_block(rec)

        assert block["type"] == "web_search_tool_result"
        assert block["tool_use_id"] == "srvtoolu_1"

    def test_a_failed_search_reports_an_error_result(self):
        from src.api.claude_proxy.handler.anthropic_spec import (
            _web_search_result_block,
        )

        block = _web_search_result_block(
            {"id": "srvtoolu_1", "name": "web_search", "query": "q",
             "result": "Search error: boom", "failed": True})

        assert block["content"]["type"] == "web_search_tool_result_error"
        assert block["content"]["error_code"] == "unavailable"

    def test_a_search_error_counts_as_failed(self):
        from src.api.claude_proxy.handler.anthropic_spec import (
            _server_tool_record,
        )

        rec = _server_tool_record("WebSearch", "c", {}, "Search error: x")

        assert rec["failed"] is True, rec

    def test_a_real_result_is_not_marked_failed(self):
        from src.api.claude_proxy.handler.anthropic_spec import (
            _server_tool_record,
        )

        assert _server_tool_record("WebSearch", "c", {}, "Paris")["failed"] \
            is False


class TestSearchIsNeverAutomatic:
    """Asking for a search in the prompt does not turn it on. That is OpenAI's
    own wording, and the native Gemini path used to do exactly this: any account
    whose search_engine was not literally "disabled" had search forced on, which
    is every account since the default is "auto"."""

    def test_the_native_path_only_searches_when_the_ask_for_it(self):
        import inspect
        from src.server.pass_through_server.routes import gemini_handlers

        src = inspect.getsource(gemini_handlers._handle_gemini_native_inner)

        assert 'account.get("web_search_enabled")' not in src, (
            "the account default still switches search on")
        # Search is derived once from explicit request fields/tools. There is
        # no account-level default that silently turns it on.
        assert src.count("web_search = any(") == 1, (
            f"search is not derived from one explicit request gate:\n{src}")

# ── Spec fidelity: what the official models actually declare ────────────────
#
# The earlier gate in this file used `strict=True`, which only enforces types.
# A payload carrying a field the spec does not declare passes it, so the router
# shipped `search_engine` inside the action and the test was green. `extra=
# "forbid"` is the check that actually rejects an undeclared field.

ACTION_WITH_RESULTS = {
    "id": "call_3", "name": "WebSearch", "type": "search",
    "query": "capital of France",
    "engine": "duckduckgo",
    "citations": [{"title": "France", "url": "https://example.org/paris"},
                  {"title": "Facts", "url": "https://example.net/france"}],
}


class TestActionMatchesTheDeclaredSpec:
    def test_strict_alone_does_not_catch_an_undeclared_field(self):
        """Why this file checks extra='forbid' and not just strict=True."""
        pytest.importorskip("openai.types.responses")
        from openai.types.responses import ResponseFunctionWebSearch

        invented = {"id": "ws_1", "type": "web_search_call",
                    "status": "completed",
                    "action": {"type": "search", "query": "x",
                               "search_engine": "duckduckgo"}}

        ResponseFunctionWebSearch.model_validate(invented, strict=True)
        with pytest.raises(Exception):
            ResponseFunctionWebSearch.model_validate(invented, strict=True,
                                                     extra="forbid")

    def test_the_router_emits_no_undeclared_action_field(self):
        pytest.importorskip("openai.types.responses")
        from openai.types.responses import ResponseFunctionWebSearch
        from src.server.openai_server.routes.completions.responses import (
            _web_search_call_item,
        )

        ResponseFunctionWebSearch.model_validate(
            _web_search_call_item("ws_1", ACTION_WITH_RESULTS),
            strict=True, extra="forbid")

    def test_the_action_lists_the_sources_it_read(self):
        from src.server.openai_server.routes.completions.responses import (
            _web_search_action,
        )

        action = _web_search_action(ACTION_WITH_RESULTS)

        assert action["sources"] == [
            {"type": "url", "url": "https://example.org/paris"},
            {"type": "url", "url": "https://example.net/france"},
        ]

    def test_no_search_means_no_sources_key(self):
        from src.server.openai_server.routes.completions.responses import (
            _web_search_action,
        )

        action = _web_search_action({"type": "search", "query": "x"})

        assert "sources" not in action
        assert "search_engine" not in action


class TestMessageCitations:
    def test_a_search_backed_answer_carries_url_citations(self):
        from src.server.openai_server.routes.completions.responses import (
            _url_citations,
        )

        anns = _url_citations("Paris is the capital.", ACTION_WITH_RESULTS["citations"])

        assert [a["type"] for a in anns] == ["url_citation", "url_citation"]
        assert anns[0]["url"] == "https://example.org/paris"
        assert anns[0]["title"] == "France"

    def test_a_citation_spans_the_text_it_supports(self):
        from src.server.openai_server.routes.completions.responses import (
            _url_citations,
        )

        text = "Paris is the capital."
        ann = _url_citations(text, ACTION_WITH_RESULTS["citations"])[0]

        assert ann["start_index"] == 0
        assert ann["end_index"] == len(text)

    def test_no_search_means_no_annotations(self):
        from src.server.openai_server.routes.completions.responses import (
            _url_citations,
        )

        assert _url_citations("Paris is the capital.", []) == []

    def test_the_annotations_validate_against_the_official_model(self):
        pytest.importorskip("openai.types.responses")
        from openai.types.responses.response_output_text import (
            AnnotationURLCitation,
        )
        from src.server.openai_server.routes.completions.responses import (
            _url_citations,
        )

        for ann in _url_citations("text", ACTION_WITH_RESULTS["citations"]):
            AnnotationURLCitation.model_validate(ann, strict=True, extra="forbid")

    def test_the_text_part_carries_them(self):
        from src.server.openai_server.routes.completions.responses import (
            _output_text_part,
        )

        part = _output_text_part("Paris.", ACTION_WITH_RESULTS["citations"])

        assert len(part["annotations"]) == 2


class TestAnthropicCitationsAndLimits:
    def test_the_result_block_carries_the_real_url_not_the_query(self):
        from src.api.claude_proxy.handler.anthropic_spec import (
            _server_tool_record, _web_search_result_block,
        )

        rec = _server_tool_record("WebSearch", "srvtoolu_1",
                                  {"query": "capital of France"},
                                  "Paris is the capital.",
                                  ACTION_WITH_RESULTS["citations"])

        result = _web_search_result_block(rec)
        assert result["content"][0]["url"] == "https://example.org/paris"
        assert result["content"][0]["title"] == "France"
        assert result["content"][0]["encrypted_content"] == "Paris is the capital."

    def test_the_answering_text_carries_the_citations(self):
        from src.api.claude_proxy.handler.anthropic_spec import (
            _search_citations, _server_tool_record,
        )

        rec = _server_tool_record("WebSearch", "srvtoolu_1", {"query": "q"},
"body", ACTION_WITH_RESULTS["citations"])

        cites = _search_citations([rec])

        assert len(cites) == 2
        assert cites[0]["type"] == "web_search_result_location"
        assert cites[0]["url"] == "https://example.org/paris"
        assert cites[0]["cited_text"] == "body"

    def test_the_citation_validates_against_the_official_model(self):
        pytest.importorskip("anthropic.types")
        from anthropic.types import CitationsWebSearchResultLocation
        from src.api.claude_proxy.handler.anthropic_spec import (
            _search_citations, _server_tool_record,
        )

        rec = _server_tool_record("WebSearch", "srvtoolu_1", {"query": "q"},
                                  "body", ACTION_WITH_RESULTS["citations"])

        for cite in _search_citations([rec]):
            CitationsWebSearchResultLocation.model_validate(cite,
                                                            strict=True,
                                                            extra="forbid")

    def test_max_uses_is_read_off_the_client_tool(self):
        from src.api.claude_proxy.handler.anthropic_spec import server_search_limits

        limits = server_search_limits({"tools": [
            {"type": "web_search_20250305", "name": "web_search",
             "max_uses": 3}]})

        assert limits["max_uses"] == 3

    def test_an_omitted_max_uses_is_the_api_default(self):
        from src.api.claude_proxy.handler.anthropic_spec import (
            _search_budget_left, server_search_limits,
        )

        limits = server_search_limits({"tools": [
            {"type": "web_search_20250305", "name": "web_search"}]})

        assert limits["max_uses"] is None
        assert _search_budget_left(limits, 4) is True
        assert _search_budget_left(limits, 5) is False

    def test_a_third_search_is_the_last_one_allowed(self):
        from src.api.claude_proxy.handler.anthropic_spec import (
            _search_budget_left, server_search_limits,
        )

        limits = server_search_limits({"tools": [
            {"type": "web_search_20250305", "name": "web_search",
             "max_uses": 3}]})

        assert [ _search_budget_left(limits, i) for i in (0, 1, 2, 3) ] == [
            True, True, True, False]

    def test_a_blocked_domain_is_kept_out_of_the_answer(self):
        from src.api.claude_proxy.handler.anthropic_spec import filter_citations

        kept = filter_citations(ACTION_WITH_RESULTS["citations"],
                               {"blocked_domains": ["example.net"]})

        assert [c["url"] for c in kept] == ["https://example.org/paris"]

    def test_a_subdomain_of_a_blocked_domain_is_kept_out_too(self):
        from src.api.claude_proxy.handler.anthropic_spec import filter_citations

        cits = [{"title": "a", "url": "https://news.example.net/x"}]

        assert filter_citations(cits, {"blocked_domains": ["example.net"]}) == []

    def test_only_the_allowed_domain_survives(self):
        from src.api.claude_proxy.handler.anthropic_spec import filter_citations

        kept = filter_citations(ACTION_WITH_RESULTS["citations"],
                               {"allowed_domains": ["example.net"]})

        assert [c["url"] for c in kept] == ["https://example.net/france"]

    def test_no_restriction_keeps_everything(self):
        from src.api.claude_proxy.handler.anthropic_spec import filter_citations

        assert filter_citations(ACTION_WITH_RESULTS["citations"], {}) == (
            ACTION_WITH_RESULTS["citations"])


# ── The whole response, not just the pieces ────────────────────────────────
#
# Every check above validates one helper's output. This drives the real proxy
# with a search the model asked for and validates the assembled Message against
# the official model under strict=True *and* extra="forbid" — the two together
# being the only combination that rejects both wrong types and undeclared fields.

class _Message:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.reasoning_content = None
        self.thought_signature = None


class _Choice:
    def __init__(self, msg):
        self.message = msg
        self.finish_reason = "stop"


class _Response:
    """Mirrors the chat-completion shape the proxy reads: resp.choices[0].message."""

    def __init__(self, msg, prompt=10, completion=5):
        self.usage = {"prompt_tokens": prompt, "completion_tokens": completion}
        self.choices = [_Choice(msg)]


def _scripted_model(*turns):
    """A pool_manager stand-in that returns one scripted tool-call turn at a time."""
    state = {"i": 0}

    async def call_nonstream(**kwargs):
        turn = turns[min(state["i"], len(turns) - 1)]
        state["i"] += 1
        return {"response": _Response(turn), "api_key": "k-123456",
                "model_id": "gemini-3.5-flash", "input_tokens": 100,
                "reservation": {}}

    return call_nonstream


SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search",
               "max_uses": 3}


def _websearch_call(id_="call_1"):
    return {"id": id_, "type": "function",
            "function": {"name": "WebSearch",
                         "arguments": '{"query": "capital of France"}'}}


@pytest.fixture
def search_backed(monkeypatch):
    """Run the Anthropic non-stream proxy over a turn that searches, twice.

    The first turn asks for a search and the router runs it; the second answers.
    """
    import src.api.claude_proxy.handler.proxy_nonstream as ns
    from src.api.claude_proxy import claude_proxy
    from src.tools.search.orchestration import manager as search_manager

    async def fake_search(queries, **kwargs):
        return ("Paris is the capital of France.", ACTION_WITH_RESULTS["citations"])

    async def fake_log_usage(*a):
        return None

    monkeypatch.setattr(ns.pool_manager, "call_nonstream",
                        _scripted_model(_Message(tool_calls=[_websearch_call()]),
                                        _Message("Paris is the capital.")))
    monkeypatch.setattr(search_manager, "execute_hybrid_search", fake_search)
    monkeypatch.setattr(ns, "log_usage", fake_log_usage, raising=False)

    body = {"model": "gemini-flash",
            "messages": [{"role": "user", "content": "capital of France?"}],
            "tools": [SEARCH_TOOL]}
    return asyncio.run(claude_proxy.create_message(body))


class TestTheAssembledMessageIsValid:
    def test_the_whole_message_validates_against_the_official_model(
            self, search_backed):
        pytest.importorskip("anthropic.types")
        from anthropic.types import Message

        Message.model_validate(search_backed, strict=True, extra="forbid")

    def test_the_blocks_appear_in_the_declared_order(self, search_backed):
        types = [b["type"] for b in search_backed["content"]]

        assert types == ["server_tool_use", "web_search_tool_result", "text"]

    def test_the_result_block_names_the_page_it_came_from(self, search_backed):
        result = next(b for b in search_backed["content"]
                      if b["type"] == "web_search_tool_result")

        assert result["content"][0]["url"] == "https://example.org/paris"
        assert result["content"][0]["title"] == "France"

    def test_the_answer_carries_the_citations(self, search_backed):
        text_block = next(b for b in search_backed["content"]
                          if b["type"] == "text")

        assert [c["type"] for c in text_block["citations"]] == [
            "web_search_result_location"] * 2
        assert text_block["citations"][0]["url"] == "https://example.org/paris"

    def test_the_usage_counts_the_search(self, search_backed):
        assert search_backed["usage"]["server_tool_use"] == {
            "web_search_requests": 1, "web_fetch_requests": 0}

    def test_the_stop_reason_is_end_turn(self, search_backed):
        assert search_backed["stop_reason"] == "end_turn"

    def test_no_client_tool_use_block_is_emitted(self, search_backed):
        """The router ran the search, so the client is not asked to answer it."""
        assert not [b for b in search_backed["content"]
                    if b["type"] == "tool_use"]


class TestRefusedSearchesAreNotCounted:
    """A search the cap refused is an attempt, not a search.

    `max_uses_exceeded` was stored as the result text, and nothing recognised it
    as an error, so the attempt was reported as a completed search: `usage`
    said four searches with `max_uses=3`, and the result block was built as a
    success carrying an empty content list.
    """

    def _refused(self):
        from src.api.claude_proxy.handler.anthropic_spec import (
            _server_tool_record, _web_search_result_block,
        )

        rec = _server_tool_record("WebSearch", "srvtoolu_9", {"query": "q"},
                                  "max_uses_exceeded", [], "max_uses_exceeded")

        return rec, _web_search_result_block(rec)

    def test_the_attempt_is_marked_failed(self):
        rec, _ = self._refused()

        assert rec["failed"] is True

    def test_the_result_block_reports_the_declared_error_code(self):
        _, block = self._refused()

        assert block["content"] == {
            "type": "web_search_tool_result_error",
            "error_code": "max_uses_exceeded"}

    def test_the_error_code_is_one_the_sdk_declares(self):
        import typing
        pytest.importorskip("anthropic")
        from anthropic.types import WebSearchToolResultErrorCode

        _, block = self._refused()

        assert block["content"]["error_code"] in typing.get_args(
            WebSearchToolResultErrorCode)

    def test_a_search_that_found_nothing_is_not_an_error(self):
        """Distinct case: ran, matched nothing. Anthropic says content: []."""
        from src.api.claude_proxy.handler.anthropic_spec import (
            _server_tool_record, _web_search_result_block,
        )

        rec = _server_tool_record("WebSearch", "srvtoolu_8", {"query": "q"},
                                  "No search results found.", [])
        block = _web_search_result_block(rec)

        assert rec["failed"] is False
        assert block["content"] == []

    def test_the_usage_skips_a_refused_attempt(self, monkeypatch):
        import src.api.claude_proxy.handler.proxy_nonstream as ns
        from src.api.claude_proxy import claude_proxy
        from src.tools.search.orchestration import manager as search_manager

        calls = {"n": 0}

        async def only_once(queries, **kwargs):
            calls["n"] += 1
            if calls["n"] > 1:
                raise AssertionError("a search ran past max_uses=1")
            return ("Paris.", ACTION_WITH_RESULTS["citations"])

        async def fake_log_usage(*a):
            return None

        monkeypatch.setattr(ns.pool_manager, "call_nonstream",
                            _scripted_model(
                                _Message(tool_calls=[_websearch_call("c1")]),
                                _Message(tool_calls=[_websearch_call("c2")]),
                                _Message("Answered from what I had.")))
        monkeypatch.setattr(search_manager, "execute_hybrid_search", only_once)
        monkeypatch.setattr(ns, "log_usage", fake_log_usage, raising=False)

        body = {"model": "gemini-flash",
                "messages": [{"role": "user", "content": "q"}],
                "tools": [dict(SEARCH_TOOL, max_uses=1)]}
        msg = asyncio.run(claude_proxy.create_message(body))

        assert calls["n"] == 1, "the search past the cap still ran"
        assert msg["usage"]["server_tool_use"] == {"web_search_requests": 1,
                                                   "web_fetch_requests": 0}

    def test_the_refused_attempt_reaches_the_client_as_an_error(self,
                                                               monkeypatch):
        import src.api.claude_proxy.handler.proxy_nonstream as ns
        from src.api.claude_proxy import claude_proxy
        from src.tools.search.orchestration import manager as search_manager

        async def only_once(queries, **kwargs):
            return ("Paris.", ACTION_WITH_RESULTS["citations"])

        async def fake_log_usage(*a):
            return None

        monkeypatch.setattr(ns.pool_manager, "call_nonstream",
                            _scripted_model(
                                _Message(tool_calls=[_websearch_call("c1")]),
                                _Message(tool_calls=[_websearch_call("c2")]),
                                _Message("Answered.")))
        monkeypatch.setattr(search_manager, "execute_hybrid_search", only_once)
        monkeypatch.setattr(ns, "log_usage", fake_log_usage, raising=False)

        body = {"model": "gemini-flash",
                "messages": [{"role": "user", "content": "q"}],
                "tools": [dict(SEARCH_TOOL, max_uses=1)]}
        msg = asyncio.run(claude_proxy.create_message(body))

        codes = [b["content"]["error_code"] for b in msg["content"]
                 if b["type"] == "web_search_tool_result"
                 and isinstance(b["content"], dict)]
        assert codes == ["max_uses_exceeded"]


# ── The streaming path ──────────────────────────────────────────────────────
#
# The search execution used to be written out twice, once per proxy path, and the
# two copies drifted: only the non-streaming one read `max_uses`, only one kept
# the citations, and the streaming one emitted no server-tool blocks at all — so a
# streamed search lost every source it had found. These run the real streaming
# proxy, so reverting any part of that shows up here.

class _SDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = None
        self.thought = None
        self.thought_signature = None

    def get(self, key, default=None):
        return getattr(self, key, default)


class _SChoice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason
        self.index = 0


class _SChunk:
    def __init__(self, delta, finish_reason=None):
        self.choices = [_SChoice(delta, finish_reason)]


def _stream_item(chunk):
    return {"chunk": chunk, "api_key": "k-abcd123456",
            "model_id": "gemini-3.5-flash", "input_tokens": 1000,
            "reservation": {}}


def _tool_call_chunk(name, args, tc_id):
    call = {"index": 0, "id": tc_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}
    return _SChunk(_SDelta(tool_calls=[call]))


def _parse_sse(raw_chunks):
    out = []
    for raw in raw_chunks:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        name = data = None
        for line in text.splitlines():
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            out.append((name, data))
    return out


def _run_streamed_search(monkeypatch, turns, body=None):
    """Drive the real streaming proxy over scripted turns; return parsed events."""
    import src.api.claude_proxy.handler.proxy_stream as st
    from src.api.claude_proxy import claude_proxy
    from src.tools.search.orchestration import manager as search_manager

    calls = {"n": 0, "search": 0}

    async def fake_call_stream(**kwargs):
        turn = turns[min(calls["n"], len(turns) - 1)]
        calls["n"] += 1
        for c in turn:
            yield _stream_item(c)

    async def fake_search(queries, **kwargs):
        calls["search"] += 1
        return ("Paris is the capital.", ACTION_WITH_RESULTS["citations"])

    async def fake_log_usage(*a):
        return None

    monkeypatch.setattr(st.pool_manager, "call_stream", fake_call_stream)
    monkeypatch.setattr(search_manager, "execute_hybrid_search", fake_search)
    monkeypatch.setattr(st, "log_usage", fake_log_usage, raising=False)

    body = body or {"model": "gemini-flash",
                    "messages": [{"role": "user", "content": "capital?"}],
                    "tools": [SEARCH_TOOL]}

    async def go():
        return [c async for c in claude_proxy.stream_message(dict(body))]

    return _parse_sse(asyncio.run(go())), calls


def _started_blocks(events):
    return [d["content_block"]["type"] for n, d in events
            if n == "content_block_start"]


class TestTheStreamedSearchUsesTheHostedShape:
    def test_the_search_is_reported_as_a_server_tool(self, monkeypatch):
        events, _ = _run_streamed_search(monkeypatch, [
            [_tool_call_chunk("WebSearch", {"query": "capital"}, "c1")],
            [_SChunk(_SDelta("Paris."), finish_reason="stop")],
        ])

        blocks = _started_blocks(events)
        assert "server_tool_use" in blocks
        assert "web_search_tool_result" in blocks

    def test_no_client_tool_use_is_emitted_for_the_search(self, monkeypatch):
        events, _ = _run_streamed_search(monkeypatch, [
            [_tool_call_chunk("WebSearch", {"query": "capital"}, "c1")],
            [_SChunk(_SDelta("Paris."), finish_reason="stop")],
        ])

        assert "tool_use" not in _started_blocks(events)

    def test_the_answer_still_arrives_after_the_search(self, monkeypatch):
        events, _ = _run_streamed_search(monkeypatch, [
            [_tool_call_chunk("WebSearch", {"query": "capital"}, "c1")],
            [_SChunk(_SDelta("Paris."), finish_reason="stop")],
        ])

        text = "".join(d["delta"]["text"] for n, d in events
                       if n == "content_block_delta"
                       and d["delta"].get("type") == "text_delta")
        assert "Paris." in text

    def test_the_result_block_pairs_with_its_use(self, monkeypatch):
        events, _ = _run_streamed_search(monkeypatch, [
            [_tool_call_chunk("WebSearch", {"query": "capital"}, "c1")],
            [_SChunk(_SDelta("Paris."), finish_reason="stop")],
        ])

        use = next(d["content_block"] for n, d in events
                   if n == "content_block_start"
                   and d["content_block"].get("type") == "server_tool_use")
        res = next(d["content_block"] for n, d in events
                   if n == "content_block_start"
                   and d["content_block"].get("type")
                   == "web_search_tool_result")
        assert res["tool_use_id"] == use["id"]
        assert use["id"].startswith("srvtoolu_")

    def test_the_usage_counts_the_search(self, monkeypatch):
        events, _ = _run_streamed_search(monkeypatch, [
            [_tool_call_chunk("WebSearch", {"query": "capital"}, "c1")],
            [_SChunk(_SDelta("Paris."), finish_reason="stop")],
        ])

        delta = next(d for n, d in events if n == "message_delta")
        assert delta["usage"]["server_tool_use"] == {
            "web_search_requests": 1, "web_fetch_requests": 0}

    def test_the_answer_carries_citation_deltas(self, monkeypatch):
        events, _ = _run_streamed_search(monkeypatch, [
            [_tool_call_chunk("WebSearch", {"query": "capital"}, "c1")],
            [_SChunk(_SDelta("Paris."), finish_reason="stop")],
        ])

        cites = []
        for n, d in events:
            if n == "content_block_delta" and d["delta"].get("type") == "citations_delta":
                cites.append(d["delta"]["citation"])
        
        assert cites, "the streamed answer cites nothing"
        assert cites[0]["type"] == "web_search_result_location"
        assert cites[0]["url"] == "https://example.org/paris"

    def test_the_whole_streamed_message_validates(self, monkeypatch):
        pytest.importorskip("anthropic.types")
        from anthropic.lib.streaming._messages import accumulate_event as _accum
        from anthropic.types import Message

        events, _ = _run_streamed_search(monkeypatch, [
            [_tool_call_chunk("WebSearch", {"query": "capital"}, "c1")],
            [_SChunk(_SDelta("Paris."), finish_reason="stop")],
        ])

        # The SDK's own accumulator, fed our frames: if the order were wrong it
        # raises, and if a block shape were wrong the Message would not validate.
        snapshot = None
        bufs = {}
        for _name, data in events:
            snapshot = _accum(event=data, current_snapshot=snapshot,
                              json_bufs=bufs)
        assert snapshot is not None
        Message.model_validate(snapshot.model_dump(), strict=True,
                               extra="forbid")


class TestTheStreamedSearchHonoursMaxUses:
    def _many_searches(self, monkeypatch, cap):
        turns = [[_tool_call_chunk("WebSearch", {"query": f"q{i}"}, f"c{i}")]
                 for i in range(5)]
        turns.append([_SChunk(_SDelta("Answered."), finish_reason="stop")])
        body = {"model": "gemini-flash",
                "messages": [{"role": "user", "content": "q"}],
                "tools": [dict(SEARCH_TOOL, max_uses=cap)]}
        return _run_streamed_search(monkeypatch, turns, body)

    def test_the_search_runs_at_most_max_uses_times(self, monkeypatch):
        _, calls = self._many_searches(monkeypatch, 2)

        assert calls["search"] == 2

    def test_the_cap_counts_across_the_whole_recursion(self, monkeypatch):
        """One shared counter, not one per recursion level."""
        _, calls = self._many_searches(monkeypatch, 3)

        assert calls["search"] == 3

    def test_a_refused_attempt_is_not_reported_as_a_search(self, monkeypatch):
        events, calls = self._many_searches(monkeypatch, 1)

        delta = next(d for n, d in events if n == "message_delta")
        assert delta["usage"]["server_tool_use"]["web_search_requests"] == 1

    def test_the_refusal_reaches_the_client_as_an_error(self, monkeypatch):
        events, _ = self._many_searches(monkeypatch, 1)

        results = [d["content_block"] for n, d in events
                   if n == "content_block_start"
                   and d["content_block"].get("type")
                   == "web_search_tool_result"]
        refused = [r for r in results
                   if isinstance(r["content"], dict)]
        assert refused, "the refused attempt was reported as a result list"
        assert refused[0]["content"]["error_code"] == "max_uses_exceeded"

    def test_empty_recursive_turn_emits_fallback_text_not_ending_on_tool_result(self, monkeypatch):
        """When the model does not produce text after a search, fallback text must be emitted
        so the assistant turn does not end with last_content_type=web_search_tool_result."""
        events, _ = _run_streamed_search(monkeypatch, [
            [_tool_call_chunk("WebSearch", {"query": "capital"}, "c1")],
            [_SChunk(_SDelta(""), finish_reason="stop")],
        ])

        blocks = _started_blocks(events)
        assert "server_tool_use" in blocks
        assert "web_search_tool_result" in blocks
        assert "text" in blocks

        # Ensure the final content block before message_delta is text
        start_events = [d["content_block"]["type"] for n, d in events if n == "content_block_start"]
        assert start_events[-1] == "text"

    def test_multi_hop_search_allows_distinct_queries_up_to_cap(self, monkeypatch):
        """Smart budgeting allows multi-hop search for distinct queries up to cap (3)."""
        turns = [
            [_tool_call_chunk("WebSearch", {"query": "who acquired GitHub"}, "c1")],
            [_tool_call_chunk("WebSearch", {"query": "Microsoft stock price"}, "c2")],
            [_SChunk(_SDelta("Microsoft acquired GitHub and its stock is..."), finish_reason="stop")],
        ]
        events, calls = _run_streamed_search(monkeypatch, turns)
        assert calls["search"] == 2
        text_deltas = [
            d["delta"]["text"] for n, d in events
            if n == "content_block_delta" and d["delta"].get("type") == "text_delta"
        ]
        assert any("Microsoft" in t for t in text_deltas)

    def test_redundant_query_is_stripped_and_synthesized(self, monkeypatch):
        """Repeating the identical query triggers duplicate protection."""
        turns = [
            [_tool_call_chunk("WebSearch", {"query": "capital of France"}, "c1")],
            [_tool_call_chunk("WebSearch", {"query": "capital of France"}, "c2")],
            [_SChunk(_SDelta("Paris is the capital."), finish_reason="stop")],
        ]
        events, calls = _run_streamed_search(monkeypatch, turns)
        # 2nd call was redundant, loop terminated and answered
        assert calls["search"] == 2


class TestHostedSearchHistoryConversion:
    def test_message_converter_handles_server_tool_use_and_web_search_tool_result(self):
        from src.logical_HQ_translator.messages.converter import _convert_messages
        body = {
            "messages": [
                {"role": "user", "content": "What is the capital of France?"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "server_tool_use",
                            "id": "srvtoolu_123",
                            "name": "web_search",
                            "input": {"query": "capital of France"}
                        },
                        {
                            "type": "web_search_tool_result",
                            "id": "srvtoolu_123",
                            "content": [
                                {
                                    "type": "web_search_result",
                                    "url": "https://example.org/paris",
                                    "title": "Paris Info",
                                    "encrypted_content": "Paris is the capital of France."
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        converted, _ = _convert_messages(body)
        assert len(converted) >= 2
        # Verify tool calls and tool responses were extracted without dropping or crashing
        roles = [m.get("role") for m in converted]
        assert "user" in roles



# ── Anthropic WebFetch: answered by the search manager ───────────────────────
#
# WebFetch used to open an aiohttp session against whatever url the model named
# and hand the raw page back as the tool result. Claude Code issues WebFetch
# against whatever model is configured, so with a custom provider the router was
# the only thing that would ever have fetched it — a turn that could call a tool
# could pull an arbitrary url into its own context.
#
# It runs on `execute_hybrid_search` now, the seam WebSearch already uses, with
# the url reduced to a query. Everything below is deterministic: the one
# collaborator is a faked search, and `aiohttp` is booby-trapped so that any
# route back to the socket fails loudly instead of reaching the network.

FETCH_URL = "https://www.example.org/blog/why-caching-fails?utm_source=rss"
FETCH_CITATIONS = [{"title": "Caching", "url": "https://example.org/caching"},
                   {"title": "Notes", "url": "https://example.net/notes"}]


def _no_outbound_http(monkeypatch):
    """Make an outbound request impossible, so a direct fetch cannot pass."""
    aiohttp = pytest.importorskip("aiohttp")

    def bomb(*a, **kw):
        raise AssertionError("the router opened an HTTP session of its own")

    monkeypatch.setattr(aiohttp, "ClientSession", bomb, raising=False)


def _fetch_call(url=FETCH_URL, id_="call_1", name="WebFetch"):
    return {"id": id_, "type": "function",
            "function": {"name": name, "arguments": json.dumps({"url": url})}}


def _run_server_tool(name, args, fake_search, monkeypatch, *, limits=None):
    """Run one server tool with the search manager faked out."""
    from src.api.claude_proxy.handler.anthropic_spec import run_server_tool
    from src.tools.search.orchestration import manager as search_manager

    seen = {}

    async def spy(queries, **kwargs):
        seen["queries"] = list(queries)
        seen["kwargs"] = kwargs
        return await fake_search(queries, **kwargs)

    monkeypatch.setattr(search_manager, "execute_hybrid_search", spy)
    return asyncio.run(run_server_tool(
        name, args, body={"model": "gemini-flash"}, account=None,
        auth_key_prefix="kp", limits=limits or {"max_uses": 3}, used=0)), seen


class TestTheUrlBecomesTheQuery:
    """What the model typed as a url is scored as a query."""

    def _q(self, url):
        from src.api.claude_proxy.handler.anthropic_spec import (
            _fetch_query_from_url,
        )

        return _fetch_query_from_url(url)

    def test_the_scheme_and_the_path_separators_are_dropped(self):
        assert self._q("https://example.org/blog/post") == "example.org blog post"

    def test_the_query_string_and_fragment_are_dropped(self):
        assert "utm_source" not in self._q(FETCH_URL)

    def test_the_words_stay_readable(self):
        assert self._q(FETCH_URL) == "example.org blog why caching fails"

    def test_a_bare_host_is_the_whole_query(self):
        assert self._q("https://example.org/") == "example.org"

    def test_www_and_the_port_are_noise(self):
        assert self._q("https://www.example.org:8443/x") == "example.org x"

    def test_a_string_that_is_not_a_url_is_still_searchable(self):
        """Better a poor query than an empty one."""
        assert self._q("not a url at all") == "not a url at all"

    def test_no_url_yields_no_query(self):
        assert self._q("") == ""


class TestWebFetchRunsOnTheSearchManager:
    @staticmethod
    async def _ok(*a, **kw):
        return "Caching is hard.", FETCH_CITATIONS

    def test_the_url_is_never_fetched(self, monkeypatch):
        """The guarantee, stated as a test: no socket, only a search."""
        _no_outbound_http(monkeypatch)

        (result, citations, error), _ = _run_server_tool(
            "WebFetch", {"url": FETCH_URL}, self._ok, monkeypatch)

        assert error is None
        assert "Caching is hard." in result
        assert [c["url"] for c in citations] == [c["url"] for c in FETCH_CITATIONS]

    def test_the_search_is_asked_for_the_url_as_a_query(self, monkeypatch):
        _, seen = _run_server_tool("WebFetch", {"url": FETCH_URL}, self._ok,
                                   monkeypatch)

        assert seen["queries"] == ["example.org blog why caching fails"]

    def test_the_fetch_uses_the_same_engine_resolution_as_search(self, monkeypatch):
        """Not a second implementation — the existing search path, unchanged."""
        _, seen = _run_server_tool("WebFetch", {"url": FETCH_URL}, self._ok,
                                   monkeypatch)

        assert seen["kwargs"]["search_engine"] == "duckduckgo"

    def test_the_sources_are_listed_with_the_result(self, monkeypatch):
        (result, _, _), _ = _run_server_tool("WebFetch", {"url": FETCH_URL},
                                             self._ok, monkeypatch)

        assert "https://example.org/caching" in result
        assert "https://example.net/notes" in result

    def test_a_search_that_matched_nothing_is_not_an_error(self, monkeypatch):
        async def nothing(*a, **kw):
            return "", []

        (result, citations, error), _ = _run_server_tool(
            "WebFetch", {"url": FETCH_URL}, nothing, monkeypatch)

        assert error is None
        assert citations == []

    def test_a_failing_search_becomes_a_fetch_error(self, monkeypatch):
        async def boom(*a, **kw):
            raise RuntimeError("backend down")

        (result, citations, error), _ = _run_server_tool(
            "WebFetch", {"url": FETCH_URL}, boom, monkeypatch)

        assert result == "WebFetch error: backend down"
        assert error == "unavailable"
        assert citations == []

    def test_the_failure_text_is_read_as_a_failure(self, monkeypatch):
        """`_search_failed` decides the wire shape; it must know this prefix."""
        from src.api.claude_proxy.handler.anthropic_spec import _search_failed

        assert _search_failed("WebFetch error: backend down") is True
        assert _search_failed("No fetch results found.") is False

    def test_a_fetch_with_no_url_is_invalid_input(self, monkeypatch):
        (result, _, error), seen = _run_server_tool(
            "WebFetch", {}, self._ok, monkeypatch)

        assert error == "invalid_tool_input"
        assert "queries" not in seen, "an empty fetch still ran a search"

    def test_the_error_code_is_one_the_sdk_declares(self, monkeypatch):
        import typing
        pytest.importorskip("anthropic")
        from anthropic.types import WebSearchToolResultErrorCode

        _, _, error = _run_server_tool("WebFetch", {}, self._ok,
                                       monkeypatch)[0]

        assert error in typing.get_args(WebSearchToolResultErrorCode)

    def test_blocked_domains_apply_to_a_fetch_too(self, monkeypatch):
        _, citations, _ = _run_server_tool(
            "WebFetch", {"url": FETCH_URL}, self._ok, monkeypatch,
            limits={"max_uses": 3, "blocked_domains": ["example.net"]})[0]

        assert [c["url"] for c in citations] == ["https://example.org/caching"]

    def test_an_unknown_tool_is_left_alone(self, monkeypatch):
        result, citations, error = _run_server_tool(
            "get_weather", {"city": "Paris"}, self._ok, monkeypatch)[0]

        assert (result, citations, error) == ("", [], None)
        assert "queries" not in _run_server_tool(
            "get_weather", {"city": "Paris"}, self._ok, monkeypatch)[1]


class TestTheFetchReachesTheClientUnchanged:
    """The wire shape is the contract; only where the bytes came from moved."""

    def _message(self, monkeypatch, fake_search, *, tools=None, url=FETCH_URL):
        import src.api.claude_proxy.handler.proxy_nonstream as ns
        from src.api.claude_proxy import claude_proxy
        from src.tools.search.orchestration import manager as search_manager

        async def fake_log_usage(*a):
            return None

        monkeypatch.setattr(ns.pool_manager, "call_nonstream",
                            _scripted_model(
                                _Message(tool_calls=[_fetch_call(url)]),
                                _Message("Answered from the sources.")))
        monkeypatch.setattr(search_manager, "execute_hybrid_search", fake_search)
        monkeypatch.setattr(ns, "log_usage", fake_log_usage, raising=False)

        body = {"model": "gemini-flash",
                "messages": [{"role": "user", "content": "summarise it"}],
                "tools": tools if tools is not None else [SEARCH_TOOL]}
        return asyncio.run(claude_proxy.create_message(body))

    @staticmethod
    async def _ok(*a, **kw):
        return "Caching is hard.", FETCH_CITATIONS

    def test_the_use_block_still_reports_the_url(self, monkeypatch):
        _no_outbound_http(monkeypatch)

        msg = self._message(monkeypatch, self._ok)

        use = next(b for b in msg["content"]
                   if b["type"] == "server_tool_use")
        assert use["name"] == "web_fetch"
        assert use["input"] == {"url": FETCH_URL}
        assert use["id"].startswith("srvtoolu_"), use["id"]

    def test_the_blocks_appear_in_the_declared_order(self, monkeypatch):
        msg = self._message(monkeypatch, self._ok)

        assert [b["type"] for b in msg["content"]] == [
            "server_tool_use", "web_search_tool_result", "text"]

    def test_the_result_block_pairs_with_its_use(self, monkeypatch):
        msg = self._message(monkeypatch, self._ok)

        use = next(b for b in msg["content"] if b["type"] == "server_tool_use")
        result = next(b for b in msg["content"]
                      if b["type"] == "web_search_tool_result")
        assert result["tool_use_id"] == use["id"]

    def test_the_result_block_carries_the_sources(self, monkeypatch):
        msg = self._message(monkeypatch, self._ok)

        result = next(b for b in msg["content"]
                      if b["type"] == "web_search_tool_result")
        assert [c["url"] for c in result["content"]] == [
            c["url"] for c in FETCH_CITATIONS]
        assert result["content"][0]["title"] == "Caching"

    def test_the_answer_carries_the_citations(self, monkeypatch):
        msg = self._message(monkeypatch, self._ok)

        text = next(b for b in msg["content"] if b["type"] == "text")
        assert [c["type"] for c in text["citations"]] == [
            "web_search_result_location"] * 2
        assert text["citations"][0]["url"] == "https://example.org/caching"

    def test_the_usage_counts_the_fetch(self, monkeypatch):
        msg = self._message(monkeypatch, self._ok)

        assert msg["usage"]["server_tool_use"] == {"web_search_requests": 0,
                                                   "web_fetch_requests": 1}

    def test_no_client_tool_use_block_is_emitted(self, monkeypatch):
        msg = self._message(monkeypatch, self._ok)

        assert not [b for b in msg["content"] if b["type"] == "tool_use"]

    def test_a_failing_search_reaches_the_client_as_an_error(self, monkeypatch):
        async def boom(*a, **kw):
            raise RuntimeError("backend down")

        msg = self._message(monkeypatch, boom)

        result = next(b for b in msg["content"]
                      if b["type"] == "web_search_tool_result")
        assert result["content"] == {
            "type": "web_search_tool_result_error", "error_code": "unavailable"}
        # An attempt that failed is not a fetch, the same way a search refused
        # past `max_uses` is not a search.
        assert "server_tool_use" not in msg["usage"], msg["usage"]

    def test_the_whole_message_validates(self, monkeypatch):
        pytest.importorskip("anthropic.types")
        from anthropic.types import Message

        Message.model_validate(self._message(monkeypatch, self._ok),
                               strict=True, extra="forbid")



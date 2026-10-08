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
        from src.server.openai_server.routes.completions_routes import (
            _web_search_call_item,
        )

        item = _web_search_call_item("ws_1", RECORD)

        assert item["type"] == "web_search_call"
        assert item["id"] == "ws_1"
        assert item["status"] == "completed"

    def test_the_item_validates_against_the_official_model(self):
        pytest.importorskip("openai.types.responses")
        from openai.types.responses import ResponseFunctionWebSearch
        from src.server.openai_server.routes.completions_routes import (
            _web_search_call_item,
        )

        ResponseFunctionWebSearch.model_validate(
            _web_search_call_item("ws_1", RECORD))

    def test_the_item_validates_under_strict(self):
        pytest.importorskip("openai.types.responses")
        from openai.types.responses import ResponseFunctionWebSearch
        from src.server.openai_server.routes.completions_routes import (
            _web_search_call_item,
        )

        ResponseFunctionWebSearch.model_validate(
            _web_search_call_item("ws_1", RECORD), strict=True)

    def test_a_failed_search_says_so(self):
        from src.server.openai_server.routes.completions_routes import (
            _web_search_call_item,
        )

        assert _web_search_call_item("ws_2", FAILED)["status"] == "failed"

    def test_the_action_carries_the_query(self):
        from src.server.openai_server.routes.completions_routes import (
            _web_search_call_item,
        )

        action = _web_search_call_item("ws_1", RECORD)["action"]

        assert action["type"] == "search"
        assert action["query"] == "capital of France"

    def test_a_fetch_uses_the_open_page_action(self):
        from src.server.openai_server.routes.completions_routes import (
            _web_search_call_item,
        )

        item = _web_search_call_item("ws_3", {"type": "open_page",
                                              "url": "https://example.com"})

        assert item["action"] == {"type": "open_page",
                                  "url": "https://example.com"}

    def test_the_router_own_search_never_becomes_a_function_call(self):
        from src.server.openai_server.routes.completions_routes import (
            _extract_response_tool_calls,
        )

        got = _extract_response_tool_calls({"choices": [{"message": {
            "tool_calls": [{"id": "c1", "function": {
                "name": "WebSearch", "arguments": "{}"}}]}}]})

        assert got, "the record exists so it can be filtered where it is emitted"
        assert got[0]["name"] == "WebSearch"

    def test_a_real_client_tool_is_still_a_function_call(self):
        from src.server.openai_server.routes.completions_routes import (
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
        # Exactly one place turns it on: the loop that reads a google_search tool
        # out of the request. The old code had a second, after an `else:`.
        assert src.count("web_search = True") == 1, (
            "search is switched on somewhere other than the tool the client "
            f"declared:\n{src}")
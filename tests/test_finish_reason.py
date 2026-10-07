"""Upstream finish reasons are not OpenAI finish reasons.

`finish_reason` is a `Literal` in the OpenAI SDK, so a value outside
`stop`/`length`/`tool_calls`/`content_filter`/`function_call` makes the whole
response fail to parse for a strict client.

The router relayed upstream's value verbatim. Gemini's FinishReason includes
`SAFETY`, `RECITATION`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `SPII`,
`IMAGE_SAFETY`, `MALFORMED_FUNCTION_CALL` and `UNEXPECTED_TOOL_CALL`, and one of
them was observed reaching a live client as `finish_reason:
"malformed_function_call"` with `tool_calls: null` — the single most
invite-a-bug-report string in the enum's place.

The streaming path reduced everything else to `length` or `stop`, which threw
away `content_filter` entirely.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.providers.finish_reason import normalize_finish_reason

OPENAI_ENUM = ("stop", "length", "tool_calls", "content_filter", "function_call")


class TestMapsOntoTheOpenAIEnum:
    @pytest.mark.parametrize("reason", [
        "STOP", "stop", "Stop", None, "", "MALFORMED_FUNCTION_CALL",
        "UNEXPECTED_TOOL_CALL", "OTHER", "LANGUAGE", "something_new",
        "malformed_function_call", "unexpected_tool_call",
    ])
    def test_anything_unmapped_becomes_stop(self, reason):
        """An unknown string cannot be passed through — that is the bug."""
        assert normalize_finish_reason(reason) == "stop"

    @pytest.mark.parametrize("reason", [
        "MAX_TOKENS", "max_tokens", "length", "MAX_TOKENS_EXCEEDED",
    ])
    def test_a_token_ceiling_becomes_length(self, reason):
        assert normalize_finish_reason(reason) == "length"

    @pytest.mark.parametrize("reason", [
        "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII",
        "IMAGE_SAFETY", "NO_IMAGE", "safety", "recitation",
    ])
    def test_a_withheld_answer_becomes_content_filter(self, reason):
        assert normalize_finish_reason(reason) == "content_filter"

    def test_every_gemini_reason_maps_to_a_legal_value(self):
        from google.genai.types import FinishReason

        for member in FinishReason:
            got = normalize_finish_reason(member.name)
            assert got in OPENAI_ENUM, f"{member.name} -> {got!r}"


class TestToolCallsWin:
    """If a function call is in the payload, that is the useful thing to say,
    whatever upstream called the turn."""

    @pytest.mark.parametrize("reason", ["STOP", "MALFORMED_FUNCTION_CALL",
                                        "UNEXPECTED_TOOL_CALL", None])
    def test_a_call_in_the_payload_reports_tool_calls(self, reason):
        assert normalize_finish_reason(reason, has_tool_calls=True) == "tool_calls"

    def test_a_call_is_reported_even_when_the_reason_says_filter(self):
        assert normalize_finish_reason("SAFETY", has_tool_calls=True) == "tool_calls"

    def test_without_a_call_a_malformed_reason_does_not_claim_one(self):
        """There is nothing to execute, so claiming tool_calls would leave a
        client waiting on a call that never arrives."""
        assert normalize_finish_reason("MALFORMED_FUNCTION_CALL") == "stop"


class TestReachesTheWire:
    """The helpers are only worth anything if the builders use them."""

    def test_the_chat_response_builder_normalizes(self, monkeypatch):
        import asyncio

        from src.api.opencode_proxy.handler import response as response_mod

        # build_response ends with asyncio.ensure_future(log_usage(...)), which
        # needs a current loop in this thread. Not the subject of this test, and
        # whether one exists depends on what ran before it in the suite.
        async def _no_log(*_a, **_kw):
            return None

        monkeypatch.setattr(response_mod, "log_usage", _no_log)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        from src.api.opencode_proxy.handler.response import build_response

        class _Fn:
            name = "get_weather"
            arguments = '{"city":"Paris"}'

        class _Tc:
            id = "call_1"
            type = "function"
            function = _Fn()

        class _Msg:
            role = "assistant"
            content = None
            reasoning_content = ""
            thought_signature = None
            tool_calls = [_Tc()]

        class _Choice:
            finish_reason = "MALFORMED_FUNCTION_CALL"
            message = _Msg()

        class _Resp:
            model = "gemini-flash"
            choices = [_Choice()]
            usage = None

        # build_response logs usage, which needs a running loop; an earlier test
        # in the suite may have left the thread without one.
        try:
            out = build_response({"model": "gemini-flash"}, _Resp(),
                                 "gemini-flash", "sk-x", 10)
        finally:
            asyncio.set_event_loop(None)
            loop.close()

        assert out["choices"][0]["finish_reason"] == "tool_calls"

    def test_the_responses_route_normalizes(self):
        from src.server.openai_server.routes.completions_routes import (
            _extract_finish_reason,
        )

        assert _extract_finish_reason(
            {"choices": [{"finish_reason": "MALFORMED_FUNCTION_CALL"}]}) == "stop"
        assert _extract_finish_reason(
            {"choices": [{"finish_reason": "MAX_TOKENS"}]}) == "length"
        assert _extract_finish_reason(
            {"choices": [{"finish_reason": "STOP", "message": {
                "tool_calls": [{"id": "c"}]}}]}) == "tool_calls"
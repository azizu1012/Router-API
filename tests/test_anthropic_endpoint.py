"""A custom endpoint belongs to whoever paid for it, so the router speaks the
owner's dialect rather than assuming OpenAI.

The failure mode this guards against is silent in both directions. A request
shaped for the wrong dialect still returns HTTP 200 with a plausible body, and
the client sees an empty answer instead of an error -- the same class of bug the
schema sanitizer and the dead-key classifier both came out of.

The tests below cover the four disagreements between the two dialects, and the
URL join, which is the same /v1/v1 trap the client-facing routes already had.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.providers.anthropic_wire import (
    anthropic_event_to_openai_delta,
    anthropic_to_openai_message,
    anthropic_to_openai_usage,
    is_anthropic_format,
    normalize_api_format,
    openai_to_anthropic_body,
)
from src.core.providers.custom_endpoint_client import anthropic_messages_url


class TestTheFormatIsDeclaredAndDefaultsSafely:
    """An endpoint predating the column reads as openai, which is what every
    endpoint used to be. Defaulting the other way breaks them all silently."""

    @pytest.mark.parametrize("value", ["anthropic", "Anthropic", " ANTHROPIC ", "anthropic"])
    def test_anthropic_is_recognised(self, value):
        assert is_anthropic_format(value) is True

    @pytest.mark.parametrize("value", [
        "openai", "OpenAI", "", None, "unknown", "gemini", 0, 1, ["anthropic"],
    ])
    def test_everything_else_is_openai(self, value):
        assert is_anthropic_format(value) is False

    def test_normalize_returns_a_supported_name(self):
        assert normalize_api_format("Anthropic") == "anthropic"
        assert normalize_api_format("nonsense") == "openai"


class TestTheUrlIsJoinedExactlyOnce:
    """Operators paste base URLs both with and without /v1. Naive joining
    produces /v1/v1/messages for the second form, which 404s."""

    @pytest.mark.parametrize("base,expected", [
        ("https://h", "https://h/v1/messages"),
        ("https://h/", "https://h/v1/messages"),
        ("https://h/v1", "https://h/v1/messages"),
        ("https://h/v1/", "https://h/v1/messages"),
        ("https://h/api/v1", "https://h/api/v1/messages"),
    ])
    def test_join(self, base, expected):
        assert anthropic_messages_url(base) == expected

    def test_never_produces_a_doubled_v1(self):
        for base in ("https://h", "https://h/", "https://h/v1", "https://h/v1/"):
            assert "/v1/v1/" not in anthropic_messages_url(base)


class TestSystemBecomesATopLevelField:
    """Anthropic has no system role; a system message must be lifted out or the
    endpoint rejects the request."""

    def test_system_message_is_lifted(self):
        body = openai_to_anthropic_body(
            "m", [{"role": "system", "content": "be terse"},
                  {"role": "user", "content": "hi"}])
        assert body["system"] == "be terse"
        assert all(m["role"] != "system" for m in body["messages"])

    def test_developer_role_is_treated_as_system(self):
        body = openai_to_anthropic_body(
            "m", [{"role": "developer", "content": "rules"},
                  {"role": "user", "content": "hi"}])
        assert body["system"] == "rules"

    def test_several_system_messages_join_in_order(self):
        body = openai_to_anthropic_body(
            "m", [{"role": "system", "content": "one"},
                  {"role": "system", "content": "two"},
                  {"role": "user", "content": "hi"}])
        assert body["system"] == "one\n\ntwo"

    def test_no_system_field_when_there_is_none(self):
        body = openai_to_anthropic_body("m", [{"role": "user", "content": "hi"}])
        assert "system" not in body


class TestToolsAreReshapedNotDropped:
    def test_openai_function_wrapper_is_unwrapped(self):
        body = openai_to_anthropic_body(
            "m", [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {
                "name": "get_weather",
                "description": "look it up",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            }}])
        assert body["tools"] == [{
            "name": "get_weather",
            "description": "look it up",
            "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
        }]
        assert "type" not in body["tools"][0]

    def test_a_tool_without_parameters_still_has_a_schema(self):
        body = openai_to_anthropic_body(
            "m", [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "ping"}}])
        assert body["tools"][0]["input_schema"] == {"type": "object", "properties": {}}

    def test_a_malformed_tool_is_skipped_not_fatal(self):
        body = openai_to_anthropic_body(
            "m", [{"role": "user", "content": "hi"}],
            tools=[{"type": "function"}, "nonsense", {"type": "function", "function": {"name": "ok"}}])
        assert [t["name"] for t in body["tools"]] == ["ok"]


class TestToolResultsAndToolCallsSurviveTheRoundTrip:
    """Anthropic nests a tool result inside a user turn. Kept as its own turn it
    is a validation error, so the loss would be the whole conversation."""

    def test_openai_tool_message_becomes_a_tool_result_block(self):
        body = openai_to_anthropic_body("m", [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "18C"},
        ])
        last = body["messages"][-1]
        assert last["role"] == "user"
        assert last["content"][0]["type"] == "tool_result"
        assert last["content"][0]["tool_use_id"] == "call_1"

    def test_assistant_tool_call_becomes_a_tool_use_block(self):
        body = openai_to_anthropic_body("m", [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'}}]},
        ])
        blocks = body["messages"][-1]["content"]
        assert blocks[0]["type"] == "tool_use"
        assert blocks[0]["input"] == {"city": "Paris"}

    def test_unparseable_arguments_do_not_lose_the_call(self):
        body = openai_to_anthropic_body("m", [
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "c", "type": "function",
                "function": {"name": "f", "arguments": "not json"}}]}])
        assert body["messages"][-1]["content"][0]["type"] == "tool_use"


class TestTheRequestIsAlwaysAcceptedByAnthropic:
    def test_max_tokens_is_always_present_and_positive(self):
        assert openai_to_anthropic_body("m", [{"role": "user", "content": "x"}])["max_tokens"] > 0
        assert openai_to_anthropic_body(
            "m", [{"role": "user", "content": "x"}], max_tokens=0)["max_tokens"] > 0

    def test_consecutive_user_turns_are_merged(self):
        body = openai_to_anthropic_body("m", [
            {"role": "user", "content": "one"},
            {"role": "user", "content": "two"}])
        assert len(body["messages"]) == 1

    def test_an_empty_history_still_produces_one_turn(self):
        body = openai_to_anthropic_body("m", [])
        assert len(body["messages"]) == 1

    def test_openai_stop_is_renamed_to_stop_sequences(self):
        body = openai_to_anthropic_body(
            "m", [{"role": "user", "content": "x"}], extra_body={"stop": "END"})
        assert body["stop_sequences"] == ["END"]
        assert "stop" not in body

    def test_a_list_stop_stays_a_list(self):
        body = openai_to_anthropic_body(
            "m", [{"role": "user", "content": "x"}], extra_body={"stop": ["a", "b"]})
        assert body["stop_sequences"] == ["a", "b"]

    def test_extra_body_cannot_overwrite_the_canonical_fields(self):
        body = openai_to_anthropic_body(
            "m", [{"role": "user", "content": "x"}],
            extra_body={"messages": [{"role": "user", "content": "evil"}],
                        "model": "other", "system": "hijack"})
        # Content is always emitted as blocks. Anthropic accepts a bare string
        # too, but a string cannot carry an image, and one message in the middle
        # of a conversation switching between the two shapes is how a picture
        # ends up silently dropped.
        assert body["messages"][0]["content"] == [{"type": "text", "text": "x"}]
        assert body["model"] == "m"
        assert body.get("system") != "hijack"


class TestImagesSurvive:
    """Flattening content to text is how the picture disappears: a request
    still returns 200, the answer just ignores what the user pointed at."""

    def test_an_openai_data_url_becomes_a_base64_source(self):
        body = openai_to_anthropic_body("m", [{"role": "user", "content": [
            {"type": "text", "text": "so do?"},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
        ]}])
        blocks = body["messages"][0]["content"]
        assert blocks[0] == {"type": "text", "text": "so do?"}
        assert blocks[1] == {"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}

    def test_a_remote_url_becomes_a_url_source(self):
        body = openai_to_anthropic_body("m", [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "https://x.example/a.png"}}]}])
        assert body["messages"][0]["content"][0] == {
            "type": "image", "source": {"type": "url", "url": "https://x.example/a.png"}}

    def test_an_anthropic_image_block_passes_through_untouched(self):
        native = {"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": "AAA"}}
        body = openai_to_anthropic_body(
            "m", [{"role": "user", "content": [native]}])
        assert body["messages"][0]["content"][0] == native

    def test_jpg_is_normalised_because_anthropic_rejects_it(self):
        """`image/jpg` is the common non-standard label and it is a 400 upstream
        (hermes-agent#55432). Media types are an enum, not free text."""
        body = openai_to_anthropic_body("m", [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": "data:image/jpg;base64,QUJD"}}]}])
        assert body["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"

    @pytest.mark.parametrize("label,expected", [
        ("image/jpeg", "image/jpeg"), ("image/png", "image/png"),
        ("image/gif", "image/gif"), ("image/webp", "image/webp"),
        ("image/jpg", "image/jpeg"),
    ])
    def test_every_supported_media_type_lands_on_its_enum_value(self, label, expected):
        body = openai_to_anthropic_body("m", [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:{label};base64,QUJD"}}]}])
        assert body["messages"][0]["content"][0]["source"]["media_type"] == expected

    def test_an_image_reaching_a_tool_result_is_kept(self):
        """The spec allows tool_result content to be blocks, so a tool that
        returns a picture keeps it."""
        body = openai_to_anthropic_body("m", [
            {"role": "user", "content": "screenshot?"},
            {"role": "tool", "tool_call_id": "c1", "content": [
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}]},
        ])
        block = body["messages"][-1]["content"][0]
        assert block["type"] == "tool_result"
        assert block["content"][0]["type"] == "image"

    def test_tool_result_stays_a_string_when_there_is_only_text(self):
        body = openai_to_anthropic_body("m", [
            {"role": "tool", "tool_call_id": "c1", "content": "18C"}])
        assert body["messages"][-1]["content"][0]["content"] == "18C"

    def test_a_tool_result_stays_ahead_of_an_image(self):
        """Anthropic requires tool_result first in the user turn."""
        body = openai_to_anthropic_body("m", [
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "18C"},
        ])
        types = [b["type"] for b in body["messages"][-1]["content"]]
        assert types.index("tool_result") < types.index("image")

    def test_an_unreadable_image_url_drops_that_part_not_the_whole_message(self):
        body = openai_to_anthropic_body("m", [{"role": "user", "content": [
            {"type": "text", "text": "keep me"},
            {"type": "image_url", "image_url": {"url": ""}}]}])
        assert body["messages"][0]["content"] == [{"type": "text", "text": "keep me"}]


class TestToolChoiceIsSpelledTheWayAnthropicSpellsIt:
    """OpenAI's `required` has no Anthropic equivalent as a string, and its
    named-function object has no meaning there at all. Both are a 400."""

    @pytest.mark.parametrize("openai_value,expected", [
        ("auto", {"type": "auto"}),
        ("none", {"type": "none"}),
        ("required", {"type": "any"}),
    ])
    def test_string_spellings(self, openai_value, expected):
        body = openai_to_anthropic_body(
            "m", [{"role": "user", "content": "x"}],
            tools=[{"type": "function", "function": {"name": "f"}}],
            extra_body={"tool_choice": openai_value})
        assert body["tool_choice"] == expected

    def test_a_named_function_becomes_a_tool_choice(self):
        body = openai_to_anthropic_body(
            "m", [{"role": "user", "content": "x"}],
            tools=[{"type": "function", "function": {"name": "f"}}],
            extra_body={"tool_choice": {"type": "function",
                                        "function": {"name": "f"}}})
        assert body["tool_choice"] == {"type": "tool", "name": "f"}

    def test_no_tool_choice_field_when_the_client_sent_none(self):
        body = openai_to_anthropic_body(
            "m", [{"role": "user", "content": "x"}],
            tools=[{"type": "function", "function": {"name": "f"}}])
        assert "tool_choice" not in body


class TestTheResponseComesBackAsOpenAI:
    def test_text_becomes_content_and_stop_becomes_finish_reason(self):
        out = anthropic_to_openai_message({
            "id": "msg_1", "model": "claude-x", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "hello"}]})
        assert out["choices"][0]["message"]["content"] == "hello"
        assert out["choices"][0]["finish_reason"] == "stop"

    @pytest.mark.parametrize("reason,expected", [
        ("end_turn", "stop"), ("stop_sequence", "stop"),
        ("max_tokens", "length"), ("tool_use", "tool_calls"),
    ])
    def test_stop_reason_maps_into_the_openai_enum(self, reason, expected):
        out = anthropic_to_openai_message({"stop_reason": reason, "content": []})
        assert out["choices"][0]["finish_reason"] == expected

    def test_tool_use_becomes_an_openai_tool_call(self):
        out = anthropic_to_openai_message({
            "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": "tu_1", "name": "f", "input": {"a": 1}}]})
        tc = out["choices"][0]["message"]["tool_calls"][0]
        assert tc["id"] == "tu_1"
        assert tc["function"]["name"] == "f"
        assert json.loads(tc["function"]["arguments"]) == {"a": 1}

    def test_cache_tokens_fold_into_prompt_tokens(self):
        usage = anthropic_to_openai_usage({
            "input_tokens": 10, "output_tokens": 5,
            "cache_read_input_tokens": 7, "cache_creation_input_tokens": 3})
        assert usage["prompt_tokens"] == 20
        assert usage["completion_tokens"] == 5
        assert usage["total_tokens"] == 25

    def test_a_response_with_no_content_is_still_well_formed(self):
        out = anthropic_to_openai_message({"content": [], "stop_reason": "end_turn"})
        assert out["choices"][0]["message"]["content"] == ""
        assert "tool_calls" not in out["choices"][0]["message"]


class TestStreamEventsBecomeOpenAIChunks:
    def test_text_delta_becomes_content(self):
        chunk = anthropic_event_to_openai_delta({
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "hi"}})
        assert chunk["choices"][0]["delta"]["content"] == "hi"

    def test_thinking_delta_becomes_reasoning_content(self):
        chunk = anthropic_event_to_openai_delta({
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "hmm"}})
        assert chunk["choices"][0]["delta"]["reasoning_content"] == "hmm"

    def test_tool_use_start_opens_a_tool_call(self):
        chunk = anthropic_event_to_openai_delta({
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "tu_1", "name": "f", "input": {}}})
        tc = chunk["choices"][0]["delta"]["tool_calls"][0]
        assert tc["id"] == "tu_1"
        assert tc["function"]["name"] == "f"

    def test_partial_json_streams_into_arguments(self):
        chunk = anthropic_event_to_openai_delta({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"a":'}})
        assert chunk["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] == '{"a":'

    def test_message_delta_carries_the_finish_reason(self):
        chunk = anthropic_event_to_openai_delta({
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"input_tokens": 3, "output_tokens": 4}})
        assert chunk["choices"][0]["finish_reason"] == "tool_calls"
        assert chunk["usage"]["total_tokens"] == 7

    @pytest.mark.parametrize("event", [
        {"type": "message_start", "message": {}},
        {"type": "content_block_stop", "index": 0},
        {"type": "ping"},
        {"type": "message_stop"},
        {"type": "content_block_delta", "delta": {"type": "signature_delta"}},
        {},
    ])
    def test_events_with_no_delta_produce_nothing(self, event):
        """A chunk with an empty delta is not the same as no chunk: a strict
        client validates the frame and rejects one that carries nothing."""
        assert anthropic_event_to_openai_delta(event) is None
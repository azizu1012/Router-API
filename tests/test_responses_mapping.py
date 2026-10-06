"""The Responses API must not silently drop what the client sent.

/v1/responses used to forward only model, input, temperature, top_p and
max_output_tokens. tools, instructions and stream were all discarded, with no
error and no log. A client asking for hosted web search got back a confident
answer to a question that had never been searched — indistinguishable from a
model that simply made things up.

These pin the mapping in both directions: each Responses field reaches the chat
body, and each Responses field that has no chat equivalent is either mapped to a
router-native capability or refused loudly rather than dropped.
"""

import pytest

from src.api.opencode_proxy.handler.responses_mapping import (
    hosted_tool_names,
    responses_to_chat_body,
    split_tools,
)


class TestInputBecomesMessages:
    def test_a_plain_string_input_is_one_user_message(self):
        chat = responses_to_chat_body({"model": "m", "input": "hello"})
        assert chat["messages"] == [{"role": "user", "content": "hello"}]

    def test_a_list_input_keeps_roles(self):
        chat = responses_to_chat_body({
            "model": "m",
            "input": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        })
        assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]

    def test_an_untyped_input_item_becomes_a_user_message(self):
        chat = responses_to_chat_body({"model": "m", "input": ["bare string"]})
        assert chat["messages"][0] == {"role": "user", "content": "bare string"}

    def test_structured_content_parts_are_flattened_to_text(self):
        chat = responses_to_chat_body({
            "model": "m",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "part"}]}],
        })
        assert chat["messages"][0]["content"] == "part", chat["messages"]

    def test_an_empty_input_produces_no_messages(self):
        chat = responses_to_chat_body({"model": "m", "input": []})
        assert chat["messages"] == []


class TestInstructionsWereBeingDropped:
    def test_instructions_become_a_system_message(self):
        chat = responses_to_chat_body({"model": "m", "input": "hi", "instructions": "be terse"})
        assert chat["messages"][0] == {"role": "system", "content": "be terse"}

    def test_the_system_message_precedes_the_user_turn(self):
        chat = responses_to_chat_body({"model": "m", "input": "hi", "instructions": "be terse"})
        assert [m["role"] for m in chat["messages"]] == ["system", "user"]

    def test_blank_instructions_add_no_message(self):
        chat = responses_to_chat_body({"model": "m", "input": "hi", "instructions": "   "})
        assert all(m["role"] != "system" for m in chat["messages"]), chat["messages"]

    def test_list_instructions_are_joined(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi",
            "instructions": [{"type": "input_text", "text": "a"}, {"type": "input_text", "text": "b"}],
        })
        assert chat["messages"][0]["content"] == "ab", chat["messages"]


class TestHostedSearchMapsToRouterNative:
    """The Responses dialect asks for the hosted capability, so it opts into
    grounding. Other chat paths stay on DuckDuckGo at no Gemini quota."""

    def test_web_search_tool_turns_on_search(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi", "tools": [{"type": "web_search"}],
        })
        assert chat.get("web_search") is True, chat

    def test_web_search_selects_the_grounding_first_engine(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi", "tools": [{"type": "web_search"}],
        })
        assert chat.get("search_engine") == "auto", chat

    @pytest.mark.parametrize("tool_type", [
        "web_search", "web_search_preview", "web_search_2025_08_26",
    ])
    def test_every_hosted_search_spelling_is_recognised(self, tool_type):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi", "tools": [{"type": tool_type}],
        })
        assert chat.get("web_search") is True, tool_type

    def test_a_hosted_search_tool_is_not_forwarded_as_a_function(self):
        """It has no chat-path schema; forwarding it would send a broken tool."""
        chat = responses_to_chat_body({
            "model": "m", "input": "hi", "tools": [{"type": "web_search"}],
        })
        assert "tools" not in chat, chat

    def test_an_explicit_engine_still_wins_over_the_dialect_default(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi",
            "tools": [{"type": "web_search"}],
            "search_engine": "duckduckgo",
        })
        assert chat.get("search_engine") == "duckduckgo", chat

    def test_url_context_maps_to_fetch(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi", "tools": [{"type": "url_context"}],
        })
        assert chat.get("web_fetch") is True, chat

    def test_no_search_tools_means_no_search_flag(self):
        chat = responses_to_chat_body({"model": "m", "input": "hi"})
        assert "web_search" not in chat, chat
        assert "search_engine" not in chat, chat


class TestFunctionToolsSurvive:
    def test_a_flat_function_tool_is_normalised(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi",
            "tools": [{"name": "Bash", "description": "shell",
                       "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}}],
        })
        fn = chat["tools"][0]["function"]
        assert fn["name"] == "Bash", chat["tools"]
        assert "cmd" in fn["parameters"]["properties"], chat["tools"]

    def test_a_nested_function_tool_is_kept(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi",
            "tools": [{"type": "function", "function": {"name": "Read", "parameters": {}}}],
        })
        assert chat["tools"][0]["function"]["name"] == "Read", chat["tools"]

    def test_input_schema_is_accepted_as_a_parameters_alias(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi",
            "tools": [{"name": "Read", "input_schema": {"type": "object"}}],
        })
        assert chat["tools"][0]["function"]["parameters"] == {"type": "object"}, chat["tools"]

    def test_a_tool_with_no_parameters_gets_an_object_schema(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi", "tools": [{"name": "Now"}],
        })
        assert chat["tools"][0]["function"]["parameters"]["type"] == "object", chat["tools"]

    def test_function_and_hosted_tools_coexist(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi",
            "tools": [{"name": "Bash", "parameters": {}}, {"type": "web_search"}],
        })
        assert chat["tools"][0]["function"]["name"] == "Bash", chat["tools"]
        assert chat["web_search"] is True, chat

    def test_a_nameless_tool_is_dropped_rather_than_sent_broken(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi", "tools": [{"type": "function"}],
        })
        assert "tools" not in chat, chat

    def test_tool_choice_is_forwarded(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi", "tools": [{"name": "Bash"}],
            "tool_choice": "none",
        })
        assert chat["tool_choice"] == "none", chat


class TestSamplingAndStream:
    def test_max_output_tokens_is_forwarded(self):
        chat = responses_to_chat_body({"model": "m", "input": "hi", "max_output_tokens": 512})
        assert chat["max_tokens"] == 512, chat

    def test_max_tokens_is_accepted_as_an_alias(self):
        chat = responses_to_chat_body({"model": "m", "input": "hi", "max_tokens": 256})
        assert chat["max_tokens"] == 256, chat

    def test_temperature_and_top_p_are_forwarded(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi", "temperature": 0.2, "top_p": 0.8,
        })
        assert chat["temperature"] == 0.2 and chat["top_p"] == 0.8, chat

    def test_stream_true_is_forwarded(self):
        """Was silently dropped, so an SSE client got a single JSON body."""
        chat = responses_to_chat_body({"model": "m", "input": "hi", "stream": True})
        assert chat.get("stream") is True, chat

    def test_stream_absent_means_no_stream_key(self):
        chat = responses_to_chat_body({"model": "m", "input": "hi"})
        assert "stream" not in chat, chat

    def test_reasoning_effort_is_translated(self):
        chat = responses_to_chat_body({
            "model": "m", "input": "hi", "reasoning": {"effort": "high"},
        })
        assert chat["reasoning_effort"] == "high", chat

    def test_an_empty_reasoning_block_adds_nothing(self):
        chat = responses_to_chat_body({"model": "m", "input": "hi", "reasoning": {}})
        assert "reasoning_effort" not in chat, chat


class TestHelpers:
    def test_hosted_tool_names_lists_what_was_asked_for(self):
        assert hosted_tool_names({"tools": [{"type": "web_search"}, {"type": "url_context"}]}) == \
            ["web_search", "web_fetch"]

    def test_hosted_tool_names_is_empty_for_function_tools_only(self):
        assert hosted_tool_names({"tools": [{"name": "Bash"}]}) == []

    def test_split_tools_reports_each_capability(self):
        functions, hosted, wants_search, wants_fetch = split_tools(
            [{"type": "web_search"}, {"name": "Bash", "parameters": {}}]
        )
        assert wants_search is True and wants_fetch is False
        assert hosted == ["web_search"]
        assert len(functions) == 1

    def test_split_tools_tolerates_garbage(self):
        functions, hosted, s, f = split_tools(["nonsense", None, 42])
        assert (functions, hosted, s, f) == ([], [], False, False)

    def test_a_body_with_no_model_does_not_invent_one(self):
        chat = responses_to_chat_body({"input": "hi"})
        assert "model" not in chat, chat

"""tool_choice has to survive all the way to whichever provider answers.

It was read on the Anthropic path only to strip the tools list on `none`; the
choice itself never reached a provider, so `any` and a forced tool were both
silent no-ops. Three dialects spell it three ways, and forwarding one dialect's
spelling to another is a 400 rather than a fallback:

  OpenAI    "none" | "auto" | "required" | {"type":"function","function":{"name":...}}
  Anthropic {"type":"auto"|"any"|"none"} | {"type":"tool","name":...}
  Gemini    FunctionCallingConfig(mode=AUTO|ANY|NONE, allowed_function_names=[...])

Gemini has no primitive for "call this exact function". The documented way is
mode ANY with that one name in allowed_function_names, so that is what a forced
tool becomes.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.claude_proxy.handler.anthropic_spec import extract_sampling_params, extract_tool_choice
from src.api.opencode_proxy.handler.proxy import _client_sampling_params
from src.core.providers.gemini_facade import _gemini_tool_config


def fcc(choice):
    out = _gemini_tool_config(choice)
    return (out or {}).get("function_calling_config")


class TestAnAnthropicClientBecomesTheCanonicalShape:
    def test_the_whole_sampling_channel_carries_the_choice(self):
        """The Anthropic proxy already had a channel into acompletion kwargs;
        the choice simply was not put on it."""
        out = extract_sampling_params({"tool_choice": {"type": "any"},
                                       "top_p": 0.5})
        assert out["tool_choice"] == "required"
        assert out["top_p"] == 0.5

    def test_sampling_params_still_works_without_tool_choice(self):
        assert "tool_choice" not in extract_sampling_params({"top_p": 0.9})


class TestAnOpenAIClientIsLeftAlone:
    @pytest.mark.parametrize("value", [
        "auto", "none", "required",
        {"type": "function", "function": {"name": "get_weather"}},
    ])
    def test_passes_through_verbatim(self, value):
        assert _client_sampling_params({"tool_choice": value})["tool_choice"] == value

    def test_stop_becomes_a_stop_sequences_list(self):
        assert _client_sampling_params({"stop": "END"})["stop_sequences"] == ["END"]
        assert _client_sampling_params(
            {"stop": ["A", "B"]})["stop_sequences"] == ["A", "B"]

    def test_parallel_false_is_carried_but_true_is_not_invented(self):
        assert _client_sampling_params(
            {"parallel_tool_calls": False})["parallel_tool_calls"] is False
        assert "parallel_tool_calls" not in _client_sampling_params({})


class TestGeminiGetsItsOwnSpelling:
    @pytest.mark.parametrize("value,expected", [
        ("auto", "AUTO"),
        ("none", "NONE"),
        ("required", "ANY"),
        ({"type": "auto"}, "AUTO"),
        ({"type": "any"}, "ANY"),
        ({"type": "none"}, "NONE"),
        ({"type": "required"}, "ANY"),
    ])
    def test_modes(self, value, expected):
        assert fcc(value)["mode"] == expected

    @pytest.mark.parametrize("value,name", [
        ({"type": "function", "function": {"name": "get_weather"}}, "get_weather"),
        ({"type": "tool", "name": "get_weather"}, "get_weather"),
        ({"name": "get_weather"}, "get_weather"),
    ])
    def test_a_forced_tool_becomes_any_with_a_one_entry_allowlist(self, value, name):
        cfg = fcc(value)
        assert cfg["mode"] == "ANY"
        assert cfg["allowed_function_names"] == [name]

    def test_an_allowlist_keeps_every_name(self):
        cfg = fcc({"type": "allowed_tools", "allowed_tools": ["a", "b"]})
        assert cfg["mode"] == "ANY"
        assert cfg["allowed_function_names"] == ["a", "b"]

    def test_names_are_dropped_unless_the_mode_is_any(self):
        """Google documents allowed_function_names as ANY-only, so attaching it
        to AUTO is not a faithful translation."""
        cfg = fcc({"type": "allowed_tools", "mode": "auto", "allowed_tools": ["a"]})
        assert cfg["mode"] == "AUTO"
        assert "allowed_function_names" not in cfg

    @pytest.mark.parametrize("value", [None, "", "weird", 123, {"type": "mystery"}])
    def test_anything_unrecognised_produces_no_config(self, value):
        assert _gemini_tool_config(value) is None


class TestTheSdkActuallyAcceptsWhatWeBuild:
    """A dict is only useful if the SDK coerces it into the real config type;
    otherwise the failure surfaces on the first production request."""

    def test_it_becomes_a_function_calling_config(self):
        from google.genai import types

        out = _gemini_tool_config({"type": "function", "function": {"name": "f"}})
        cfg = types.GenerateContentConfig(tool_config=out)
        assert cfg.tool_config.function_calling_config.mode == (
            types.FunctionCallingConfigMode.ANY)
        assert cfg.tool_config.function_calling_config.allowed_function_names == ["f"]

    @pytest.mark.parametrize("mode", ["AUTO", "ANY", "NONE"])
    def test_every_mode_we_emit_is_a_real_enum_member(self, mode):
        from google.genai import types

        cfg = types.GenerateContentConfig(
            tool_config={"function_calling_config": {"mode": mode}})
        assert cfg.tool_config.function_calling_config.mode.value == mode


class TestARoundTripThroughEveryDialect:
    """Same request, three clients, one intended meaning each time."""

    SCENARIO = {"type": "function", "function": {"name": "Bash"}}

    def test_anthropic_forced_tool(self):
        canonical = extract_tool_choice({"tool_choice": {"type": "tool", "name": "Bash"}})
        assert fcc(canonical["tool_choice"])["allowed_function_names"] == ["Bash"]

    def test_openai_forced_tool(self):
        canonical = _client_sampling_params({"tool_choice": self.SCENARIO})
        assert fcc(canonical["tool_choice"])["allowed_function_names"] == ["Bash"]

    def test_both_clients_asking_for_a_tool_agree(self):
        a = extract_tool_choice({"tool_choice": {"type": "tool", "name": "Bash"}})
        o = _client_sampling_params({"tool_choice": self.SCENARIO})
        assert fcc(a["tool_choice"]) == fcc(o["tool_choice"])
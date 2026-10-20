"""Reasoning effort must never reach a custom endpoint as a placeholder.

OpenCode, Codex and Claude Code each spell the same intent differently, and two
of their values — ``auto`` and ``default`` — mean "you pick", not something a
provider accepts:

    Custom endpoint error (400): invalid_value
    "Invalid value: 'auto'. Supported values are: 'none', 'minimal', 'low',
     'medium', 'high', 'xhigh', and 'max'."

The placeholder is dropped rather than translated, so the upstream model applies
its own default. This suite pins every layer that used to forward it verbatim.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.runtime.effort import (
    extract_reasoning_effort,
    normalize_reasoning_effort,
    sanitize_custom_endpoint_payload,
)


class TestNormalize:
    @pytest.mark.parametrize("value", [None, "", "auto", "default", "AUTO", " Default "])
    def test_placeholders_are_omitted(self, value):
        assert normalize_reasoning_effort(value) is None

    @pytest.mark.parametrize("value,expected", [
        ("none", "none"),
        ("off", "none"),
        ("false", "none"),
        ("disabled", "none"),
        ("minimal", "minimal"),
        ("min", "minimal"),
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        ("xhigh", "max"),
        ("max", "max"),
        ("maximum", "max"),
        ("highest", "max"),
        ("HIGH", "high"),
    ])
    def test_valid_values_map_to_provider_vocabulary(self, value, expected):
        assert normalize_reasoning_effort(value) == expected

    @pytest.mark.parametrize("value", ["banana", 1, 0.5, {"effort": "high"}, ["high"], True])
    def test_unknown_or_non_string_is_omitted(self, value):
        assert normalize_reasoning_effort(value) is None


class TestExtract:
    def test_reasoning_effort_wins(self):
        body = {
            "reasoning_effort": "high",
            "reasoning": {"effort": "low"},
            "output_config": {"effort": "medium"},
        }
        assert extract_reasoning_effort(body) == "high"

    def test_reasoning_effort_auto_does_not_fall_through(self):
        """An explicit placeholder means "model default", not "try the next key"."""
        body = {"reasoning_effort": "auto", "output_config": {"effort": "high"}}
        assert extract_reasoning_effort(body) is None

    def test_reasoning_dict_effort(self):
        assert extract_reasoning_effort({"reasoning": {"effort": "max"}}) == "max"

    def test_output_config_effort(self):
        assert extract_reasoning_effort({"output_config": {"effort": "medium"}}) == "medium"

    @pytest.mark.parametrize("budget,expected", [
        (0, "none"),
        (1, "low"),
        (4095, "low"),
        (4096, "medium"),
        (8191, "medium"),
        (8192, "high"),
        (32000, "high"),
    ])
    def test_thinking_budget_becomes_effort(self, budget, expected):
        body = {"thinking": {"type": "enabled", "budget_tokens": budget}}
        assert extract_reasoning_effort(body) == expected

    def test_disabled_thinking_is_none_effort(self):
        assert extract_reasoning_effort({"thinking": {"type": "disabled"}}) == "none"

    def test_thinking_without_budget_or_type_is_omitted(self):
        assert extract_reasoning_effort({"thinking": {"type": "enabled"}}) is None
        assert extract_reasoning_effort({"thinking": None}) is None

    def test_non_dict_reasoning_keys_do_not_raise(self):
        assert extract_reasoning_effort({"reasoning": "auto", "output_config": []}) is None
        assert extract_reasoning_effort(None) is None


class TestSanitizeOpenAI:
    def test_auto_is_removed_not_forwarded(self):
        payload = {"reasoning_effort": "auto", "messages": []}
        sanitize_custom_endpoint_payload(payload, api_format="openai")
        assert "reasoning_effort" not in payload

    def test_default_is_removed(self):
        payload = {"reasoning_effort": "default"}
        sanitize_custom_endpoint_payload(payload, api_format="openai")
        assert "reasoning_effort" not in payload

    def test_real_effort_is_kept_and_normalized(self):
        payload = {"reasoning_effort": "MAXIMUM"}
        sanitize_custom_endpoint_payload(payload, api_format="openai")
        assert payload["reasoning_effort"] == "max"

    def test_anthropic_only_fields_are_dropped(self):
        payload = {
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "output_config": {"effort": "high"},
        }
        sanitize_custom_endpoint_payload(payload, api_format="openai")
        assert "thinking" not in payload
        assert "output_config" not in payload

    def test_reasoning_effort_auto_is_dropped_but_summary_survives(self):
        payload = {"reasoning": {"effort": "auto", "summary": "auto"}}
        sanitize_custom_endpoint_payload(payload, api_format="openai")
        assert payload["reasoning"] == {"summary": "auto"}

    def test_empty_reasoning_dict_is_popped(self):
        payload = {"reasoning": {"effort": "default"}}
        sanitize_custom_endpoint_payload(payload, api_format="openai")
        assert "reasoning" not in payload

    def test_other_fields_are_untouched(self):
        payload = {"model": "m", "messages": [], "temperature": 0.2}
        sanitize_custom_endpoint_payload(payload, api_format="openai")
        assert payload == {"model": "m", "messages": [], "temperature": 0.2}


class TestSanitizeAnthropic:
    def test_openai_spellings_are_removed(self):
        payload = {
            "reasoning_effort": "high",
            "reasoning": {"effort": "high"},
            "messages": [],
        }
        sanitize_custom_endpoint_payload(payload, api_format="anthropic")
        assert "reasoning_effort" not in payload
        assert "reasoning" not in payload

    def test_existing_output_config_is_preserved(self):
        payload = {"reasoning_effort": "high", "output_config": {"effort": "low"}}
        sanitize_custom_endpoint_payload(payload, api_format="anthropic")
        assert payload["output_config"] == {"effort": "low"}

    def test_effort_without_output_config_is_mapped(self):
        payload = {"reasoning_effort": "medium"}
        sanitize_custom_endpoint_payload(payload, api_format="anthropic")
        assert payload["output_config"] == {"effort": "medium"}

    @pytest.mark.parametrize("effort", ["auto", "default", "none", "minimal"])
    def test_placeholder_or_unsupported_effort_does_not_create_output_config(self, effort):
        payload = {"reasoning_effort": effort}
        sanitize_custom_endpoint_payload(payload, api_format="anthropic")
        assert "output_config" not in payload


class TestTheClientActuallyAppliesIt:
    """The helpers are correct; these pin the calls that used to skip them."""

    def test_build_payload_drops_auto_from_extra_body(self):
        from src.core.providers.custom_endpoint.client import _build_payload

        payload = _build_payload(
            "m", [], extra_body={
                "reasoning_effort": "auto",
                "reasoning": {"effort": "auto"},
                "thinking": {"type": "enabled", "budget_tokens": 1024},
                "output_config": {"effort": "high"},
            },
        )
        assert "reasoning_effort" not in payload
        assert "reasoning" not in payload
        assert "thinking" not in payload
        assert "output_config" not in payload

    def test_build_payload_keeps_a_real_effort(self):
        from src.core.providers.custom_endpoint.client import _build_payload

        payload = _build_payload("m", [], extra_body={"reasoning_effort": "high"})
        assert payload["reasoning_effort"] == "high"


class TestOpenCodeSamplingParams:
    def test_auto_is_never_assigned(self):
        from src.api.opencode_proxy.handler.proxy import _client_sampling_params

        assert "reasoning_effort" not in _client_sampling_params({"reasoning_effort": "auto"})
        assert "reasoning_effort" not in _client_sampling_params(
            {"reasoning": {"effort": "auto"}}
        )
        assert "reasoning_effort" not in _client_sampling_params(
            {"output_config": {"effort": "default"}}
        )

    def test_valid_effort_is_normalized(self):
        from src.api.opencode_proxy.handler.proxy import _client_sampling_params

        assert _client_sampling_params({"reasoning_effort": "maximum"})["reasoning_effort"] == "max"
        assert _client_sampling_params({"reasoning": {"effort": "medium"}})["reasoning_effort"] == "medium"
        assert _client_sampling_params({"output_config": {"effort": "low"}})["reasoning_effort"] == "low"


class TestResponsesMapping:
    def test_auto_is_not_copied(self):
        from src.api.opencode_proxy.handler.responses_mapping import responses_to_chat_body

        chat = responses_to_chat_body({"reasoning": {"effort": "auto"}, "input": "hi"})
        assert "reasoning_effort" not in chat

    def test_valid_effort_is_mapped(self):
        from src.api.opencode_proxy.handler.responses_mapping import responses_to_chat_body

        chat = responses_to_chat_body({"reasoning": {"effort": "high"}, "input": "hi"})
        assert chat["reasoning_effort"] == "high"


class TestClaudeCodeSources:
    def test_output_config_becomes_reasoning_effort(self):
        from src.api.claude_proxy.handler.anthropic_spec.request import extract_sampling_params

        out = extract_sampling_params({"output_config": {"effort": "max"}})
        assert out["reasoning_effort"] == "max"

    def test_thinking_budget_becomes_reasoning_effort(self):
        from src.api.claude_proxy.handler.anthropic_spec.request import extract_sampling_params

        out = extract_sampling_params({"thinking": {"type": "enabled", "budget_tokens": 8192}})
        assert out["reasoning_effort"] == "high"
        # The native block still travels verbatim for Anthropic destinations.
        assert out["thinking"] == {"type": "enabled", "budget_tokens": 8192}

    def test_auto_output_config_is_omitted(self):
        from src.api.claude_proxy.handler.anthropic_spec.request import extract_sampling_params

        out = extract_sampling_params({"output_config": {"effort": "auto"}})
        assert "reasoning_effort" not in out


class TestFacadeCustomEndpointPath:
    def _call(self, monkeypatch, **kwargs):
        import src.core.providers.custom_endpoint.client as client_mod
        from src.core.providers.gemini import facade

        captured = {}

        async def fake_call(**call_kwargs):
            captured.update(call_kwargs)
            return object()

        monkeypatch.setattr(client_mod, "call_custom_nonstream", fake_call)
        asyncio.run(facade.acompletion(
            model="m", messages=[], api_base="http://example.test",
            api_key="k", **kwargs,
        ))
        return captured

    def test_openai_target_never_receives_auto(self, monkeypatch):
        captured = self._call(
            monkeypatch,
            reasoning_effort="auto",
            extra_body={"reasoning": {"effort": "auto"}},
        )
        extra = captured.get("extra_body") or {}
        assert "reasoning_effort" not in extra
        assert "reasoning" not in extra

    def test_openai_target_drops_anthropic_fields(self, monkeypatch):
        captured = self._call(
            monkeypatch,
            extra_body={
                "thinking": {"type": "enabled", "budget_tokens": 1024},
                "output_config": {"effort": "high"},
            },
        )
        extra = captured.get("extra_body") or {}
        assert "thinking" not in extra
        assert "output_config" not in extra

    def test_anthropic_target_keeps_thinking_and_drops_reasoning(self, monkeypatch):
        thinking = {"type": "enabled", "budget_tokens": 1024}
        captured = self._call(
            monkeypatch,
            api_format="anthropic",
            reasoning_effort="high",
            thinking=thinking,
            extra_body={"reasoning": {"effort": "high"}},
        )
        extra = captured.get("extra_body") or {}
        assert extra["thinking"] == thinking
        assert "reasoning_effort" not in extra
        assert "reasoning" not in extra

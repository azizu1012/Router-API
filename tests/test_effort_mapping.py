"""Effort from the client must reach Gemini at the level that was asked for.

Three request shapes reach this router, and all three mean the same thing to a
user who types /effort or picks a reasoning level:

  Anthropic  output_config: {"effort": "low"|"medium"|"high"|"max"}
             thinking:       {"type": "enabled", "budget_tokens": N}
  OpenAI     reasoning_effort: "low"|"medium"|"high"|"max"

Gemini 3 takes thinking_level (minimal/low/medium/high); Gemini 2.5 takes a
token thinking_budget. Neither is optional in practice: an explicit low that
arrives as high costs the user real latency and tokens on every turn.

These tests pin the mapping rather than the implementation, so a future
refactor can change how it is computed as long as the levels stay honest.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.opencode_proxy.handler.proxy import (
    _extract_thinking_params,
    _resolve_thinking_config,
)

V3 = "gemini-3.5-flash"
V25 = "gemini-2.5-flash"


def level_of(body, model_id=V3):
    """The thinking level or budget Gemini will actually be given."""
    cfg = _resolve_thinking_config(body, model_id)
    return cfg.get("thinking_level", cfg.get("thinking_budget"))


# ── Anthropic output_config.effort ─────────────────────────────────────────

class TestAnthropicOutputConfig:
    @pytest.mark.parametrize("effort,expected", [
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        ("max", "high"),
    ])
    def test_effort_maps_to_matching_level(self, effort, expected):
        body = {"output_config": {"effort": effort}}
        assert level_of(body) == expected, (
            f"output_config.effort={effort} was not honoured"
        )

    def test_effort_survives_to_gemini_25(self):
        body = {"output_config": {"effort": "high"}}
        assert level_of(body, V25) == 4096, "2.5 needs a budget, not a level"

    @pytest.mark.parametrize("effort,expected", [
        ("HIGH", "high"),
        ("High", "high"),
        (" high ", "high"),
        ("MEDIUM", "medium"),
        ("LOW", "low"),
    ])
    def test_case_and_whitespace_tolerated(self, effort, expected):
        assert level_of({"output_config": {"effort": effort}}) == expected

    def test_thoughts_included_for_real_effort(self):
        cfg = _resolve_thinking_config({"output_config": {"effort": "high"}}, V3)
        assert cfg.get("include_thoughts") is True, (
            "asking for effort must not silently drop the thinking it produces"
        )

    def test_missing_effort_falls_back_not_crashes(self):
        assert "thinking_level" in _extract_thinking_params({"output_config": {}})


# ── Anthropic thinking.budget_tokens ───────────────────────────────────────

class TestAnthropicThinkingBudget:
    @pytest.mark.parametrize("budget,expected", [
        (31999, "high"),
        (16384, "high"),
        (8192, "high"),
        (4096, "medium"),
        (2048, "low"),
        (1024, "low"),
        (256, "low"),
    ])
    def test_buckets_map_to_levels(self, budget, expected):
        body = {"thinking": {"type": "enabled", "budget_tokens": budget}}
        assert level_of(body) == expected, (
            f"budget_tokens={budget} should map to {expected}"
        )

    def test_high_budget_is_reachable(self):
        """The old table topped out at medium, so 32k asked for high got medium."""
        body = {"thinking": {"type": "enabled", "budget_tokens": 31999}}
        assert level_of(body) == "high"

    def test_disabled_thinking_turns_it_off(self):
        cfg = _resolve_thinking_config(
            {"thinking": {"type": "disabled"}}, V3)
        assert cfg.get("include_thoughts") is False


# ── OpenAI reasoning_effort ────────────────────────────────────────────────

class TestOpenAIReasoningEffort:
    @pytest.mark.parametrize("effort,expected", [
        ("high", "high"),
        ("medium", "medium"),
        ("low", "low"),
        ("minimal", "minimal"),
    ])
    def test_effort_maps_to_matching_level(self, effort, expected):
        body = {"reasoning_effort": effort}
        assert level_of(body) == expected, (
            f"reasoning_effort={effort} was not honoured"
        )

    def test_medium_does_not_disable_thinking(self):
        """medium used to map to include_thoughts=False — backwards."""
        cfg = _resolve_thinking_config({"reasoning_effort": "medium"}, V3)
        assert cfg.get("thinking_level") == "medium"
        assert cfg.get("include_thoughts") is not False

    def test_high_is_not_downgraded_to_low(self):
        """high used to map to low."""
        cfg = _resolve_thinking_config({"reasoning_effort": "high"}, V3)
        assert cfg.get("thinking_level") == "high"

    def test_max_maps_to_high(self):
        assert level_of({"reasoning_effort": "max"}) == "high"


# ── precedence and default ─────────────────────────────────────────────────

class TestPrecedence:
    def test_explicit_thinking_level_wins_over_effort(self):
        body = {"thinking_level": "high", "reasoning_effort": "low"}
        assert level_of(body) == "high"

    def test_output_config_wins_over_reasoning_effort(self):
        """Anthropic clients may send both; output_config is the newer field."""
        body = {"output_config": {"effort": "high"}, "reasoning_effort": "low"}
        assert level_of(body) == "high"

    def test_default_is_low_not_off(self):
        cfg = _resolve_thinking_config({}, V3)
        assert cfg.get("thinking_level") == "low"

    def test_monotonic_in_effort(self):
        """More effort must never yield less thinking."""
        ladder = ["low", "medium", "high"]
        seen = [level_of({"output_config": {"effort": e}}) for e in ladder]
        assert seen == ["low", "medium", "high"], f"not monotonic: {seen}"

    def test_both_families_agree_on_the_same_word(self):
        for effort in ("low", "medium", "high"):
            anthropic = level_of({"output_config": {"effort": effort}})
            openai = level_of({"reasoning_effort": effort})
            assert anthropic == openai, (
                f"{effort}: Anthropic gave {anthropic}, OpenAI gave {openai}"
            )


# ── guards that must survive ───────────────────────────────────────────────

class TestExistingBehaviour:
    def test_lite_models_get_no_thinking(self):
        cfg = _resolve_thinking_config(
            {"output_config": {"effort": "high"}}, "gemini-3.5-flash-lite")
        assert cfg == {}, "lite has no thinking support"

    def test_sub_agent_gets_no_thinking(self):
        from src.core.providers.gemini_thinking import resolve_thinking_config
        cfg = resolve_thinking_config(V3, thinking_level="high",
                                      is_sub_agent=True)
        assert cfg == {}

    def test_gemini_25_uses_budget_never_level(self):
        for effort in ("low", "medium", "high"):
            cfg = _resolve_thinking_config({"output_config": {"effort": effort}}, V25)
            assert "thinking_level" not in cfg, f"{effort}: 2.5 got a level"
            assert isinstance(cfg.get("thinking_budget"), int)


# ── the router's own thinking_level field must obey the enum ────────────────

class TestExplicitThinkingLevelIsChecked:
    """`thinking_level` is the router's own field, so it is the one effort
    source a client can spell any way it likes.

    Gemini's ThinkingLevel is a closed enum. The GenAI SDK does not validate it
    — it emits a UserWarning and still constructs a ThinkingLevel.banana member
    — so an unchecked value is not caught locally, it becomes a `thinkingLevel`
    that Google rejects at request time. Every other source here already lands on
    "low" for a word it does not recognise.
    """

    def test_an_unknown_word_does_not_reach_gemini(self):
        assert level_of({"thinking_level": "banana"}) == "low"

    @pytest.mark.parametrize("word", ["minimal", "low", "medium", "high"])
    def test_every_real_level_survives_untouched(self, word):
        assert level_of({"thinking_level": word}) == word

    def test_uppercase_is_normalised(self):
        assert level_of({"thinking_level": "HIGH"}) == "high"

    @pytest.mark.parametrize("word", ["off", "none", "false"])
    def test_a_word_that_turns_thinking_off_still_does(self, word):
        """_effort_to_level folds these into "minimal", which re-enables
        thinking on a 2.5 model rather than switching it off."""
        cfg = _resolve_thinking_config({"thinking_level": word}, V25)
        assert cfg == {"include_thoughts": False}, word

    def test_an_empty_string_does_not_reach_gemini(self):
        assert level_of({"thinking_level": ""}) == "low"

    def test_the_result_is_a_real_thinking_level_member(self):
        """The end the check exists for: what GenAI actually constructs."""
        types = pytest.importorskip("google.genai.types")
        cfg = _resolve_thinking_config({"thinking_level": "banana"}, V3)

        level = types.ThinkingConfig(**cfg).thinking_level
        assert level in tuple(types.ThinkingLevel), level
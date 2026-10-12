"""An Anthropic client arriving at an Anthropic endpoint used to arrive smaller
than it left.

thinking and cache_control were read at the edge and then dropped on the way
out, so routing a Claude Code session to a paid Anthropic endpoint silently
downgraded it. Both are now carried.

thinking is forwarded exactly as written rather than synthesised, which is the
whole point: Anthropic is mid-migration between

    {"type": "enabled", "budget_tokens": N}   deprecated on 4.6, 400 on 4.7+
    {"type": "adaptive"}                       400 on 4.5 and earlier

and the model behind a third-party endpoint is not something the router can see.
Guessing would mean picking whichever form is wrong for half the fleet.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.claude_proxy.handler.anthropic_spec import extract_sampling_params
from src.core.providers.anthropic_wire import openai_to_anthropic_body
from src.logical_HQ_translator.messages.converter import _convert_messages


def roundtrip(body, **kw):
    messages, _ = _convert_messages(body)
    params = extract_sampling_params(body)
    kw.setdefault("extra_body", dict(params))
    return openai_to_anthropic_body("m", messages, max_tokens=4096, **kw)


class TestThinkingIsForwardedNotGuessed:
    @pytest.mark.parametrize("thinking", [
        {"type": "enabled", "budget_tokens": 2048},
        {"type": "adaptive"},
        {"type": "enabled", "budget_tokens": 1024},
    ])
    def test_whatever_the_client_sent_arrives_unchanged(self, thinking):
        out = roundtrip({"thinking": thinking,
                         "messages": [{"role": "user", "content": "hi"}]})
        assert out["thinking"] == thinking

    @pytest.mark.parametrize("body", [
        {},
        {"thinking": None},
        {"thinking": "enabled"},
        {"thinking": {"budget_tokens": 100}},   # no type, nothing to forward
    ])
    def test_nothing_is_invented_when_the_client_sent_nothing(self, body):
        body["messages"] = [{"role": "user", "content": "hi"}]
        assert "thinking" not in roundtrip(body)

    def test_it_travels_on_the_same_channel_as_top_k(self):
        out = extract_sampling_params({"thinking": {"type": "adaptive"},
                                       "top_k": 40, "stop_sequences": ["X"]})
        assert out["thinking"] == {"type": "adaptive"}
        assert out["top_k"] == 40


class TestCacheControlSurvivesOnTheSystemBlock:
    """cache_control lives on the block. Joining the system blocks into one
    string, which is what the converter did, discards it."""

    BODY = {
        "system": [{"type": "text", "text": "ban la tro ly lap trinh.",
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "hi"}],
    }

    def test_system_arrives_as_blocks_carrying_the_marker(self):
        out = roundtrip(dict(self.BODY))
        assert isinstance(out["system"], list)
        assert out["system"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_the_internal_marker_never_reaches_the_wire(self):
        out = roundtrip(dict(self.BODY))
        assert "_cache_control" not in out["system"][-1]

    def test_plain_system_stays_a_plain_string(self):
        """No marker means no reason to pay for the block form."""
        out = roundtrip({
            "system": "ban la tro ly lap trinh.",
            "messages": [{"role": "user", "content": "hi"}]})
        assert isinstance(out["system"], str)
        assert "tro ly" in out["system"]

    def test_system_as_a_list_without_markers_also_stays_a_string(self):
        out = roundtrip({
            "system": [{"type": "text", "text": "khong co marker."}],
            "messages": [{"role": "user", "content": "hi"}]})
        assert isinstance(out["system"], str)


class TestTheMarkerCountStaysInsideTheApiLimit:
    """Anthropic allows four cache breakpoints per request. A router that
    re-adds markers on top of the client's own can cross that."""

    def test_we_add_at_most_one(self):
        out = roundtrip({
            "system": [{"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}},
                       {"type": "text", "text": "b", "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": "hi"}]})
        markers = [b for b in out["system"] if b.get("cache_control")]
        assert len(markers) <= 4
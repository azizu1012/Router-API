"""A turn Gemini refused must say so, not end in silence.

`parse_gemini_chunk` returned `[]` whenever a chunk carried no `candidates`.
A blocked prompt has no candidate — only `promptFeedback.blockReason` — so the
reason was swallowed, the turn reached the client as an empty 200, and the
usage log recorded `output_tokens=1` for an 8k-token request.

An empty frame with no `promptFeedback` is a different thing (a keepalive), and
must still produce nothing: manufacturing a finish reason for it would end every
stream the moment a heartbeat arrived.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.providers.gemini.response import parse_gemini_chunk

BLOCKED = {
    "responseId": "resp_1",
    "modelVersion": "gemini-3.1-pro",
    "promptFeedback": {"blockReason": "PROHIBITED_CONTENT"},
    "usageMetadata": {"promptTokenCount": 8000, "candidatesTokenCount": 0,
                      "totalTokenCount": 8000},
}


class TestBlockedTurn:
    def test_block_reason_becomes_a_finish_reason(self):
        state = {}
        chunks = parse_gemini_chunk(BLOCKED, state)
        assert chunks, "a blocked turn produced no chunk at all — the old bug"
        finish = chunks[-1]["choices"][0]["finish_reason"]
        assert finish, "the client is still told the turn ended cleanly"

    def test_the_reason_is_recoverable_from_state(self):
        state = {}
        parse_gemini_chunk(BLOCKED, state)
        assert state.get("block_reason") == "PROHIBITED_CONTENT"

    def test_usage_survives_the_block(self):
        """Without usage here the turn still logs as out=1."""
        state = {}
        chunks = parse_gemini_chunk(BLOCKED, state)
        assert chunks[-1]["usage"]["prompt_tokens"] == 8000

    def test_no_candidate_and_no_feedback_stays_empty(self):
        """A keepalive frame is not a refusal — ending the stream on one
        would break every request that receives one mid-flight."""
        assert parse_gemini_chunk({"responseId": "r", "modelVersion": "g"}, {}) == []

    def test_an_empty_prompt_feedback_is_not_a_refusal(self):
        assert parse_gemini_chunk(
            {"promptFeedback": {"blockReason": ""}, "responseId": "r"}, {}) == []

    @pytest.mark.parametrize("reason", [
        "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION",
    ])
    def test_every_block_reason_is_caught(self, reason):
        state = {}
        chunks = parse_gemini_chunk({"promptFeedback": {"blockReason": reason}}, state)
        assert chunks and state["block_reason"] == reason


class TestNormalTurnsAreUnchanged:
    def test_text_still_parses(self):
        chunks = parse_gemini_chunk(
            {"candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}]},
            {},
        )
        contents = [c["choices"][0]["delta"].get("content") for c in chunks]
        assert "hi" in contents

    def test_finish_reason_still_closes_the_stream(self):
        chunks = parse_gemini_chunk(
            {"candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}]},
            {},
        )
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"




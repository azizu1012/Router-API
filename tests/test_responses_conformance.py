"""/v1/responses must speak the Responses dialect, validated by the OpenAI SDK.

The chat dialect and the Responses dialect name the same things differently and
open with different events, so re-framing one into the other is easy to get
subtly wrong. Every assertion here is checked against `openai.types.responses`,
which is the model a real client constructs — including `strict=True`, the
tighter reading that rejects any field the SDK does not declare.

The adapter used to produce a stream that the SDK rejected on 4 of 6 events:

  * `Response` requires `parallel_tool_calls`, `tool_choice` and `tools`; all
    three were absent, so `response.created`, `response.in_progress` and
    `response.completed` each failed validation.
  * `output_text.delta` and `output_text.done` require `logprobs`; it was absent.
  * `ResponseUsage` requires `input_tokens_details` and `output_tokens_details`.
  * The closing frames carried `text: ""`, `content: []` and `output: []`. Those
    shapes validate, which is why nothing complained — but `response.completed`
    `.output` is what a Responses client reads as the result, so an empty array
    there is a successful call that returns nothing.
  * Frames carried no `event:` line. The Python SDK reads the type out of the
    data payload so it coped, but the documented wire format has both, and
    clients that dispatch on the event name do not.
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_MODELS = None


def _models():
    """Import lazily so the suite still runs without the openai package."""
    global _MODELS
    if _MODELS is None:
        R = pytest.importorskip("openai.types.responses")
        _MODELS = {
            "response.created": R.ResponseCreatedEvent,
            "response.in_progress": R.ResponseInProgressEvent,
            "response.output_item.added": R.ResponseOutputItemAddedEvent,
            "response.content_part.added": R.ResponseContentPartAddedEvent,
            "response.output_text.delta": R.ResponseTextDeltaEvent,
            "response.output_text.done": R.ResponseTextDoneEvent,
            "response.content_part.done": R.ResponseContentPartDoneEvent,
            "response.output_item.done": R.ResponseOutputItemDoneEvent,
            "response.completed": R.ResponseCompletedEvent,
        }
    return _MODELS


async def _chat(*pieces, usage=None, finish="stop"):
    """Yield the bytes the chat proxy actually yields: SSE with a JSON payload."""
    for piece in pieces:
        yield ("data: " + json.dumps({
            "choices": [{"delta": {"content": piece}, "finish_reason": None}]}
        )).encode("utf-8")
    tail = {"choices": [{"delta": {}, "finish_reason": finish}]}
    if usage:
        tail["usage"] = usage
    yield ("data: " + json.dumps(tail)).encode("utf-8")
    yield b"data: [DONE]\n\n"


def _stream(*pieces, usage=None, finish="stop"):
    from src.server.openai_server.routes.completions_routes import _responses_sse_stream

    async def go():
        raw = []
        async for c in _responses_sse_stream(_chat(*pieces, usage=usage, finish=finish),
                                            model="gemini-flash"):
            raw.append(c.decode("utf-8") if isinstance(c, (bytes, bytearray))
                       else str(c))
        return raw

    return asyncio.run(go())


def _events(frames):
    out = []
    for f in frames:
        for line in f.split("\n"):
            if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                out.append(json.loads(line[6:]))
    return out


def _of_type(events, kind):
    return [e for e in events if e["type"] == kind]


# ── validation against the official models ───────────────────────────────────

class TestEveryEventValidates:
    def test_all_events_match_the_official_sdk_models(self):
        events = _events(_stream("Hello", " world"))

        assert events, "adapter emitted no events"
        for e in events:
            model = _models().get(e["type"])
            assert model is not None, f"unknown event type {e['type']!r}"
            model.model_validate(e)

    def test_all_events_pass_strict_validation(self):
        """strict=True rejects any field the SDK does not declare, so this is
        the check that catches fields we invented."""
        events = _events(_stream("Hello", " world"))

        for e in events:
            _models()[e["type"]].model_validate(e, strict=True)

    def test_the_sequence_numbers_are_contiguous_and_start_at_zero(self):
        events = _events(_stream("a", "b", "c"))

        assert [e["sequence_number"] for e in events] == list(range(len(events)))

    def test_every_frame_carries_an_event_line_naming_its_type(self):
        frames = _stream("Hello")

        for f in frames:
            if "[DONE]" in f:
                continue
            names = [ln[len("event: "):] for ln in f.split("\n")
                     if ln.startswith("event: ")]
            data = [json.loads(ln[6:]) for ln in f.split("\n")
                    if ln.startswith("data: ")]
            assert names == [data[0]["type"]], (
                f"event line {names} does not match payload type {data[0]['type']}"
            )


# ── the answer survives ──────────────────────────────────────────────────────

class TestTheAnswerIsNotLost:
    def test_output_text_done_carries_the_whole_reply(self):
        events = _events(_stream("Hello", " ", "world"))

        done = _of_type(events, "response.output_text.done")
        assert done[0]["text"] == "Hello world", done[0]["text"]

    def test_content_part_done_carries_the_whole_reply(self):
        events = _events(_stream("Hello", " ", "world"))

        part = _of_type(events, "response.content_part.done")[0]["part"]
        assert part["text"] == "Hello world"
        assert part["type"] == "output_text"
        assert part["annotations"] == []
        assert part["logprobs"] == []

    def test_output_item_done_carries_the_whole_reply(self):
        events = _events(_stream("Hello", " ", "world"))

        item = _of_type(events, "response.output_item.done")[0]["item"]
        assert item["status"] == "completed"
        assert item["content"][0]["text"] == "Hello world"

    def test_completed_output_carries_the_whole_reply(self):
        """This is the field a Responses client reads as the result."""
        events = _events(_stream("Hello", " ", "world"))

        output = _of_type(events, "response.completed")[0]["response"]["output"]
        assert len(output) == 1, output
        assert output[0]["type"] == "message"
        assert output[0]["content"][0]["text"] == "Hello world"

    def test_the_deltas_reassemble_into_the_done_text(self):
        events = _events(_stream("Hel", "lo ", "world"))

        joined = "".join(e["delta"] for e in _of_type(events, "response.output_text.delta"))
        done = _of_type(events, "response.output_text.done")[0]["text"]
        assert joined == done == "Hello world"


# ── required members ────────────────────────────────────────────────────────

class TestRequiredMembers:
    def test_the_response_envelope_declares_parallel_tool_calls_tool_choice_and_tools(self):
        for kind in ("response.created", "response.in_progress", "response.completed"):
            resp = _of_type(_events(_stream("x")), kind)[0]["response"]
            assert resp["parallel_tool_calls"] is True, kind
            assert resp["tool_choice"] == "auto", kind
            assert resp["tools"] == [], kind

    def test_every_delta_carries_a_logprobs_array(self):
        events = _events(_stream("a", "b"))

        for e in _of_type(events, "response.output_text.delta"):
            assert e["logprobs"] == []
        assert _of_type(events, "response.output_text.done")[0]["logprobs"] == []

    def test_the_content_part_is_opened_before_any_delta(self):
        events = _events(_stream("a"))

        kinds = [e["type"] for e in events]
        assert kinds.index("response.content_part.added") < \
            kinds.index("response.output_text.delta")

    def test_the_part_is_closed_before_the_item(self):
        events = _events(_stream("a"))

        kinds = [e["type"] for e in events]
        assert kinds.index("response.content_part.done") < \
            kinds.index("response.output_item.done")

    def test_the_stream_closes_with_completed_then_done(self):
        frames = _stream("a")

        assert "response.completed" in frames[-2]
        assert frames[-1].strip() == "data: [DONE]"


# ── usage dialect ───────────────────────────────────────────────────────────

class TestUsageDialect:
    def test_chat_token_counts_are_renamed_to_the_responses_spelling(self):
        events = _events(_stream("a", usage={"prompt_tokens": 11,
                                             "completion_tokens": 3,
                                             "total_tokens": 14}))

        usage = _of_type(events, "response.completed")[0]["response"]["usage"]
        assert usage["input_tokens"] == 11
        assert usage["output_tokens"] == 3
        assert usage["total_tokens"] == 14

    def test_both_details_objects_are_present_even_when_empty(self):
        events = _events(_stream("a", usage={"prompt_tokens": 1,
                                             "completion_tokens": 1}))

        usage = _of_type(events, "response.completed")[0]["response"]["usage"]
        assert usage["input_tokens_details"] == {"cached_tokens": 0}
        assert usage["output_tokens_details"] == {"reasoning_tokens": 0}

    def test_a_response_with_no_usage_still_validates(self):
        events = _events(_stream("a"))

        _models()["response.completed"].model_validate(
            _of_type(events, "response.completed")[0])


class TestNonStreamEnvelope:
    def test_the_non_stream_body_matches_the_official_response_model(self):
        pytest.importorskip("openai.types.responses")
        from src.server.openai_server.routes.completions_routes import (
            _output_message,
            _responses_envelope,
            _responses_usage,
        )
        import time

        body = _responses_envelope(
            "resp_1", "gemini-flash", "completed",
            [_output_message("msg_1", "completed", "hi")],
            int(time.time()),
            # Exactly what the route passes: chat usage, not a hand-built dict.
            _responses_usage({"prompt_tokens": 1, "completion_tokens": 2}),
        )
        body["output_text"] = "hi"

        _models()["response.completed"].model_validate(
            {"type": "response.completed", "sequence_number": 0, "response": body})

    def test_renaming_is_shared_by_both_paths(self):
        """The stream and non-stream routes must not drift apart on usage."""
        from src.server.openai_server.routes.completions_routes import _responses_usage

        got = _responses_usage({"prompt_tokens": 7, "completion_tokens": 5,
                                "total_tokens": 12})

        assert got["input_tokens"] == 7
        assert got["output_tokens"] == 5
        assert got["total_tokens"] == 12
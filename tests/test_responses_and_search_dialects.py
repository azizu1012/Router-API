"""Two bugs a client cannot distinguish from a working server.

Both produced a successful HTTP response with nothing usable in it, which is why
neither showed up as an error anywhere:

1. /v1/responses with stream:true forwarded chat-completions SSE verbatim.
   A Responses client parses typed events — response.created,
   response.output_text.delta, response.completed — and ignores a `choices`
   payload it does not recognise. The stream opened, produced deltas, and
   closed with no text printed. The model was never asked anything wrong.

2. Anthropic SDK web search sends a server tool identified by `type`
   (web_search_20250305), not by `name`. The converter read only `name`, so when
   the SDK omitted it the tool was dropped in silence: the request succeeded, the
   model answered from its weights, and nothing in the logs said the search tool
   had been removed.
"""

import asyncio
import json

import pytest

from src.logical_HQ_translator.message_converter import _convert_messages
from src.server.openai_server.routes.completions_routes import _responses_sse_stream


def _collect(stream):
    async def _run():
        return [c async for c in stream]

    return asyncio.run(_run())


def _events(chunks, model="gemini-flash"):
    raw = _collect(_responses_sse_stream(_chat_stream(chunks), model=model))
    parsed = []
    for blob in raw:
        text = blob.decode("utf-8")
        if text.strip() == "data: [DONE]":
            continue
        assert text.startswith("data: "), f"not an SSE data frame: {text[:80]!r}"
        parsed.append(json.loads(text[6:]))
    return parsed


async def _chat_stream(deltas):
    for d in deltas:
        chunk = {"choices": [{"delta": {"content": d}, "finish_reason": None}]}
        yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"


def _names(tools):
    return [t["function"]["name"] for t in tools]


# ── Responses SSE ───────────────────────────────────────────────────────────

class TestResponsesStreamEmitsTypedEvents:
    def test_the_text_arrives_as_output_text_delta(self):
        events = _events(["Hello", " ", "world"])

        deltas = [e["delta"] for e in events
                  if e["type"] == "response.output_text.delta"]
        assert "".join(deltas) == "Hello world", deltas

    def test_no_chat_completions_payload_leaks_through(self):
        """The shape that caused the bug: a client that only reads Responses
        events found nothing to print. If any event still carries `choices`,
        a client parsing strictly will skip it."""
        events = _events(["hi"])

        assert events
        for e in events:
            assert "choices" not in e, e

    def test_sequence_numbers_increase_from_one(self):
        events = _events(["a", "b", "c"])

        seqs = [e["sequence_number"] for e in events]
        assert seqs == sorted(seqs)
        assert seqs[0] == 1

    def test_the_lifecycle_opens_and_closes(self):
        events = _events(["x"])
        types = [e["type"] for e in events]

        assert types[0] == "response.created"
        assert types[-1] == "response.completed"
        assert "response.in_progress" in types
        assert "response.output_item.added" in types
        assert "response.output_item.done" in types

    def test_the_terminal_sentinel_is_sent(self):
        raw = _collect(_responses_sse_stream(_chat_stream(["hi"])))

        assert raw[-1] == b"data: [DONE]\n\n"

    def test_an_empty_answer_still_terminates_cleanly(self):
        # No deltas at all must not hang: the client needs the completed event to
        # stop waiting.
        events = _events([])

        assert events[-1]["type"] == "response.completed"

    def test_the_model_name_is_carried_through(self):
        events = _events(["hi"], model="gemini-flash-lite")

        assert events[0]["response"]["model"] == "gemini-flash-lite"

    def test_empty_delta_content_is_not_forwarded(self):
        """Role-only and usage-only chunks carry no text; emitting them as empty
        deltas makes clients append nothing and, on some, resets the buffer."""
        async def _chunks():
            for chunk in (
                {"choices": [{"delta": {"role": "assistant"}}]},
                {"choices": [{"delta": {"content": "kept"}}]},
            ):
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

        async def _run():
            return [c async for c in _responses_sse_stream(_chunks(), model="m")]

        events = [json.loads(b.decode()[6:]) for b in asyncio.run(_run())
                  if b.strip() != b"data: [DONE]"]
        deltas = [e["delta"] for e in events
                  if e["type"] == "response.output_text.delta"]

        assert deltas == ["kept"], deltas

    def test_malformed_frames_do_not_kill_the_stream(self):
        async def _chunks():
            yield b"data: {not json\n\n"
            yield b"\n\n"
            yield b"data: " + json.dumps(
                {"choices": [{"delta": {"content": "survived"}}]}
            ).encode() + b"\n\n"
            yield b"data: [DONE]\n\n"

        async def _run():
            return [c async for c in _responses_sse_stream(_chunks(), model="m")]

        events = [json.loads(b.decode()[6:]) for b in asyncio.run(_run())
                  if b.strip() != b"data: [DONE]"]
        deltas = [e["delta"] for e in events
                  if e["type"] == "response.output_text.delta"]

        assert deltas == ["survived"], deltas


# ── Anthropic SDK server tool ───────────────────────────────────────────────

class TestAnthropicServerSearchTool:
    def _convert(self, tool):
        _, tools = _convert_messages({
            "model": "gemini-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [tool],
        })
        return _names(tools)

    def test_the_dated_type_is_recognised(self):
        assert "web_search" in self._convert(
            {"type": "web_search_20250305", "name": "web_search"}
        )

    def test_a_tool_with_only_a_type_is_still_a_tool(self):
        """The regression. Anthropic's server tool carries no input_schema and,
        in the shapes seen in the wild, no name either. Reading only `name`
        dropped it — and the request then answered without searching."""
        assert "web_search" in self._convert({"type": "web_search_20250305"})

    def test_the_plain_type_is_recognised(self):
        assert "web_search" in self._convert({"type": "web_search"})

    def test_the_injected_schema_is_usable_by_gemini(self):
        """Gemini needs a query property. A tool with an empty schema is dropped
        downstream, so a recognised type is not enough on its own."""
        _, tools = _convert_messages({
            "model": "gemini-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "web_search_20250305"}],
        })

        web = next(t for t in tools if t["function"]["name"] == "web_search")
        params = web["function"]["parameters"]
        assert params["properties"]["query"]["type"] == "string"
        assert params.get("required") == ["query"]

    def test_a_regular_named_tool_still_works(self):
        assert "Read" in self._convert({
            "name": "Read",
            "description": "read a file",
            "input_schema": {"type": "object", "properties": {"p": {"type": "string"}}},
        })

    def test_a_tool_with_neither_name_nor_type_is_still_dropped(self):
        # Guard against the fix becoming "keep everything": an unidentifiable
        # entry is not a tool.
        assert self._convert({"description": "no identity"}) == []

    def test_stripped_tools_are_still_stripped(self):
        from src.logical_HQ_translator.message_converter import (
            UNSUPPORTED_OR_HEAVY_TOOLS,
        )
        name = sorted(UNSUPPORTED_OR_HEAVY_TOOLS)[0]
        assert self._convert({"name": name, "input_schema": {}}) == []


# ── precedence: an explicit engine still wins ───────────────────────────────

class TestEngineResolution:
    def test_an_explicit_engine_is_honoured(self):
        from src.api.opencode_proxy.handler.websearch import resolve_search_engine

        body = {"web_search": True, "_hosted_search": True,
                "search_engine": "duckduckgo"}
        assert resolve_search_engine(body, None) == "duckduckgo"

    def test_a_disabled_engine_stays_disabled(self):
        from src.api.opencode_proxy.handler.websearch import should_enable_web_search

        body = {"web_search": True, "search_engine": "disabled"}
        assert should_enable_web_search(body, None) is False

    def test_a_responses_hosted_tool_opt_into_grounding(self):
        from src.api.opencode_proxy.handler.responses_mapping import (
            responses_to_chat_body,
        )

        chat = responses_to_chat_body({
            "model": "m",
            "input": "hi",
            "tools": [{"type": "web_search"}],
        })

        assert chat["web_search"] is True
        assert chat["search_engine"] == "auto"

    def test_a_chat_request_stays_on_duckduckgo(self):
        """Only the Responses dialect opts up to grounding. Anything else keeps
        the free path so search does not spend a Gemini call."""
        from src.api.opencode_proxy.handler.websearch import resolve_search_engine

        assert resolve_search_engine({"web_search": True}, None) == "duckduckgo"


@pytest.mark.parametrize("version", [
    "web_search_20250305",
    "web_search_2025_08_26",
    "web_search_preview",
    "web_search",
])
def test_every_known_search_spelling_is_recognised(version):
    _, tools = _convert_messages({
        "model": "gemini-flash",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": version}],
    })
    assert "web_search" in _names(tools), version
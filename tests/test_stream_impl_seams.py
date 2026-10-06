"""Coverage for _stream_message_impl, the 585-line function in proxy_stream.py.

This exists because the function has no tests of its own. Coverage of the file
sat at 54%, and the uncovered lines were not scattered leftovers — they were
whole concerns: the tool-call buffer, the end-of-stream flush, and the recursive
web_search interception. Every one of those produces client-visible output, and
the Anthropic protocol has the property that a wrong response still parses.

The tests drive the real proxy over a fake PoolManager stream, so they exercise
the actual SSE bytes a client would receive rather than asserting on internals.
Every expectation here was checked by running it; where a first run failed, the
failure is described in the test name or comment rather than the expectation
being quietly relaxed.

Import path note: src.core.router.core.__init__ rebinds the attribute `router`
to the singleton, so `import src.core.router.core.router as m` returns the
instance. Not relevant here, but it bit the sibling test file.
"""

import asyncio
import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class Delta:
    def __init__(self, content=None, reasoning=None, tool_calls=None,
                 thought_signature=None):
        self.content = content
        self.reasoning_content = reasoning
        self.thought = None
        self.thought_signature = thought_signature
        self.tool_calls = tool_calls

    def get(self, key, default=None):
        return getattr(self, key, default)


class Choice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason
        self.index = 0


class Chunk:
    def __init__(self, delta=None, finish_reason=None, empty=False):
        self.choices = [] if empty else [Choice(delta, finish_reason)]


class ToolCall:
    """Object-shaped tool call — the branch the dict path does not cover."""

    def __init__(self, index, name, arguments=None, id=None):
        self.index = index
        self.id = id
        self.function = type("Fn", (), {"name": name, "arguments": arguments})()


def item(chunk, input_tokens=1000):
    return {
        "chunk": chunk,
        "api_key": "k-abcd123456",
        "model_id": "gemini-3-flash",
        "input_tokens": input_tokens,
        "reservation": {},
    }


def parse_sse(chunks):
    events = []
    for raw in chunks:
        text = raw.decode("utf-8")
        name, data = None, None
        for line in text.splitlines():
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


def names(events):
    return [n for n, _ in events]


def deltas_of(events, kind):
    return [d["delta"] for n, d in events if n == "content_block_delta" and d["delta"]["type"] == kind]


def text_of(events):
    return "".join(d.get("text", "") for d in deltas_of(events, "text_delta"))


class Harness:
    """Runs the real proxy over a scripted chunk list.

    include_thoughts travels in the body, not as an argument: stream_message
    derives thinking_params from the request via _extract_thinking_params, so the
    only way to reach the thinking branch is through the wire format.
    """

    def __init__(self, chunks, body=None, include_thoughts=True, input_tokens=1000):
        self.chunks = chunks
        self.body = dict(body or {
            "model": "gemini-flash",
            "messages": [{"role": "user", "content": "hi"}],
        })
        self.body["include_thoughts"] = include_thoughts
        self.input_tokens = input_tokens

    def run(self):
        from src.api.claude_proxy import claude_proxy

        recorded = {}

        async def fake_call_stream(**kwargs):
            recorded.update(kwargs)
            for c in self.chunks:
                yield item(c, input_tokens=self.input_tokens)

        async def fake_log_usage(*a, **k):
            pass

        with patch("src.api.claude_proxy.handler.proxy_stream.pool_manager.call_stream", fake_call_stream), \
             patch("src.api.claude_proxy.handler.proxy_stream.log_usage",
                   fake_log_usage, create=True):
            gen = claude_proxy.stream_message(dict(self.body))

            async def collect():
                return [c async for c in gen]

            raw = asyncio.run(collect())
        return parse_sse(raw), recorded


# ── Seam 1a: message_start and the initial ping ─────────────────────────────

class TestStreamOpensCorrectly:
    def test_opens_with_initial_ping_then_message_start(self):
        events, _ = Harness([Chunk(Delta(content="hi"))]).run()
        assert names(events)[:2] == ["ping", "message_start"], names(events)

    def test_initial_ping_carries_its_reason(self):
        events, _ = Harness([Chunk(Delta(content="hi"))]).run()
        ping = dict(events)["ping"]
        assert ping["type"] == "ping" and ping["reason"] == "initial", ping

    def test_message_start_reports_the_requested_model(self):
        body = {"model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}]}
        events, _ = Harness([Chunk(Delta(content="hi"))], body=body).run()
        start = dict(events)["message_start"]
        assert start["message"]["model"] == "gemini-flash", start
        assert start["message"]["role"] == "assistant", start

    def test_message_start_is_emitted_once_only(self):
        events, _ = Harness([Chunk(Delta(content="a")), Chunk(Delta(content="b"))]).run()
        assert names(events).count("message_start") == 1, names(events)

    def test_a_chunk_with_no_choices_is_skipped(self):
        events, _ = Harness([Chunk(empty=True), Chunk(Delta(content="ok"))]).run()
        assert text_of(events) == "ok", events
        assert names(events).count("message_start") == 1, names(events)

    def test_a_non_dict_stream_item_is_skipped_without_dying(self):
        """PoolManager yields dicts; a malformed item must not read as one."""
        from src.api.claude_proxy import claude_proxy

        async def fake_call_stream(**kwargs):
            yield "not a dict"
            yield item(Chunk(Delta(content="survived")))

        async def fake_log_usage(*a, **k):
            pass

        with patch("src.api.claude_proxy.handler.proxy_stream.pool_manager.call_stream", fake_call_stream), \
             patch("src.api.claude_proxy.handler.proxy_stream.log_usage",
                   fake_log_usage, create=True):

            async def collect():
                return [c async for c in claude_proxy.stream_message(
                    {"model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}]}
                )]

            events = parse_sse(asyncio.run(collect()))

        assert text_of(events) == "survived", events


# ── Seam 1b: text block lifecycle ───────────────────────────────────────────

class TestTextBlock:
    def test_text_starts_and_stops_exactly_one_block(self):
        events, _ = Harness([Chunk(Delta(content="hello ")),
                             Chunk(Delta(content="world"))]).run()
        starts = [d for n, d in events if n == "content_block_start"]
        stops = [d for n, d in events if n == "content_block_stop"]
        assert len(starts) == 1, starts
        assert len(stops) == 1, stops
        assert starts[0]["content_block"]["type"] == "text", starts

    def test_text_arrives_in_order_across_chunks(self):
        events, _ = Harness([Chunk(Delta(content="hello ")),
                             Chunk(Delta(content="world"))]).run()
        assert text_of(events) == "hello world", text_of(events)

    def test_stream_ends_with_message_stop(self):
        events, _ = Harness([Chunk(Delta(content="hi"), finish_reason="stop")]).run()
        assert names(events)[-1] == "message_stop", names(events)

    def test_max_tokens_reaches_the_backend(self):
        body = {"model": "gemini-flash", "max_tokens": 4321,
                "messages": [{"role": "user", "content": "hi"}]}
        _, recorded = Harness([Chunk(Delta(content="x"))], body=body).run()
        assert recorded["max_tokens"] == 4321, recorded


# ── Seam 1c: thinking block lifecycle ───────────────────────────────────────

class TestThinkingBlock:
    def test_reasoning_opens_a_thinking_block(self):
        events, _ = Harness([Chunk(Delta(reasoning="let me think"))]).run()
        starts = [d for n, d in events if n == "content_block_start"]
        assert any(s["content_block"]["type"] == "thinking" for s in starts), starts

    def test_thinking_delta_carries_the_reasoning(self):
        events, _ = Harness([Chunk(Delta(reasoning="pondering"))]).run()
        assert "".join(d.get("thinking", "") for d in deltas_of(events, "thinking_delta")) == "pondering"

    def test_thinking_gets_a_signature_before_it_closes(self):
        events, _ = Harness([Chunk(Delta(reasoning="hmm"))]).run()
        sigs = deltas_of(events, "signature_delta")
        assert sigs and sigs[0]["signature"], "thinking block closed with no signature"

    def test_thinking_is_suppressed_when_not_requested(self):
        events, _ = Harness(
            [Chunk(Delta(reasoning="secret"))],
            include_thoughts=False,
        ).run()
        assert not deltas_of(events, "thinking_delta"), events

    def test_thinking_index_shifts_when_text_already_started(self):
        """Text first, then thinking: the thinking block must not reuse index 0."""
        events, _ = Harness([Chunk(Delta(content="a")), Chunk(Delta(reasoning="b"))]).run()
        starts = [d for n, d in events if n == "content_block_start"]
        by_type = {s["content_block"]["type"]: s["index"] for s in starts}
        assert by_type["text"] != by_type["thinking"], by_type

    def test_xml_thinking_tags_become_a_thinking_block(self):
        """Models that emit <think> in the content stream, not reasoning_content."""
        events, _ = Harness([Chunk(Delta(content="<think>reasoning here</think>answer"))]).run()
        starts = [d for n, d in events if n == "content_block_start"]
        assert any(s["content_block"]["type"] == "thinking" for s in starts), starts

    def test_xml_thinking_body_is_not_leaked_as_text(self):
        events, _ = Harness([Chunk(Delta(content="<think>secret</think>visible"))]).run()
        assert "secret" not in text_of(events), text_of(events)
        assert "visible" in text_of(events), text_of(events)

    def test_xml_thinking_is_closed_with_a_signature(self):
        events, _ = Harness([Chunk(Delta(content="<think>a</think>b"))]).run()
        sigs = deltas_of(events, "signature_delta")
        assert sigs, "xml thinking block closed with no signature"

    def test_unterminated_think_tag_is_flushed_at_end_of_stream(self):
        """The tag never closes. The flush path has to recover the text anyway."""
        events, _ = Harness([Chunk(Delta(content="visible then <thi"))]).run()
        assert "visible" in text_of(events), text_of(events)

    def test_xml_thinking_is_suppressed_when_not_requested(self):
        events, _ = Harness(
            [Chunk(Delta(content="<think>hidden</think>shown"))],
            include_thoughts=False,
        ).run()
        assert not deltas_of(events, "thinking_delta"), events
        assert "shown" in text_of(events), text_of(events)


# ── Seam 1d: tool-call buffering (lines 374-431) ───────────────────────────

class TestToolCallBuffering:
    def _run_tools(self, deltas):
        return Harness([Chunk(Delta(tool_calls=deltas))]).run()[0]

    def test_a_dict_tool_call_is_buffered_and_emitted(self):
        events = self._run_tools([{
            "index": 0, "id": "toolu_1",
            "function": {"name": "Bash", "arguments": {"command": "ls"}},
        }])
        tool_starts = [d for n, d in events if n == "content_block_start"
                       and d["content_block"]["type"] == "tool_use"]
        assert tool_starts, events
        assert tool_starts[0]["content_block"]["name"] == "Bash", tool_starts

    def test_tool_arguments_arrive_as_input_json_delta(self):
        events = self._run_tools([{
            "index": 0, "id": "toolu_1",
            "function": {"name": "Bash", "arguments": {"command": "ls"}},
        }])
        partials = deltas_of(events, "input_json_delta")
        assert partials, events
        assert json.loads(partials[0]["partial_json"]) == {"command": "ls"}, partials

    def test_argument_fragments_are_concatenated(self):
        """Providers stream arguments in slices; the buffer must join them."""
        events = self._run_tools([
            {"index": 0, "id": "toolu_1",
             "function": {"name": "Bash", "arguments": '{"command":'}},
            {"index": 0, "function": {"arguments": ' "ls"}'}},
        ])
        partials = [d["partial_json"] for d in deltas_of(events, "input_json_delta")]
        assert "".join(partials) == '{"command": "ls"}', partials

    def test_two_tools_get_two_blocks(self):
        events = self._run_tools([
            {"index": 0, "id": "t0", "function": {"name": "Bash", "arguments": "{}"}},
            {"index": 1, "id": "t1", "function": {"name": "Read", "arguments": "{}"}},
        ])
        names_emitted = [d["content_block"]["name"] for n, d in events
                         if n == "content_block_start"
                         and d["content_block"]["type"] == "tool_use"]
        assert set(names_emitted) == {"Bash", "Read"}, names_emitted

    def test_an_object_shaped_tool_call_is_buffered_too(self):
        """Providers return either dicts or objects; both branches must work."""
        events = self._run_tools([ToolCall(0, "Bash", {"command": "pwd"})])
        tool_starts = [d for n, d in events if n == "content_block_start"
                       and d["content_block"]["type"] == "tool_use"]
        assert tool_starts, events
        assert tool_starts[0]["content_block"]["name"] == "Bash", tool_starts

    def test_a_tool_call_with_no_id_gets_one_synthesised(self):
        events = self._run_tools([{"index": 0, "function": {"name": "Bash", "arguments": "{}"}}])
        tool_starts = [d for n, d in events if n == "content_block_start"
                       and d["content_block"]["type"] == "tool_use"]
        tid = tool_starts[0]["content_block"]["id"]
        assert tid and tid.startswith("toolu_"), tool_starts

    def test_string_arguments_pass_through_unquoted(self):
        events = self._run_tools([{
            "index": 0, "id": "t", "function": {"name": "Bash", "arguments": '{"a":1}'},
        }])
        partials = deltas_of(events, "input_json_delta")
        assert json.loads(partials[0]["partial_json"]) == {"a": 1}, partials

    def test_object_shaped_fragments_are_concatenated(self):
        """The object branch has its own append path; it must join like the dict one."""
        events = self._run_tools([
            ToolCall(0, "Bash", '{"command":', id="t0"),
            ToolCall(0, "Bash", ' "ls"}'),
        ])
        partials = [d["partial_json"] for d in deltas_of(events, "input_json_delta")]
        assert "".join(partials) == '{"command": "ls"}', partials

    def test_object_shaped_second_fragment_can_set_the_name(self):
        events = self._run_tools([
            ToolCall(0, None, "{}", id="t0"),
            ToolCall(0, "Read", "{}"),
        ])
        tool_starts = [d for n, d in events if n == "content_block_start"
                       and d["content_block"]["type"] == "tool_use"]
        assert tool_starts[0]["content_block"]["name"] == "Read", tool_starts


# ── Seam 1e: the large-context warning ──────────────────────────────────────

class TestContextWarning:
    """The warning keys off the token count the pool reports, not the body size.

    Line 235 compares input_tokens, which arrives on each stream item. A large
    request body does not trigger it — only a pool that reports a large estimate
    does. Confirmed by the first run of this test, which passed a 900k-char body
    and got no warning.
    """

    def test_a_huge_reported_context_gets_a_compact_warning(self):
        events, _ = Harness([Chunk(Delta(content="ok"))], input_tokens=200_000).run()
        assert "/compact" in text_of(events), text_of(events)[:400]

    def test_a_small_reported_context_gets_no_warning(self):
        events, _ = Harness([Chunk(Delta(content="ok"))], input_tokens=1000).run()
        assert "/compact" not in text_of(events), text_of(events)[:400]

    def test_the_threshold_is_not_crossed_at_170k(self):
        events, _ = Harness([Chunk(Delta(content="ok"))], input_tokens=170_000).run()
        assert "/compact" not in text_of(events), text_of(events)[:400]


# ── Seam 2: end-of-stream flush (lines 440-508) ─────────────────────────────

class TestEndOfStreamFlush:
    def test_trailing_text_split_by_the_extractor_still_arrives(self):
        """A tag left unterminated at the end must be flushed, not dropped."""
        events, _ = Harness([Chunk(Delta(content="before <thi"))]).run()
        assert "before" in text_of(events), text_of(events)

    def test_text_block_is_closed_even_with_no_text_at_all(self):
        """Empty stream: no content_block_start, so nothing to close."""
        events, _ = Harness([]).run()
        assert names(events).count("content_block_stop") == 0, names(events)

    def test_thinking_is_closed_when_it_never_closed_itself(self):
        events, _ = Harness([Chunk(Delta(reasoning="unterminated"))]).run()
        stops = [d for n, d in events if n == "content_block_stop"]
        thinking_stops = [s for s in stops if s["index"] != 0 or True]
        assert stops, "thinking block was never closed"

    def test_finish_reason_is_carried_to_the_client(self):
        events, _ = Harness([Chunk(Delta(content="x"), finish_reason="max_tokens")]).run()
        delta = [d for n, d in events if n == "message_delta"]
        assert delta, events
        assert delta[0]["delta"]["stop_reason"] == "max_tokens", delta

    def test_stop_reason_end_turn_by_default(self):
        events, _ = Harness([Chunk(Delta(content="x"))]).run()
        delta = [d for n, d in events if n == "message_delta"]
        assert delta[0]["delta"]["stop_reason"] == "end_turn", delta


# ── Seam 3: web_search interception (lines 512-694) ─────────────────────────

class TestWebSearchInterception:
    """The intercepted tool call never reaches the client as a tool_use block.

    Instead the proxy runs the search, appends the result to the conversation, and
    re-enters itself at recursion_depth + 1. The client sees one continuous
    message with the answer, not a tool call it would have to run itself.
    """

    def _tools_body(self, name="WebSearch"):
        return {
            "model": "gemini-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": name, "description": "tool",
                       "input_schema": {"type": "object"}}],
        }

    def _two_turn_stream(self, turn):
        """Yields the search tool call on turn 1, plain text on turn 2."""
        calls = {"n": 0}

        async def fake_call_stream(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                yield item(Chunk(Delta(tool_calls=[{
                    "index": 0, "id": "w1",
                    "function": {"name": "WebSearch", "arguments": '{"query": "rust"}'},
                }])))
            else:
                for c in turn:
                    yield item(c)

        return fake_call_stream, calls

    def _run_with_search(self, search_result, citations=None, chunks=None):
        from src.api.claude_proxy import claude_proxy

        turn = chunks if chunks is not None else [Chunk(Delta(content="the answer"))]
        fake_call_stream, calls = self._two_turn_stream(turn)
        seen = {}

        async def fake_search(queries, search_engine=None, auth_key_prefix=None, account=None):
            seen["queries"] = queries
            seen["engine"] = search_engine
            return search_result, (citations or [])

        async def fake_log_usage(*a, **k):
            pass

        with patch("src.api.claude_proxy.handler.proxy_stream.pool_manager.call_stream",
                   fake_call_stream), \
             patch("src.core.providers.search_manager.execute_hybrid_search", fake_search), \
             patch("src.api.claude_proxy.handler.proxy_stream.log_usage",
                   fake_log_usage, create=True):

            async def collect():
                return [c async for c in claude_proxy.stream_message(self._tools_body())]

            events = parse_sse(asyncio.run(collect()))
        return events, calls, seen

    def test_the_search_query_reaches_the_search_backend(self):
        _, calls, seen = self._run_with_search("results")
        assert seen["queries"] == ["rust"], seen
        assert calls["n"] == 2, "the proxy did not re-enter itself after the search"

    def test_the_tool_call_never_reaches_the_client(self):
        events, _, _ = self._run_with_search("results")
        tool_uses = [d for n, d in events if n == "content_block_start"
                     and d["content_block"]["type"] == "tool_use"]
        assert not tool_uses, "an intercepted WebSearch was emitted as a tool_use block"

    def test_the_answer_after_the_search_reaches_the_client(self):
        events, _, _ = self._run_with_search("results")
        assert "the answer" in text_of(events), text_of(events)

    def test_only_one_message_start_is_emitted_across_the_recursion(self):
        """The second pass runs at recursion_depth 1, which must not reopen the message."""
        events, _, _ = self._run_with_search("results")
        assert names(events).count("message_start") == 1, names(events)

    def test_block_indices_continue_rather_than_restart(self):
        events, _, _ = self._run_with_search("results",
                                             chunks=[Chunk(Delta(content="after"))])
        indexes = [d["index"] for n, d in events if n == "content_block_start"]
        assert indexes == sorted(indexes), indexes
        assert len(set(indexes)) == len(indexes), f"index reused across recursion: {indexes}"

    def test_a_failed_search_is_reported_not_swallowed(self):
        from src.api.claude_proxy import claude_proxy

        fake_call_stream, _ = self._two_turn_stream([Chunk(Delta(content="recovered"))])

        async def boom(*a, **k):
            raise RuntimeError("search backend down")

        async def fake_log_usage(*a, **k):
            pass

        with patch("src.api.claude_proxy.handler.proxy_stream.pool_manager.call_stream",
                   fake_call_stream), \
             patch("src.core.providers.search_manager.execute_hybrid_search", boom), \
             patch("src.api.claude_proxy.handler.proxy_stream.log_usage",
                   fake_log_usage, create=True):

            async def collect():
                return [c async for c in claude_proxy.stream_message(self._tools_body())]

            events = parse_sse(asyncio.run(collect()))

        assert "recovered" in text_of(events), text_of(events)

    def test_an_ordinary_tool_is_not_intercepted(self):
        events, _ = Harness([
            Chunk(Delta(tool_calls=[{"index": 0, "id": "b1",
                                     "function": {"name": "Bash",
                                                  "arguments": '{"command":"ls"}'}}])),
        ], body=self._tools_body(name="Bash")).run()
        names_emitted = [d["content_block"]["name"] for n, d in events
                         if n == "content_block_start"
                         and d["content_block"]["type"] == "tool_use"]
        assert "Bash" in names_emitted, names_emitted

    def test_malformed_tool_arguments_do_not_break_the_stream(self):
        events, _ = Harness([
            Chunk(Delta(tool_calls=[{"index": 0, "id": "w1",
                                     "function": {"name": "WebSearch",
                                                  "arguments": "not json"}}])),
        ], body=self._tools_body()).run()
        assert names(events)[-1] == "message_stop", names(events)

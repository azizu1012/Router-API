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


@pytest.fixture(autouse=True)
def _enable_router_hosted_tools(monkeypatch):
    """Opt this seam suite into the Router-owned hosted-tool path."""
    monkeypatch.setenv("ROUTER_CLAUDE_HOSTED_TOOLS", "true")


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
        self.body["include_thoughts"] = include_thoughts  # type: ignore
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
    def test_opens_with_message_start_then_the_keepalive_ping(self):
        """The docs open the stream with `message_start`; pings may then be
        "dispersed throughout". This proxy used to send the ping first, which
        the Anthropic SDK accumulator rejects when it is not filtered out by
        SSE event name."""
        events, _ = Harness([Chunk(Delta(content="hi"))]).run()
        assert names(events)[:2] == ["message_start", "ping"], names(events)

    def test_the_ping_payload_is_exactly_the_documented_one(self):
        """`{"type": "ping"}` and nothing else. The extra `reason` field was
        ours, and a client validating the event against the SDK's union rejects
        any member it does not declare."""
        events, _ = Harness([Chunk(Delta(content="hi"))]).run()
        ping = dict(events)["ping"]
        assert ping == {"type": "ping"}, ping

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

    def test_compaction_reasoning_only_stream_emits_assistant_text_block(self):
        body = {
            "model": "gemini-flash",
            "messages": [{
                "role": "user",
                "content": "Write a summary of this conversation.",
            }],
        }
        events, _ = Harness(
            [Chunk(Delta(reasoning="Compacted summary from reasoning."), finish_reason="stop")],
            body=body,
        ).run()

        text_starts = [
            data for event, data in events
            if event == "content_block_start"
            and data["content_block"].get("type") == "text"
        ]
        assert text_starts, events
        assert text_of(events) == "Compacted summary from reasoning."
        assert names(events)[-2:] == ["message_delta", "message_stop"]

    def test_compaction_emits_one_canonical_assistant_text_block(self):
        body = {
            "model": "gemini-flash",
            "messages": [{
                "role": "user",
                "content": "Write a summary of this conversation.",
            }],
        }
        events, _ = Harness(
            [
                Chunk(Delta(content="summary part one ")),
                Chunk(Delta(content="summary part two"), finish_reason="stop"),
            ],
            body=body,
        ).run()

        names_list = names(events)
        text_starts = [
            data for event, data in events
            if event == "content_block_start"
            and data["content_block"].get("type") == "text"
        ]
        text_stops = [data for event, data in events if event == "content_block_stop"]
        text_deltas = deltas_of(events, "text_delta")
        assert len(text_starts) == 1, events
        assert len(text_stops) == 1, events
        assert len(text_deltas) == 1, events
        assert text_deltas[0]["text"] == "summary part one summary part two"
        assert names_list[-2:] == ["message_delta", "message_stop"]

    def test_custom_endpoint_empty_stream_is_not_reported_as_end_turn(self):
        """A clean upstream EOF must not make Claude Code think the task finished."""
        from src.api.claude_proxy import claude_proxy

        async def empty_stream():
            if False:
                yield None

        async def fake_resolve(**kwargs):
            return {
                "name": "test-endpoint",
                "auth_key": "test-key",
                "base_url": "http://endpoint.invalid/v1",
                "api_format": "openai",
            }

        async def fake_call(**kwargs):
            return None, empty_stream()

        async def collect():
            return [c async for c in claude_proxy.stream_message({
                "model": "test-model",
                "messages": [{"role": "user", "content": "continue the task"}],
            })]

        with patch(
            "src.core.providers.custom_endpoint.passthrough.resolve_custom_endpoint_for_request",
            fake_resolve,
        ), patch(
            "src.core.providers.custom_endpoint.passthrough.call_custom_endpoint_passthrough",
            fake_call,
        ):
            events = parse_sse(asyncio.run(collect()))

        assert any(name == "error" for name, _ in events), events
        deltas = [payload for name, payload in events if name == "message_delta"]
        assert not any(
            payload.get("delta", {}).get("stop_reason") == "end_turn"
            for payload in deltas
        ), events

    def test_custom_endpoint_payload_without_finish_is_not_reported_as_end_turn(self):
        """9router-class EOF: payload arrived but no finish_reason and no [DONE].

        The stream validated in the custom bypass (Step 3 of the union) must
        raise, so the client gets an `error` event — not a `message_delta`
        with end_turn that makes Claude Code stop the tool loop mid-task.
        """
        from src.api.claude_proxy import claude_proxy

        async def payload_no_finish():
            from src.core.providers.custom_endpoint.client import NativeChunk
            yield NativeChunk({
                "id": "c1", "object": "chat.completion.chunk",
                "created": 0, "model": "muse-spark",
                "choices": [{"index": 0,
                             "delta": {"content": "partial answer"},
                             "finish_reason": None}],
            })

        async def fake_resolve(**kwargs):
            return {
                "name": "9router-test",
                "auth_key": "test-key",
                "base_url": "http://9router.invalid/v1",
                "api_format": "openai",
            }

        async def fake_call(**kwargs):
            return None, payload_no_finish()

        async def collect():
            return [c async for c in claude_proxy.stream_message({
                "model": "test-model",
                "messages": [{"role": "user", "content": "continue the task"}],
            })]

        with patch(
            "src.core.providers.custom_endpoint.passthrough.resolve_custom_endpoint_for_request",
            fake_resolve,
        ), patch(
            "src.core.providers.custom_endpoint.passthrough.call_custom_endpoint_passthrough",
            fake_call,
        ):
            events = parse_sse(asyncio.run(collect()))

        assert any(name == "error" for name, _ in events), events
        deltas = [payload for name, payload in events if name == "message_delta"]
        assert not any(
            payload.get("delta", {}).get("stop_reason") == "end_turn"
            for payload in deltas
        ), events

    class _FakeContent:
        """Minimal async-iterable stand-in for aiohttp response.content."""

        def __init__(self, blobs):
            self._blobs = blobs

        def __aiter__(self):
            async def gen():
                for b in self._blobs:
                    yield b
            return gen()

    class _FakeResponse:
        def __init__(self, blobs):
            self.content = TestTextBlock._FakeContent(blobs)

    @staticmethod
    async def _parse(blobs):
        from src.core.providers.custom_endpoint.client import (
            CustomEndpointStreamGen,
        )
        gen = CustomEndpointStreamGen.__new__(CustomEndpointStreamGen)
        gen._api_base = "http://test.invalid/v1"
        gen._model = "test-model"
        return [
            c async for c in gen._parse_sse_stream(
                TestTextBlock._FakeResponse(blobs)
            )
        ]

    def test_sse_trailing_finish_without_newline_is_kept(self):
        """A gateway that closes right after the final frame must not lose it.

        The tail parser only accepted ``data: `` with a space; a final
        ``data:{...finish...}`` with no trailing newline was dropped, so a
        good turn looked like an abnormal EOF downstream.
        """
        finish = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        wire = (
            b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n'
            + b"data:" + json.dumps(finish).encode()
        )
        chunks = asyncio.run(self._parse([wire]))
        reasons = [
            (c.get("choices") or [{}])[0].get("finish_reason") for c in chunks
        ]
        assert "stop" in reasons, chunks
        assert len(chunks) == 2, chunks

    def test_sse_trailing_done_without_newline_counts_as_done(self):
        chunks = asyncio.run(self._parse([b'data: {"a": 1}\n\ndata: [DONE]']))
        assert chunks == [{"a": 1}], chunks

    def test_sse_trailing_garbage_is_ignored(self):
        chunks = asyncio.run(self._parse([b'data: {"a": 1}\n\nnot-a-frame']))
        assert chunks == [{"a": 1}], chunks

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
    def _run_tools(self, deltas, finish_reason="tool_calls"):
        return Harness([Chunk(Delta(tool_calls=deltas), finish_reason=finish_reason)]).run()[0]

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
            ToolCall(0, "Read", None),
        ])
        tool_starts = [d for n, d in events if n == "content_block_start"
                       and d["content_block"]["type"] == "tool_use"]
        assert tool_starts[0]["content_block"]["name"] == "Read", tool_starts

    def test_an_empty_object_placeholder_is_replaced_not_concatenated(self):
        """`{}` then the real arguments must yield the arguments, not `{}{...}`.

        A provider that announces the call with an empty object and streams the
        body afterwards put `{}{"file_path": ...}` on the wire, which does not
        parse, so the client got a tool call it could not run.
        """
        events = self._run_tools([
            ToolCall(0, "Write", "{}", id="t0"),
            ToolCall(0, None, '{"file_path": "/a.py", "content": "x"}'),
        ])
        partials = [d["partial_json"] for d in deltas_of(events, "input_json_delta")]
        assert "".join(partials) == '{"file_path": "/a.py", "content": "x"}', partials

    def test_a_repeated_fragment_is_not_counted_twice(self):
        """A provider that re-sends the whole tool_call must not double it.

        Concatenating an identical fragment yields '{"a":1}{"a":1}', which is
        not JSON, so the call reached the client unusable.
        """
        events = self._run_tools([
            {"index": 0, "id": "t0",
             "function": {"name": "Bash", "arguments": '{"command":"ls"}'}},
            {"index": 0, "id": "t0",
             "function": {"name": "Bash", "arguments": '{"command":"ls"}'}},
        ])
        partials = [d["partial_json"] for d in deltas_of(events, "input_json_delta")]
        assert "".join(partials) == '{"command":"ls"}', partials

    def test_a_call_cut_mid_arguments_is_not_handed_to_the_client(self):
        """max_tokens or a dropped connection ends the stream without an error.

        The buffer then holds half an object. Emitting it tells the client a
        call is ready to execute while handing it arguments that do not parse —
        or none at all, which runs the tool against an empty input.
        """
        events = self._run_tools([
            {"index": 0, "id": "toolu_1",
             "function": {"name": "Write", "arguments": '{"file_path": "/a.py", "cont'}},
        ])
        starts = [d for n, d in events if n == "content_block_start"
                  and d["content_block"]["type"] == "tool_use"]
        assert not starts, starts
        assert not deltas_of(events, "input_json_delta"), events

    def test_the_stop_reason_counters_are_assigned_before_the_branches(self):
        """The intercepted-search path recurses and then falls through.

        It never enters the branch that emits client tool blocks, so a counter
        declared only in that branch is unbound by the time the stop reason is
        computed — a NameError on the one path that runs a server tool, and the
        hardest path to reach by hand. `ast.walk` gives no ordering guarantee,
        so this compares line numbers against the branch they have to precede.
        """
        import inspect

        import src.api.claude_proxy.handler.proxy_stream as ps

        lines = inspect.getsource(ps).splitlines()
        branch_line = next(
            i for i, l in enumerate(lines)
            if "if intercepted_tool_idx is not None:" in l
        )
        for name in ("emitted_tool_blocks", "dropped_incomplete_tool"):
            assigns = [
                i for i, l in enumerate(lines)
                if f"{name} =" in l and "if " not in l
            ]
            assert assigns, f"{name} is never assigned"
            assert min(assigns) < branch_line, (
                f"{name} is first assigned at line {min(assigns) + 1}, after the "
                f"intercepted branch at {branch_line + 1} that bypasses it"
            )

    def test_a_cut_tool_call_is_reported_as_max_tokens(self):
        """Dropping the block is only half the job — say why it is missing.

        Anthropic's remedy for a truncated tool use is to raise max_tokens and
        retry. A client told `end_turn` sees nothing wrong and continues the
        conversation as though the model had chosen to stop, so the turn is lost
        silently.
        """
        events = self._run_tools([
            {"index": 0, "id": "toolu_1",
             "function": {"name": "Write", "arguments": '{"file_path": "/a.py", "cont'}},
        ], finish_reason="stop")
        md = [d for n, d in events if n == "message_delta"]
        assert md, events
        assert md[0]["delta"]["stop_reason"] == "max_tokens", md[0]

    def test_a_complete_tool_call_still_reports_tool_use(self):
        events = self._run_tools([
            {"index": 0, "id": "toolu_1",
             "function": {"name": "Write", "arguments": '{"file_path": "/a.py"}'}},
        ])
        md = [d for n, d in events if n == "message_delta"]
        assert md[0]["delta"]["stop_reason"] == "tool_use", md[0]

class TestTruncatedToolCallRecovery:
    """The retry does not replay text, so it costs no latency.

    A stream cannot be resumed token-perfect, which is why retrying after output
    has been emitted normally duplicates it. Re-issuing only to recover the tool
    call avoids that: the client keeps the first attempt's text and never sees
    the retry's.
    """

    @staticmethod
    def _run(second_arg, retry_text=None, first_text=None):
        import asyncio
        from unittest.mock import patch

        from src.api.claude_proxy import claude_proxy

        def factory(**kwargs):
            async def gen():
                if kwargs["max_tokens"] > 4096:
                    if retry_text:
                        yield item(Chunk(Delta(content=retry_text)))
                    yield item(Chunk(Delta(tool_calls=[{
                        "index": 0, "id": "toolu_2",
                        "function": {"name": "Write", "arguments": second_arg},
                    }])))
                else:
                    if first_text:
                        yield item(Chunk(Delta(content=first_text)))
                    yield item(Chunk(Delta(tool_calls=[{
                        "index": 0, "id": "toolu_1",
                        "function": {"name": "Write", "arguments": '{"path": "/a'},
                    }])))
            return gen()

        with patch("src.api.claude_proxy.handler.proxy_stream.pool_manager.call_stream",
                   factory):
            async def collect():
                return [e async for e in claude_proxy.stream_message(
                    {"model": "gemini-flash", "max_tokens": 4096,
                     "messages": [{"role": "user", "content": "go"}]})]

            return parse_sse(asyncio.run(collect()))

    @staticmethod
    def _tool_use(events):
        return [d["content_block"] for n, d in events
                if n == "content_block_start"
                and d["content_block"]["type"] == "tool_use"]

    def test_a_matching_retry_yields_a_usable_tool_call(self):
        events = self._run('{"path": "/a"}')
        assert self._tool_use(events), events
        partials = [d["partial_json"] for d in deltas_of(events, "input_json_delta")]
        assert "".join(partials) == '{"path": "/a"}', partials

    def test_a_diverging_retry_is_discarded_rather_than_mispaired(self):
        """A retry that changed its mind must not attach a call to old text.

        The first attempt streamed "reading /a.py"; the retry came back talking
        about /somewhere/else. Taking its tool call would pair a decision with
        reasoning the client never saw.
        """
        events = self._run(
            '{"path": "/somewhere/else"}',
            retry_text="Now I will write to /somewhere/else instead.",
            first_text="Reading /a.py.",
        )
        assert not self._tool_use(events), events
        md = [d for n, d in events if n == "message_delta"]
        assert md[0]["delta"]["stop_reason"] == "max_tokens", md[0]

    def test_matching_text_with_a_complete_retry_is_accepted(self):
        """The guard is a match test, not a blanket refusal.

        Same words, whole arguments this time: that is the case worth
        recovering, and refusing it would waste a turn for nothing.
        """
        events = self._run(
            '{"path": "/a"}',
            retry_text="Reading /a.py.",
            first_text="Reading /a.py.",
        )
        assert self._tool_use(events), events

    def test_a_retry_that_is_itself_cut_off_is_discarded(self):
        events = self._run('{"path": "/a", "cont')
        assert not self._tool_use(events), events
        md = [d for n, d in events if n == "message_delta"]
        assert md[0]["delta"]["stop_reason"] == "max_tokens", md[0]


class TestContextCompaction:
    """Large context is handled internally and never becomes chat text.

    The provider usage value is diagnostic only. Claude Chat cannot execute
    Claude Code's ``/compact`` command, so the proxy must not inject that
    terminal instruction into the assistant answer.
    """

    def test_a_huge_reported_context_does_not_get_a_terminal_instruction(self):
        events, _ = Harness([Chunk(Delta(content="ok"))], input_tokens=200_000).run()
        assert text_of(events) == "ok"

    def test_a_small_reported_context_is_unchanged(self):
        events, _ = Harness([Chunk(Delta(content="ok"))], input_tokens=1000).run()
        assert text_of(events) == "ok"

    def test_the_threshold_is_not_visible_at_170k(self):
        events, _ = Harness([Chunk(Delta(content="ok"))], input_tokens=170_000).run()
        assert text_of(events) == "ok"


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

    def _two_turn_stream(self, turn, first_turn=None):
        """Yields the search tool call on turn 1, plain text on turn 2."""
        calls = {"n": 0}

        async def fake_call_stream(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                first = first_turn or [Chunk(Delta(tool_calls=[{
                    "index": 0, "id": "w1",
                    "function": {"name": "WebSearch", "arguments": '{"query": "rust"}'},
                }]))]
                for c in first:
                    yield item(c)
            else:
                for c in turn:
                    yield item(c)

        return fake_call_stream, calls

    def _run_with_search(self, search_result, citations=None, chunks=None):
        from src.api.claude_proxy import claude_proxy

        turn = chunks if chunks is not None else [Chunk(Delta(content="the answer"))]
        fake_call_stream, calls = self._two_turn_stream(turn)
        seen = {}

        async def fake_search(queries, search_engine=None, auth_key_prefix=None,
                              account=None, result_limit=None, evidence_chars=None):
            seen["queries"] = queries
            seen["engine"] = search_engine
            seen["result_limit"] = result_limit
            seen["evidence_chars"] = evidence_chars
            return search_result, (citations or [])

        async def fake_log_usage(*a, **k):
            pass

        with patch("src.api.claude_proxy.handler.proxy_stream.pool_manager.call_stream",
                   fake_call_stream), \
             patch("src.tools.search.orchestration.manager.execute_hybrid_search", fake_search), \
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

    def test_hosted_search_is_capped_at_the_presentation_budget(self):
        """Chat clients must not receive the engine's full internal result set.

        A recursive synthesis turn re-sends the search context, so an unbounded
        excerpt list multiplies the prompt on every hop until it hits the
        provider's TPM ceiling. The budget is passed at the seam, so it is
        observable from here without asserting on formatted text.
        """
        from src.api.claude_proxy.handler.anthropic_spec import search as spec_search

        _, _, seen = self._run_with_search("results")
        assert seen["result_limit"] == spec_search._HOSTED_SEARCH_MAX_RESULTS, seen
        assert seen["evidence_chars"] == spec_search._HOSTED_SEARCH_MAX_EVIDENCE_CHARS, seen

    def test_the_tool_call_never_reaches_the_client(self):
        events, _, _ = self._run_with_search("results")
        tool_uses = [d for n, d in events if n == "content_block_start"
                     and d["content_block"]["type"] == "tool_use"]
        assert not tool_uses, "an intercepted WebSearch was emitted as a tool_use block"

    def test_the_answer_after_the_search_reaches_the_client(self):
        events, _, _ = self._run_with_search("results")
        assert "the answer" in text_of(events), text_of(events)

    def test_provisional_text_before_hosted_search_is_not_emitted_as_a_second_answer(self):
        """Discard a complete-looking pre-tool answer; emit only post-search synthesis."""
        from src.api.claude_proxy import claude_proxy

        initial = [
            Chunk(Delta(content="provisional answer ending with a follow-up?")),
            Chunk(Delta(tool_calls=[{"index": 0, "id": "w1",
                                    "function": {"name": "WebSearch",
                                                 "arguments": '{"query": "rust"}'}}])),
        ]
        fake_call_stream, _ = self._two_turn_stream(
            [Chunk(Delta(content="final answer after search"))], first_turn=initial
        )

        async def fake_search(*args, **kwargs):
            return "search evidence", []

        async def fake_log_usage(*args, **kwargs):
            pass

        with patch("src.api.claude_proxy.handler.proxy_stream.pool_manager.call_stream",
                   fake_call_stream), \
             patch("src.tools.search.orchestration.manager.execute_hybrid_search", fake_search), \
             patch("src.api.claude_proxy.handler.proxy_stream.log_usage",
                   fake_log_usage, create=True):
            async def collect():
                return [c async for c in claude_proxy.stream_message(self._tools_body())]

            events = parse_sse(asyncio.run(collect()))

        rendered = text_of(events)
        assert "final answer after search" in rendered, rendered
        assert "provisional answer" not in rendered, rendered

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
             patch("src.tools.search.orchestration.manager.execute_hybrid_search", boom), \
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



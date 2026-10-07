"""Anthropic stream event order, checked against the published spec.

https://docs.claude.com/en/api/messages-streaming documents the flow as:

    1. message_start  (a Message with empty content)
    2. content blocks, each: content_block_start, one or more
       content_block_delta, content_block_stop
    3. one or more message_delta
    4. message_stop

"There may be `ping` events dispersed throughout the response as well" — and the
documented payload is `{"type": "ping"}`, nothing else.

This proxy opened the stream with a ping, and tagged its pings with `retry` and
`reason`, neither of which is in the spec. That matters because Anthropic's own
accumulator is strict about arrival order:

    if current_snapshot is None:
        if event.type == "message_start":
            return ...
        raise RuntimeError(f'Unexpected event order, got {event.type} before
                            "message_start"')

The official SDK never trips over it because Stream.__stream__ filters events by
their SSE *name* and drops `ping` before it reaches the accumulator. A client
that reads `type` off the data payload has no such filter, so it is the one that
breaks — and it breaks with a message about event ordering, which points nowhere
near a keepalive.

These drive the real proxy stream with a stubbed PoolManager, so the ordering
under test is the ordering that ships.
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class _Msg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Choice:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Usage:
    prompt_tokens = 10
    completion_tokens = 5


class _Delta:
    """The proxy reads deltas with getattr(), so they must be objects.

    A plain dict silently reads as "no content" and the test would then assert
    on an empty stream rather than on the event order.
    """

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _item(delta, finish=None, tokens=10):
    return {
        "chunk": _Msg(choices=[_Choice(delta=_Delta(**delta), finish_reason=finish)],
                      usage=_Usage()),
        "model_id": "gemini-3.6-flash",
        "input_tokens": tokens,
    }


def _stub_pool(turns, monkeypatch):
    """Make PoolManager.call_stream serve the given turns in order.

    call_stream is bound on the PoolManager instance, so it has to be replaced
    there — the proxy reaches it through `pool_manager.call_stream`.
    """
    from src.core.pool_manager import pool_manager

    state = {"n": 0}

    async def call_stream(**_kw):
        for item in turns[state["n"]]:
            yield item
        state["n"] += 1

    monkeypatch.setattr(pool_manager, "call_stream", call_stream)


def _events(chunks):
    out = []
    for c in chunks:
        text = c.decode("utf-8") if isinstance(c, (bytes, bytearray)) else str(c)
        for block in text.split("\n\n"):
            for line in block.split("\n"):
                if line.startswith("data: "):
                    out.append(json.loads(line[6:]))
    return out


def _run_stream(body):
    from src.api.claude_proxy import claude_proxy

    async def go():
        return [c async for c in claude_proxy.stream_message(body, "kp")]

    return _events(asyncio.run(go()))


PLAIN_BODY = {
    "model": "gemini-flash",
    "max_tokens": 128,
    "stream": True,
    "messages": [{"role": "user", "content": "hi"}],
}

TOOL_BODY = {
    **PLAIN_BODY,
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "weather in Paris?"}],
    "tools": [{
        "name": "get_weather",
        "description": "Get weather",
        "input_schema": {"type": "object",
                         "properties": {"city": {"type": "string"}}},
    }],
}

TOOL_TURNS = [
    [_item({"tool_calls": [{
        "index": 0, "id": "call_1", "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
    }]}, finish="tool_calls")],
    [_item({"content": "Sunny in Paris."}, finish="stop", tokens=12)],
]


# ── ordering ────────────────────────────────────────────────────────────────

class TestMessageStartComesFirst:
    def test_a_plain_reply_opens_with_message_start(self, monkeypatch):
        _stub_pool([[_item({"content": "hi"}, finish="stop")]], monkeypatch)

        events = _run_stream(dict(PLAIN_BODY))

        assert events, "stream produced nothing"
        assert events[0]["type"] == "message_start", (
            f"stream opened with {events[0]['type']!r}; the SDK accumulator "
            f"raises 'Unexpected event order' for anything else"
        )

    def test_a_tool_round_trip_also_opens_with_message_start(self, monkeypatch):
        _stub_pool(TOOL_TURNS, monkeypatch)

        events = _run_stream(dict(TOOL_BODY))

        assert events[0]["type"] == "message_start"

    def test_only_one_message_start_across_a_tool_round_trip(self, monkeypatch):
        """The recursion that runs a tool result re-enters the same code path.

        A second message_start would restart the client's event state in the
        middle of one logical message.
        """
        _stub_pool(TOOL_TURNS, monkeypatch)

        types = [e["type"] for e in _run_stream(dict(TOOL_BODY))]

        assert types.count("message_start") == 1, types

    def test_no_content_block_precedes_message_start(self, monkeypatch):
        """The context-size warning emits a text block before the model replies.

        If it lands ahead of message_start the same ordering error fires.
        """
        _stub_pool([[_item({"content": "hi"}, finish="stop")]], monkeypatch)
        body = dict(PLAIN_BODY)
        # Force the warning branch: the check compares estimated input tokens.
        body["messages"] = [{"role": "user", "content": "x" * 900_000}]

        types = [e["type"] for e in _run_stream(body)]

        assert types[0] == "message_start", types[:4]


class TestSingleTerminalPair:
    def test_message_delta_appears_once(self, monkeypatch):
        _stub_pool([[_item({"content": "hi"}, finish="stop")]], monkeypatch)

        types = [e["type"] for e in _run_stream(dict(PLAIN_BODY))]

        assert types.count("message_delta") == 1, types

    def test_message_stop_appears_once(self, monkeypatch):
        _stub_pool([[_item({"content": "hi"}, finish="stop")]], monkeypatch)

        types = [e["type"] for e in _run_stream(dict(PLAIN_BODY))]

        assert types.count("message_stop") == 1, types

    def test_message_stop_is_last(self, monkeypatch):
        _stub_pool([[_item({"content": "hi"}, finish="stop")]], monkeypatch)

        types = [e["type"] for e in _run_stream(dict(PLAIN_BODY))]

        assert types[-1] == "message_stop", types


class TestToolRoundTrip:
    """A tool result runs a second turn inside the same message.

    Only an intercepted tool (web_search / web_fetch) re-enters the stream here;
    an ordinary tool call ends the message so the client can run it. What
    matters for ordering is that the continuation does not restart the message.
    """

    def _interception(self, monkeypatch):
        """Stub the search so the intercepted path completes."""
        async def fake_search(_queries, **_kw):
            return "SEARCH RESULT", []

        monkeypatch.setattr(
            "src.core.providers.search_manager.execute_hybrid_search", fake_search)

    def test_a_recursive_turn_adds_no_second_message_start(self, monkeypatch):
        self._interception(monkeypatch)
        turns = [
            [_item({"tool_calls": [{
                "index": 0, "id": "call_1", "type": "function",
                "function": {"name": "web_search",
                             "arguments": '{"query":"weather"}'},
            }]}, finish="tool_calls")],
            [_item({"content": "Sunny."}, finish="stop", tokens=12)],
        ]
        _stub_pool(turns, monkeypatch)

        types = [e["type"] for e in _run_stream(dict(TOOL_BODY))]

        assert types.count("message_start") == 1, types

    def test_a_recursive_turn_keeps_one_terminal_pair(self, monkeypatch):
        self._interception(monkeypatch)
        turns = [
            [_item({"tool_calls": [{
                "index": 0, "id": "call_1", "type": "function",
                "function": {"name": "web_search",
                             "arguments": '{"query":"weather"}'},
            }]}, finish="tool_calls")],
            [_item({"content": "Sunny."}, finish="stop", tokens=12)],
        ]
        _stub_pool(turns, monkeypatch)

        types = [e["type"] for e in _run_stream(dict(TOOL_BODY))]

        assert types.count("message_delta") == 1, types
        assert types.count("message_stop") == 1, types
        assert types[-1] == "message_stop", types


# ── ping shape ──────────────────────────────────────────────────────────────

class TestPingPayload:
    def test_ping_carries_only_type(self, monkeypatch):
        """The documented payload is `{"type": "ping"}`.

        The extra retry/reason fields were never in the spec, and a client
        switching on `type` reads them as part of an event it does not know.
        """
        _stub_pool([[_item({"content": "hi"}, finish="stop")]], monkeypatch)

        events = _run_stream(dict(PLAIN_BODY))
        pings = [e for e in events if e.get("type") == "ping"]

        assert pings, "no ping emitted; the keepalive path changed"
        for p in pings:
            assert set(p.keys()) == {"type"}, p

    def test_a_ping_still_keeps_the_connection_alive(self, monkeypatch):
        """Guard against the fix becoming "stop pinging".

        The reason the ping exists is a model that takes longer than the
        interval to produce its first chunk; dropping it would reintroduce the
        stall it was added to prevent.
        """
        _stub_pool([[_item({"content": "hi"}, finish="stop")]], monkeypatch)

        events = _run_stream(dict(PLAIN_BODY))

        assert any(e.get("type") == "ping" for e in events)

    def test_ping_never_displaces_content(self, monkeypatch):
        _stub_pool([[_item({"content": "hello there"}, finish="stop")]], monkeypatch)

        events = _run_stream(dict(PLAIN_BODY))
        text = "".join(
            e["delta"]["text"] for e in events
            if e.get("type") == "content_block_delta"
            and e.get("delta", {}).get("type") == "text_delta"
        )

        assert "hello there" in text


# ── what the SDK actually enforces ──────────────────────────────────────────

class TestToolCallsSurvive:
    """Regression: tool calls were silently dropped from every stream.

    `_buffer_tool_calls` read the delta as

        getattr(delta, "tool_calls", None) or delta.get("tool_calls")
        if hasattr(delta, "get") else None

    Python parses that as `(a or b) if hasattr(...) else None`, so for the object
    deltas the proxy actually receives — `hasattr(delta, "get")` is False — the
    whole expression was None and every tool call vanished. The stream then ended
    with `end_turn` and no tool_use block: a successful response that silently
    drops the only thing the client asked for.
    """

    def test_an_object_delta_with_tool_calls_still_yields_a_tool_use_block(
            self, monkeypatch):
        _stub_pool([TOOL_TURNS[0]], monkeypatch)

        events = _run_stream(dict(TOOL_BODY))
        starts = [e for e in events if e["type"] == "content_block_start"]

        assert [s["content_block"]["type"] for s in starts] == ["tool_use"], (
            f"no tool_use block emitted: {[s['content_block']['type'] for s in starts]}"
        )

    def test_the_tool_use_block_carries_the_id_name_and_arguments(self, monkeypatch):
        _stub_pool([TOOL_TURNS[0]], monkeypatch)

        events = _run_stream(dict(TOOL_BODY))
        block = next(e["content_block"] for e in events
                     if e["type"] == "content_block_start"
                     and e["content_block"]["type"] == "tool_use")

        assert block["id"] == "call_1"
        assert block["name"] == "get_weather"
        deltas = [e for e in events if e["type"] == "content_block_delta"]
        args = "".join(d["delta"].get("partial_json", "")
                       for d in deltas
                       if d["delta"]["type"] == "input_json_delta")
        assert json.loads(args) == {"city": "Paris"}, args

    def test_the_message_ends_with_stop_reason_tool_use(self, monkeypatch):
        """A dropped tool call also downgraded stop_reason, which is how the
        bug stayed invisible: the client got a well-formed reply with nothing
        in it instead of an error. The docs require `tool_use` here."""
        _stub_pool([TOOL_TURNS[0]], monkeypatch)

        events = _run_stream(dict(TOOL_BODY))
        finals = [e for e in events if e["type"] == "message_delta"]

        assert finals[-1]["delta"]["stop_reason"] == "tool_use", (
            f"stop_reason={finals[-1]['delta']['stop_reason']!r}"
        )

    def test_a_dict_delta_still_works(self, monkeypatch):
        """The object and dict forms are both legitimate; neither may be lost."""
        class _DictDelta(dict):
            pass

        _stub_pool([[{
            "chunk": _Msg(
                choices=[_Choice(delta=_DictDelta(tool_calls=[{
                    "index": 0, "id": "call_d", "type": "function",
                    "function": {"name": "get_weather", "arguments": "{}"},
                }]), finish_reason="tool_calls")],
                usage=_Usage()),
            "model_id": "gemini-3.6-flash",
            "input_tokens": 4,
        }]], monkeypatch)

        events = _run_stream(dict(TOOL_BODY))
        starts = [e["content_block"]["type"] for e in events
                  if e["type"] == "content_block_start"]

        assert "tool_use" in starts, starts


class TestAnthropicAccumulator:
    """The order the router emits must survive the SDK's own state machine."""

    @staticmethod
    def _replay(events):
        from anthropic.lib.streaming._messages import accumulate_event

        snap = None
        bufs = {}
        for e in events:
            snap = accumulate_event(event=e, current_snapshot=snap, json_bufs=bufs)
        return snap

    def test_a_tool_turn_accumulates(self, monkeypatch):
        pytest.importorskip("anthropic")
        _stub_pool(TOOL_TURNS, monkeypatch)

        events = _run_stream(dict(TOOL_BODY))

        # Ping is filtered out first: the official SDK drops it on the SSE event
        # name before it reaches the accumulator.
        snap = self._replay([e for e in events if e["type"] != "ping"])
        assert snap is not None
        # A tool turn carries a tool_use block rather than text, which is what
        # proves the ordering let the SDK reach content_block_start at all.
        assert [getattr(b, "type", "?") for b in snap.content] == ["tool_use"]
        assert sum(1 for e in events if e["type"] == "message_start") == 1

    def test_ping_before_message_start_is_what_the_sdk_rejects(self):
        """Documents the failure mode, so the ordering fix is not accidental.

        If this ever stops raising, the SDK relaxed its rule and the ordering
        above is merely tidy rather than required.
        """
        pytest.importorskip("anthropic")

        with pytest.raises(RuntimeError, match="before \"message_start\""):
            self._replay([{"type": "ping"}, {
                "type": "message_start",
                "message": {"id": "m", "type": "message", "role": "assistant",
                            "content": [], "model": "m", "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 1, "output_tokens": 1}},
            }])

    def test_the_full_text_reply_accumulates(self, monkeypatch):
        pytest.importorskip("anthropic")
        _stub_pool([[_item({"content": "hi"}, finish="stop")]], monkeypatch)

        events = _run_stream(dict(PLAIN_BODY))
        snap = self._replay([e for e in events if e["type"] != "ping"])

        assert snap is not None
        assert "hi" in [getattr(b, "text", None) for b in snap.content]
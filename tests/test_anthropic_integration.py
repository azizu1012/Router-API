"""Anthropic protocol integration tests.

Drives the real ClaudeProxy with a mocked PoolManager, so the full request path
(_convert_messages → proxy → anthropic_spec → SSE serialization) is exercised
without network or API keys.

Focus: verify information actually survives the hop between nodes — that params
reach the backend call, and that usage/stop_reason/signature reach the client.

Run: pytest tests/test_anthropic_integration.py -v
"""

import asyncio
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ── Doubles for the OpenAI-shaped structures PoolManager yields ──────────────

class FakeDelta:
    def __init__(self, content=None, reasoning=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning
        self.thought = None
        self.thought_signature = None
        self.tool_calls = tool_calls

    def get(self, key, default=None):
        return getattr(self, key, default)


class FakeChoice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason
        self.index = 0


class FakeChunk:
    def __init__(self, delta, finish_reason=None):
        self.choices = [FakeChoice(delta, finish_reason)]


class FakeMessage:
    def __init__(self, content="", reasoning=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning
        self.tool_calls = tool_calls


class FakeResponse:
    def __init__(self, message, usage=None):
        self.choices = [types.SimpleNamespace(message=message, finish_reason="stop", index=0)]
        self.usage = usage or {"prompt_tokens": 100, "completion_tokens": 25}


def make_stream_item(chunk, api_key="k-abcd123456", model_id="gemini-3-flash",
                     input_tokens=1000):
    """Build a pool stream item.

    Must be a dict: PoolManager.call_stream yields dicts, and the proxy reads them
    with .get(). Mirroring the real contract is the point of this double.
    """
    return {
        "chunk": chunk,
        "api_key": api_key,
        "model_id": model_id,
        "input_tokens": input_tokens,
        "reservation": {},
    }


def parse_sse(chunks: list) -> list:
    """Parse raw SSE bytes into [(event_name, data_dict), ...]."""
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


def run_stream(gen) -> list:
    async def _collect():
        return [c async for c in gen]
    return asyncio.run(_collect())


# ── Non-stream: params reach the backend, usage reaches the client ──────────

@pytest.fixture
def proxy():
    from src.api.claude_proxy import claude_proxy
    return claude_proxy


@pytest.fixture
def capture(monkeypatch):
    """Patch pool_manager + log_usage and record what the proxy passes through.

    Everything is patched through monkeypatch so the PoolManager singleton is
    restored after each test — a direct setattr here would leak into later tests.
    """
    import src.api.claude_proxy.handler.proxy_nonstream as ns
    import src.api.claude_proxy.handler.proxy_stream as st

    calls = {"nonstream": [], "stream": [], "logged": [], "_chunks": []}

    async def fake_call_nonstream(**kwargs):
        calls["nonstream"].append(kwargs)
        return {
            "response": FakeResponse(FakeMessage(content="hello world")),
            "api_key": "key-abcdef123456",
            "model_id": "gemini-3-flash",
            "input_tokens": 1000,
            "reservation": {},
        }

    async def fake_call_stream(**kwargs):
        calls["stream"].append(kwargs)
        for c in calls["_chunks"]:
            yield make_stream_item(c)

    async def fake_log_usage(*args):
        calls["logged"].append(args)

    monkeypatch.setattr(ns.pool_manager, "call_nonstream", fake_call_nonstream)
    monkeypatch.setattr(st.pool_manager, "call_stream", fake_call_stream)
    for mod in (ns, st):
        if hasattr(mod, "log_usage"):
            monkeypatch.setattr(mod, "log_usage", fake_log_usage)

    return calls


def stream_events(proxy, capture, chunks, body=None):
    """Run the real streaming proxy over `chunks` and return parsed SSE events."""
    body = body or {"model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}]}
    capture["_chunks"] = chunks
    return parse_sse(run_stream(proxy.stream_message(dict(body))))


class TestNonStreamParamsReachBackend:
    def test_sampling_params_forwarded(self, proxy, capture):
        body = {
            "model": "gemini-flash",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": "hi"}],
            "stop_sequences": ["HALT"],
            "top_p": 0.7,
            "top_k": 20,
        }
        asyncio.run(proxy.create_message(body))
        kw = capture["nonstream"][0]
        assert kw["sampling_params"]["stop_sequences"] == ["HALT"]
        assert kw["sampling_params"]["top_p"] == 0.7
        assert kw["sampling_params"]["top_k"] == 20

    def test_no_sampling_params_when_absent(self, proxy, capture):
        body = {"model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}]}
        asyncio.run(proxy.create_message(body))
        assert capture["nonstream"][0]["sampling_params"] == {}

    def test_tool_choice_none_strips_tools(self, proxy, capture):
        body = {
            "model": "gemini-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "Bash", "description": "shell",
                       "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "none"},
        }
        asyncio.run(proxy.create_message(body))
        assert not capture["nonstream"][0]["tools"]

    def test_tool_choice_tool_keeps_tools(self, proxy, capture):
        body = {
            "model": "gemini-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "Bash", "description": "shell",
                       "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "Bash"},
        }
        asyncio.run(proxy.create_message(body))
        assert capture["nonstream"][0]["tools"]


class TestNonStreamResponseShape:
    def _run(self, proxy, capture, body):
        return asyncio.run(proxy.create_message(body))

    def test_usage_has_all_four_anthropic_fields(self, proxy, capture):
        msg = self._run(proxy, capture, {
            "model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}]})
        assert set(msg["usage"]) == {
            "input_tokens", "output_tokens",
            "cache_creation_input_tokens", "cache_read_input_tokens",
        }

    def test_cache_control_produces_cache_read(self, proxy, capture):
        # Needs a prompt above Anthropic's minimum cacheable prefix, else the
        # markers are a no-op and reporting cache tokens would be a lie.
        msg = self._run(proxy, capture, {
            "model": "gemini-flash",
            "system": [{"type": "text", "text": "sys " * 3000,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert msg["usage"]["cache_read_input_tokens"] > 0
        assert (msg["usage"]["input_tokens"]
                + msg["usage"]["cache_read_input_tokens"]) > 0

    def test_thinking_block_has_signature(self, proxy, capture, monkeypatch):
        import src.api.claude_proxy.handler.proxy_nonstream as ns

        async def fake_call_nonstream(**kwargs):
            return {
                "response": FakeResponse(
                    FakeMessage(content="answer", reasoning="because reasons")),
                "api_key": "key-abcdef123456", "model_id": "gemini-3-flash",
                "input_tokens": 1000, "reservation": {},
            }

        monkeypatch.setattr(ns.pool_manager, "call_nonstream", fake_call_nonstream)
        msg = self._run(proxy, capture, {
            "model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}]})
        thinking = [b for b in msg["content"] if b["type"] == "thinking"]
        assert thinking and thinking[0]["signature"].startswith("gmni_")

    def test_log_usage_is_called(self, proxy, capture):
        # Regression: Claude traffic never reached usage_logs.db before.
        self._run(proxy, capture, {
            "model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}]})
        assert capture["logged"], "log_usage must be called on the non-stream path"

    def test_log_usage_carries_key_and_tokens(self, proxy, capture):
        self._run(proxy, capture, {
            "model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}]})
        args = capture["logged"][0]
        assert len(args) >= 4
        assert isinstance(args[2], int) and isinstance(args[3], int)


# ── Stream: SSE event ordering and payload completeness ─────────────────────

class TestStreamEvents:
    def _events(self, proxy, capture, chunks, body=None):
        return stream_events(proxy, capture, chunks, body)

    def test_message_start_before_content(self, proxy, capture):
        chunks = [FakeChunk(FakeDelta(content="hi"))]
        events = self._events(proxy, capture, chunks)
        names = [n for n, _ in events]
        assert "message_start" in names
        assert names.index("message_start") < names.index("content_block_start")
        assert names[-1] == "message_stop"

    def test_content_block_lifecycle_balanced(self, proxy, capture):
        chunks = [FakeChunk(FakeDelta(content="hello world"))]
        names = [n for n, _ in self._events(proxy, capture, chunks)]
        starts = names.count("content_block_start")
        stops = names.count("content_block_stop")
        assert starts == stops, f"unbalanced blocks: {starts} starts vs {stops} stops"

    def test_text_deltas_reassemble_to_model_text(self, proxy, capture):
        chunks = [
            FakeChunk(FakeDelta(content="Hello ")),
            FakeChunk(FakeDelta(content="world")),
        ]
        events = self._events(proxy, capture, chunks)
        text = "".join(
            d["delta"]["text"] for n, d in events
            if n == "content_block_delta" and d["delta"]["type"] == "text_delta"
        )
        assert "Hello world" in text

    def test_message_delta_carries_full_usage(self, proxy, capture):
        # Anthropic reads final totals from message_delta, so input_tokens must be
        # present there — not just in message_start.
        chunks = [FakeChunk(FakeDelta(content="hi"))]
        events = self._events(proxy, capture, chunks)
        md = [d for n, d in events if n == "message_delta"][0]
        assert set(md["usage"]) == {
            "input_tokens", "output_tokens",
            "cache_creation_input_tokens", "cache_read_input_tokens",
        }
        assert md["usage"]["input_tokens"] > 0
        assert md["usage"]["output_tokens"] > 0

    def test_stop_reason_end_turn(self, proxy, capture):
        chunks = [FakeChunk(FakeDelta(content="hi"), finish_reason="stop")]
        events = self._events(proxy, capture, chunks)
        md = [d for n, d in events if n == "message_delta"][0]
        assert md["delta"]["stop_reason"] == "end_turn"

    def test_stop_reason_max_tokens(self, proxy, capture):
        chunks = [FakeChunk(FakeDelta(content="hi"), finish_reason="length")]
        events = self._events(proxy, capture, chunks)
        md = [d for n, d in events if n == "message_delta"][0]
        assert md["delta"]["stop_reason"] == "max_tokens"

    def test_stop_reason_refusal(self, proxy, capture):
        chunks = [FakeChunk(FakeDelta(content=""), finish_reason="content_filter")]
        events = self._events(proxy, capture, chunks)
        md = [d for n, d in events if n == "message_delta"][0]
        assert md["delta"]["stop_reason"] == "refusal"

    def test_stop_sequence_reported_in_delta(self, proxy, capture):
        chunks = [FakeChunk(FakeDelta(content="done HALT"), finish_reason="stop")]
        body = {
            "model": "gemini-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "stop_sequences": ["HALT"],
        }
        events = self._events(proxy, capture, chunks, body)
        md = [d for n, d in events if n == "message_delta"][0]
        assert md["delta"]["stop_reason"] == "stop_sequence"
        assert md["delta"]["stop_sequence"] == "HALT"

    def test_tool_use_emits_input_json_delta(self, proxy, capture):
        tool_call = {"index": 0, "id": "toolu_x1", "function": {
            "name": "Bash", "arguments": '{"command":"ls"}'}}
        chunks = [
            FakeChunk(FakeDelta(content="let me look", tool_calls=[tool_call])),
            FakeChunk(FakeDelta(tool_calls=[tool_call]), finish_reason="tool_calls"),
        ]
        events = self._events(proxy, capture, chunks)
        starts = [d for n, d in events if n == "content_block_start"
                  and d["content_block"]["type"] == "tool_use"]
        assert starts, "expected a tool_use content_block_start"
        assert starts[0]["content_block"]["name"] == "Bash"
        json_deltas = [
            d for n, d in events
            if n == "content_block_delta" and d["delta"]["type"] == "input_json_delta"
        ]
        assert json_deltas
        assert "ls" in json_deltas[-1]["delta"]["partial_json"]
        md = [d for n, d in events if n == "message_delta"][0]
        assert md["delta"]["stop_reason"] == "tool_use"

    def test_agent_tool_becomes_agent_use_block(self, proxy, capture):
        # Gemini returns tool name "Agent"; the client expects agent_use.
        # Args arrive as JSON fragments across chunks, as a real stream does.
        chunks = [
            FakeChunk(FakeDelta(tool_calls=[
                {"index": 0, "id": "toolu_a1", "function": {"name": "Agent", "arguments": ""}}])),
            FakeChunk(FakeDelta(tool_calls=[
                {"index": 0, "function": {"arguments": '{"prompt":'}}])),
            FakeChunk(FakeDelta(tool_calls=[
                {"index": 0, "function": {"arguments": '"explore src"}'}}]),
                finish_reason="tool_calls"),
        ]
        events = self._events(proxy, capture, chunks)
        starts = [d for n, d in events if n == "content_block_start"]
        agent = [d for d in starts if d["content_block"]["type"] == "agent_use"]
        assert agent, "Agent tool must surface as agent_use"
        assert agent[0]["content_block"]["agent_type"] == "general-purpose"
        assert agent[0]["content_block"]["prompt"] == "explore src"

    def test_thinking_signature_delta_non_empty(self, proxy, capture):
        chunks = [
            FakeChunk(FakeDelta(reasoning="let me think")),
            FakeChunk(FakeDelta(content="answer"), finish_reason="stop"),
        ]
        events = self._events(proxy, capture, chunks)
        sigs = [
            d["delta"]["signature"] for n, d in events
            if n == "content_block_delta" and d["delta"]["type"] == "signature_delta"
        ]
        assert sigs, "thinking must emit a signature_delta"
        assert all(s for s in sigs), "signature_delta must not be empty"
        assert sigs[0].startswith("gmni_")

    def test_stream_params_forwarded(self, proxy, capture):
        body = {
            "model": "gemini-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "stop_sequences": ["STOP"], "top_p": 0.5, "top_k": 10,
        }
        stream_events(proxy, capture, [FakeChunk(FakeDelta(content="hi"))], body)
        sp = capture["stream"][0]["sampling_params"]
        assert sp["stop_sequences"] == ["STOP"]
        assert sp["top_p"] == 0.5
        assert sp["top_k"] == 10

    def test_stream_logs_usage(self, proxy, capture):
        stream_events(proxy, capture, [FakeChunk(FakeDelta(content="hi"))])
        assert capture["logged"], "log_usage must be called on the stream path"

    def test_ping_is_emitted_before_first_token(self, proxy, capture):
        """The point of a keepalive is to arrive before there is anything to
        print.

        This used to assert `ping` came before `message_start`, which is what
        the proxy emitted — but the docs open the stream with `message_start`
        and only then allow pings to be "dispersed throughout". A client that
        reads `type` off the data payload fails on that order; see
        tests/test_anthropic_stream_order.py.
        """
        chunks = [FakeChunk(FakeDelta(content="hi"))]
        events = self._events(proxy, capture, chunks)
        names = [n for n, _ in events]
        assert "ping" in names, f"no keepalive ping: {names}"
        assert names.index("message_start") == 0, names
        assert names.index("ping") < names.index("content_block_delta"), names

    def test_block_indices_are_unique_and_ordered(self, proxy, capture):
        chunks = [
            FakeChunk(FakeDelta(reasoning="thinking...")),
            FakeChunk(FakeDelta(content="answer")),
            FakeChunk(FakeDelta(), finish_reason="stop"),
        ]
        events = self._events(proxy, capture, chunks)
        indices = [d["index"] for n, d in events if n == "content_block_start"]
        assert indices == sorted(indices), f"block indices out of order: {indices}"
        assert len(indices) == len(set(indices)), f"duplicate block indices: {indices}"


# ── Error propagation ───────────────────────────────────────────────────────

class TestStreamErrorEvent:
    def test_mid_stream_failure_emits_sse_error(self, proxy, capture, monkeypatch):
        import src.api.claude_proxy.handler.proxy_stream as st

        async def boom(**kwargs):
            yield make_stream_item(FakeChunk(FakeDelta(content="partial")))
            raise RuntimeError("upstream exploded")

        monkeypatch.setattr(st.pool_manager, "call_stream", boom)
        chunks = run_stream(proxy.stream_message({
            "model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}]}))
        events = parse_sse(chunks)
        names = [n for n, _ in events]
        assert "error" in names, f"no SSE error event; got {names}"
        err = [d for n, d in events if n == "error"][0]
        assert err["type"] == "error"
        assert "error" in err and "message" in err["error"]
        # The stream must still be terminated so the client does not hang.
        assert names[-1] == "message_stop"


# ── Node connectivity: count_tokens + models ────────────────────────────────

class TestCountTokens:
    def test_excludes_max_tokens(self):
        # Anthropic count_tokens is input-only. Folding in max_tokens inflated the
        # client's /context reading by thousands.
        from src.api.claude_proxy.handler.anthropic_spec import estimate_input_tokens
        body = {
            "model": "gemini-flash",
            "max_tokens": 64000,
            "system": "you are helpful",
            "messages": [{"role": "user", "content": "hello"}],
        }
        assert estimate_input_tokens(body) < 64000

    def test_grows_with_content(self):
        from src.api.claude_proxy.handler.anthropic_spec import estimate_input_tokens
        small = {"messages": [{"role": "user", "content": "hi"}]}
        large = {"messages": [{"role": "user", "content": "hi " * 2000}]}
        assert estimate_input_tokens(large) > estimate_input_tokens(small)


class TestModelsEndpointShape:
    def test_superset_of_openai_and_anthropic(self):
        from src.server.openai_server.routes.standard_routes import _model_entry
        entry = _model_entry({"id": "gemini-flash", "display": "Gemini Flash",
                              "root": "gemini-3-flash", "context_length": 220000})
        # OpenAI client fields
        assert entry["object"] == "model"
        assert entry["created"] == 0
        # Anthropic client fields
        assert entry["type"] == "model"
        assert entry["created_at"].endswith("Z")
        assert entry["display_name"] == "Gemini Flash"
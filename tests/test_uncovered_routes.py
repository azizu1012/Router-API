"""Three ways a request used to come back broken, all found by sweeping the
endpoints that no test had touched.

1. `/v1/completions` returned 503 for any prompt that omitted `max_tokens`.
2. `/v1/models` claimed to be an Anthropic+OpenAI superset but was missing
   `lifecycle`, which Anthropic's ModelInfo requires.
3. `web_search: true` on the OpenAI chat dialect handed the client the router's
   own `WebSearch` tool, which the client has no implementation for.
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ── 1. a default that only fires when the key is absent ─────────────────────

class TestUnsetMaxTokens:
    """`body.get("max_tokens", DEFAULT)` returns DEFAULT only when the key is
    missing. A caller that always populates the body sends an explicit None for
    "unset", so the default never applied and `int(None)` raised."""

    def test_a_none_max_tokens_does_not_raise(self, monkeypatch):
        from src.api.opencode_proxy.handler.proxy import OpenCodeProxy

        captured = {}

        class _Resp:
            choices = [type("C", (), {
                "message": type("M", (), {"role": "assistant", "content": "hi",
                                          "tool_calls": []})(),
                "finish_reason": "stop"})()]

        async def fake_nonstream(**kwargs):
            captured.update(kwargs)
            return {"response": _Resp(), "api_key": "k", "model_id": "m",
                    "input_tokens": 5}

        from src.core.pool_manager import pool_manager
        monkeypatch.setattr(pool_manager, "call_nonstream", fake_nonstream)

        proxy = OpenCodeProxy()
        body = {"model": "gemini-flash", "max_tokens": None,
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": None}
        asyncio.run(proxy.chat_completion(body, account={}, is_opencode=False))

        assert captured["max_tokens"] > 0, captured["max_tokens"]
        assert captured["temperature"] is not None

    def test_the_legacy_route_does_not_send_an_explicit_none(self):
        """The caller-side half: do not write a None into the body at all."""
        import inspect

        from src.server.openai_server.routes import completions_routes

        src = inspect.getsource(completions_routes.completions)
        assert '"max_tokens": body.get("max_tokens")' not in src, (
            "the route writes an explicit None into chat_body again")
        assert 'if body.get("max_tokens")' in src


# ── 2. /v1/models has to satisfy both schemas ───────────────────────────────

class TestModelListingIsASuperset:
    def test_an_entry_validates_as_an_anthropic_model_info(self):
        pytest.importorskip("anthropic.types")
        from src.server.openai_server.routes.standard_routes import _model_entry

        entry = _model_entry({"id": "gemini-flash", "display": "Flash",
                              "context_length": 220000})

        import anthropic.types as T
        T.ModelInfo.model_validate(entry)

    def test_lifecycle_is_present_and_valid(self):
        import typing

        pytest.importorskip("anthropic.types")
        import anthropic.types as T
        from src.server.openai_server.routes.standard_routes import _model_entry

        entry = _model_entry({"id": "m"})

        allowed = typing.get_args(T.ModelInfo.model_fields["lifecycle"].annotation)
        assert entry["lifecycle"] in allowed, entry["lifecycle"]

    def test_an_entry_still_validates_as_an_openai_model(self):
        pytest.importorskip("openai.types.model")
        from openai.types.model import Model
        from src.server.openai_server.routes.standard_routes import _model_entry

        entry = _model_entry({"id": "gemini-flash", "display": "Flash",
                              "context_length": 220000})

        Model.model_validate(entry)

    def test_every_required_anthropic_field_is_emitted(self):
        pytest.importorskip("anthropic.types")
        import anthropic.types as T
        from src.server.openai_server.routes.standard_routes import _model_entry

        required = {n for n, f in T.ModelInfo.model_fields.items()
                    if f.is_required()}
        entry = _model_entry({"id": "m"})

        assert not (required - set(entry)), sorted(required - set(entry))


# ── 3. the router's own search tool must not escape ────────────────────────

class _ToolCall:
    def __init__(self, name="WebSearch", args='{"query": "capital of France"}'):
        self.id = "call_1"
        self.type = "function"
        self.function = type("F", (), {"name": name, "arguments": args})()


class _Msg:
    def __init__(self, content="", tool_calls=None):
        self.role = "assistant"
        self.content = content
        self.tool_calls = tool_calls or []


class _Resp:
    """The facade's shape: a SimpleNamespace with choices, not `message`."""

    def __init__(self, content="", tool_calls=None):
        self.choices = [type("C", (), {
            "message": _Msg(content, tool_calls),
            "finish_reason": "tool_calls" if tool_calls else "stop",
        })()]


class TestSearchToolDoesNotLeak:
    """`pool_manager` has no web_search parameter, so the injected WebSearch
    tool is the only search mechanism on this path. Nothing intercepted it, so
    the client received an internal tool plus an otherwise empty response."""

    def test_a_web_search_call_is_found_on_the_facade_shape(self):
        from src.api.opencode_proxy.handler.search_intercept import (
            find_intercepted,
        )

        resp = _Resp(tool_calls=[_ToolCall()])

        from src.api.opencode_proxy.handler.search_intercept import (
            _tool_calls_of,
        )
        found = find_intercepted(_tool_calls_of({"response": resp}))

        assert found is not None, "the intercepted call was not seen"

    def test_the_loop_runs_the_search_and_returns_the_final_answer(self):
        from src.api.opencode_proxy.handler import search_intercept as si

        seen = []

        async def fake_search(queries, **_kw):
            seen.extend(queries)
            return "SEARCH RESULT", [{"url": "http://x", "title": "X"}]

        import src.core.providers.search_manager as sm
        original = sm.execute_hybrid_search
        sm.execute_hybrid_search = fake_search
        try:
            turns = [
                {"response": _Resp(tool_calls=[_ToolCall()])},
                {"response": _Resp(content="Paris.")},
            ]
            state = {"n": 0}

            async def call_once(_msgs, _tools):
                t = turns[state["n"]]
                state["n"] += 1
                return t

            async def go():
                return await si.drive_search_loop(
                    call_once, [{"role": "user", "content": "hi"}], [],
                    {"web_search": True}, None, "kp")

            result, messages, _tools = asyncio.run(go())
        finally:
            sm.execute_hybrid_search = original

        assert seen == ["capital of France"], seen
        assert result["response"].choices[0].message.content == "Paris."
        roles = [m["role"] for m in messages]
        assert roles == ["user", "assistant", "tool"], roles
        assert messages[-1]["tool_call_id"] == "call_1"

    def test_a_reply_with_no_tool_call_is_returned_untouched(self):
        from src.api.opencode_proxy.handler import search_intercept as si

        async def call_once(_msgs, _tools):
            return {"response": _Resp(content="Paris.")}

        async def go():
            return await si.drive_search_loop(
                call_once, [{"role": "user", "content": "hi"}], [],
                {}, None, "kp")

        result, messages, _tools = asyncio.run(go())

        assert result["response"].choices[0].message.content == "Paris."
        assert len(messages) == 1, messages

    def test_a_turn_that_keeps_searching_is_bounded(self):
        from src.api.opencode_proxy.handler import search_intercept as si

        calls = {"n": 0}

        async def call_once(_msgs, _tools):
            calls["n"] += 1
            return {"response": _Resp(tool_calls=[_ToolCall()])}

        async def fake_search(*_a, **_kw):
            return "", []

        import src.core.providers.search_manager as sm
        original = sm.execute_hybrid_search
        sm.execute_hybrid_search = fake_search
        try:
            async def go():
                return await si.drive_search_loop(
                    call_once, [{"role": "user", "content": "hi"}], [],
                    {}, None, "kp")

            asyncio.run(go())
        finally:
            sm.execute_hybrid_search = original

        assert calls["n"] == si.MAX_SEARCH_ROUNDS, calls["n"]

    def test_a_search_failure_is_reported_to_the_model_not_raised(self):
        from src.api.opencode_proxy.handler import search_intercept as si

        async def boom(*_a, **_kw):
            raise RuntimeError("search backend down")

        import src.core.providers.search_manager as sm
        original = sm.execute_hybrid_search
        sm.execute_hybrid_search = boom
        try:
            result = asyncio.run(si.run_intercepted(_ToolCall(), {}, None, "kp"))
        finally:
            sm.execute_hybrid_search = original

        assert "search backend down" in result, result

    def test_the_tool_call_is_normalised_to_the_openai_shape(self):
        from src.api.opencode_proxy.handler.search_intercept import (
            _as_assistant_tool_call,
        )

        got = _as_assistant_tool_call({"id": "c1", "name": "WebSearch",
                                       "arguments": {"query": "x"}})

        assert got["type"] == "function"
        assert got["function"]["name"] == "WebSearch"
        assert json.loads(got["function"]["arguments"]) == {"query": "x"}

    def test_a_non_router_tool_is_left_for_the_client(self):
        from src.api.opencode_proxy.handler.search_intercept import (
            find_intercepted,
        )

        assert find_intercepted([_ToolCall(name="get_weather")]) is None
"""Adapter-level checks: does the choice reach the wire in the right spelling
for the endpoint that is actually there?

The unit tests cover the mapping. This covers the last hop, because a mapping
that is right but never handed to the HTTP client is the same silent no-op the
whole feature was before.
"""
import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.providers.custom_endpoint_client import (
    call_custom_nonstream,
    call_anthropic_nonstream,
)

OPENAI_REPLY = {
    "id": "c1", "object": "chat.completion", "created": 1, "model": "m",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                 "finish_reason": "stop"}],
}
ANTHROPIC_REPLY = {
    "id": "m1", "model": "m", "stop_reason": "end_turn",
    "content": [{"type": "text", "text": "ok"}],
}

TOOLS = [{"type": "function", "function": {"name": "Bash",
                                           "parameters": {"type": "object",
                                                          "properties": {}}}}]
FORCED = {"type": "function", "function": {"name": "Bash"}}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self.content = self

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)

    def __aiter__(self):
        async def gen():
            if False:
                yield b""
        return gen()

    def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Post:
    def __init__(self, resp):
        self._resp = resp

    def __await__(self):
        async def go():
            return self._resp
        return go().__await__()

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, resp, seen, headers):
        self._resp = resp
        self._seen = seen
        self.headers = headers or {}

    def post(self, url, json=None, timeout=None):
        self._seen["url"] = url
        self._seen["body"] = json
        return _Post(self._resp)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def close(self):
        pass


def _patch(payload):
    seen = {}

    def factory(headers=None):
        return _Session(_Resp(payload), seen, headers)

    return patch("aiohttp.ClientSession", factory), seen


@pytest.mark.anyio
class TestAnOpenAIEndpointGetsTheOpenAISpelling:
    @pytest.mark.parametrize("choice,expected", [
        ("auto", "auto"), ("none", "none"), ("required", "required"),
        (FORCED, FORCED),
    ])
    async def test_it_lands_verbatim(self, choice, expected):
        patcher, seen = _patch(OPENAI_REPLY)
        with patcher:
            await call_custom_nonstream(
                "https://ep.example/v1", "k", "m",
                [{"role": "user", "content": "x"}],
                tools=TOOLS, api_format="openai", tool_choice=choice)
        assert seen["body"]["tool_choice"] == expected

    async def test_no_choice_means_no_field_at_all(self):
        patcher, seen = _patch(OPENAI_REPLY)
        with patcher:
            await call_custom_nonstream(
                "https://ep.example/v1", "k", "m",
                [{"role": "user", "content": "x"}],
                tools=TOOLS, api_format="openai")
        assert "tool_choice" not in seen["body"]

    async def test_parallel_flag_is_carried(self):
        patcher, seen = _patch(OPENAI_REPLY)
        with patcher:
            await call_custom_nonstream(
                "https://ep.example/v1", "k", "m",
                [{"role": "user", "content": "x"}],
                tools=TOOLS, api_format="openai", parallel_tool_calls=False)
        assert seen["body"]["parallel_tool_calls"] is False


@pytest.mark.anyio
class TestAnAnthropicEndpointGetsTheAnthropicSpelling:
    @pytest.mark.parametrize("choice,expected", [
        ("auto", {"type": "auto"}),
        ("none", {"type": "none"}),
        ("required", {"type": "any"}),
        (FORCED, {"type": "tool", "name": "Bash"}),
    ])
    async def test_it_is_renamed(self, choice, expected):
        patcher, seen = _patch(ANTHROPIC_REPLY)
        with patcher:
            await call_anthropic_nonstream(
                "https://ep.example", "k", "m",
                [{"role": "user", "content": "x"}],
                tools=TOOLS, tool_choice=choice)
        assert seen["body"]["tool_choice"] == expected

    async def test_the_openai_string_never_reaches_an_anthropic_endpoint(self):
        """`required` as a bare string is not an Anthropic value."""
        patcher, seen = _patch(ANTHROPIC_REPLY)
        with patcher:
            await call_anthropic_nonstream(
                "https://ep.example", "k", "m",
                [{"role": "user", "content": "x"}],
                tools=TOOLS, tool_choice="required")
        assert seen["body"]["tool_choice"] != "required"
        assert isinstance(seen["body"]["tool_choice"], dict)

    async def test_the_openai_nested_shape_never_reaches_an_anthropic_endpoint(self):
        patcher, seen = _patch(ANTHROPIC_REPLY)
        with patcher:
            await call_anthropic_nonstream(
                "https://ep.example", "k", "m",
                [{"role": "user", "content": "x"}],
                tools=TOOLS, tool_choice=FORCED)
        assert "function" not in seen["body"]["tool_choice"]

    async def test_the_format_switch_still_routes_correctly(self):
        patcher, seen = _patch(ANTHROPIC_REPLY)
        with patcher:
            await call_custom_nonstream(
                "https://ep.example", "k", "m",
                [{"role": "user", "content": "x"}],
                tools=TOOLS, api_format="anthropic", tool_choice=FORCED)
        assert seen["url"] == "https://ep.example/v1/messages"
        assert seen["body"]["tool_choice"] == {"type": "tool", "name": "Bash"}
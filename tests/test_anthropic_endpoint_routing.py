"""The dialect decision has to survive the whole trip, not just the adapter.

Unit tests on openai_to_anthropic_body prove the translation is right; these
prove the router actually takes the Anthropic branch when an endpoint says so,
and still takes the OpenAI branch when it does not. A translation that is
correct but unreachable is still a broken feature.
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.providers.custom_endpoint_client import (
    CustomEndpointStreamGen,
    call_custom_nonstream,
)

ANTHROPIC_REPLY = {
    "id": "msg_x", "model": "some-model", "stop_reason": "end_turn",
    "content": [{"type": "text", "text": "chao ban"}],
    "usage": {"input_tokens": 4, "output_tokens": 2},
}

OPENAI_REPLY = {
    "id": "chatcmpl-x", "object": "chat.completion", "created": 1,
    "model": "some-model",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "chao ban"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
}


class _FakeResponse:
    def __init__(self, payload, status=200, stream_lines=None):
        self._payload = payload
        self.status = status
        self._stream_lines = stream_lines or []
        self.headers = {"Content-Type": "application/json"}
        self.content = self

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)

    def __aiter__(self):
        async def gen():
            for line in self._stream_lines:
                # Real SSE frames are newline-terminated; the client only emits
                # a frame once it has seen the delimiter.
                yield (line + "\n").encode("utf-8")
        return gen()

    def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _PostResult:
    """aiohttp's session.post returns something that is both awaitable and an
    async context manager. The fake needs both or half the client cannot run."""

    def __init__(self, response):
        self._response = response

    def __await__(self):
        async def _go():
            return self._response
        return _go().__await__()

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response, recorder):
        self._response = response
        self._recorder = recorder

    def post(self, url, json=None, timeout=None):
        self._recorder.append({"url": url, "body": json})
        return _PostResult(self._response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def close(self):
        pass


def _patch_session(payload, stream_lines=None, status=200):
    recorder = []

    def factory(headers=None):
        return _FakeSession(_FakeResponse(payload, status, stream_lines), recorder)

    return patch("aiohttp.ClientSession", factory), recorder


class TestTheRouterPicksTheDeclaredDialect:
    @pytest.mark.anyio
    async def test_anthropic_endpoint_is_called_at_v1_messages(self):
        patcher, calls = _patch_session(ANTHROPIC_REPLY)
        with patcher:
            resp = await call_custom_nonstream(
                "https://ep.example", "k", "some-model",
                [{"role": "user", "content": "chao"}],
                api_format="anthropic",
            )
        assert calls[0]["url"] == "https://ep.example/v1/messages"
        assert calls[0]["body"]["messages"][0]["content"] == [
            {"type": "text", "text": "chao"}]
        assert resp.choices[0].message.content == "chao ban"

    @pytest.mark.anyio
    async def test_openai_endpoint_is_still_called_at_chat_completions(self):
        patcher, calls = _patch_session(OPENAI_REPLY)
        with patcher:
            resp = await call_custom_nonstream(
                "https://ep.example/v1", "k", "some-model",
                [{"role": "user", "content": "chao"}],
                api_format="openai",
            )
        assert calls[0]["url"] == "https://ep.example/v1/chat/completions"
        assert resp.choices[0].message.content == "chao ban"

    @pytest.mark.anyio
    async def test_the_default_is_openai_so_old_endpoints_are_untouched(self):
        patcher, calls = _patch_session(OPENAI_REPLY)
        with patcher:
            await call_custom_nonstream(
                "https://ep.example", "k", "some-model",
                [{"role": "user", "content": "chao"}])
        assert calls[0]["url"] == "https://ep.example/chat/completions"

    @pytest.mark.anyio
    async def test_a_system_prompt_survives_to_the_wire(self):
        """The client here is OpenAI, so a system message has to be lifted into
        Anthropic's top-level system field or the endpoint sees no system."""
        patcher, calls = _patch_session(ANTHROPIC_REPLY)
        with patcher:
            await call_custom_nonstream(
                "https://ep.example", "k", "some-model",
                [{"role": "system", "content": "be terse"},
                 {"role": "user", "content": "chao"}],
                api_format="anthropic")
        assert calls[0]["body"]["system"] == "be terse"

    @pytest.mark.anyio
    async def test_an_http_error_is_raised_not_swallowed(self):
        patcher, _ = _patch_session({"error": {"message": "no credit"}}, status=402)
        with patcher:
            with pytest.raises(RuntimeError, match="402"):
                await call_custom_nonstream(
                    "https://ep.example", "k", "some-model",
                    [{"role": "user", "content": "chao"}],
                    api_format="anthropic")


class TestTheAnthropicStreamIsReassembledAsOpenAIChunks:
    SSE = [
        'data: {"type": "message_start", "message": {"id": "msg_1"}}',
        'data: {"type": "content_block_start", "index": 0,'
        ' "content_block": {"type": "text", "text": ""}}',
        'data: {"type": "content_block_delta", "index": 0,'
        ' "delta": {"type": "text_delta", "text": "chao"}}',
        'data: {"type": "content_block_delta", "index": 0,'
        ' "delta": {"type": "text_delta", "text": " ban"}}',
        'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"},'
        ' "usage": {"input_tokens": 4, "output_tokens": 2}}',
        'data: {"type": "message_stop"}',
        'data: {"type": "ping"}',
    ]

    @pytest.mark.anyio
    async def test_text_arrives_and_the_stream_terminates(self):
        patcher, calls = _patch_session(None, self.SSE)
        with patcher:
            gen = CustomEndpointStreamGen(
                "https://ep.example", "k", "some-model",
                [{"role": "user", "content": "chao"}],
                api_format="anthropic")
            chunks = []
            async for chunk in gen:
                chunks.append(chunk)

        assert calls[0]["url"] == "https://ep.example/v1/messages"
        assert calls[0]["body"]["stream"] is True
        text = "".join(
            (c.choices[0].delta.content or "") for c in chunks if c.choices
        )
        assert text == "chao ban"

    @pytest.mark.anyio
    async def test_the_last_chunk_carries_the_finish_reason(self):
        patcher, _ = _patch_session(None, self.SSE)
        with patcher:
            gen = CustomEndpointStreamGen(
                "https://ep.example", "k", "some-model",
                [{"role": "user", "content": "chao"}],
                api_format="anthropic")
            reasons = []
            async for chunk in gen:
                for c in (chunk.choices or []):
                    if c.finish_reason:
                        reasons.append(c.finish_reason)
        assert reasons == ["stop"]

    @pytest.mark.anyio
    async def test_a_tool_call_streams_back_as_a_tool_call(self):
        sse = [
            'data: {"type": "content_block_start", "index": 0,'
            ' "content_block": {"type": "tool_use", "id": "tu_1", "name": "get_weather",'
            ' "input": {}}}',
            'data: {"type": "content_block_delta", "index": 0,'
            ' "delta": {"type": "input_json_delta", "partial_json": "{\\"city\\":\\"Paris\\"}"}}',
            'data: {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}',
        ]
        patcher, _ = _patch_session(None, sse)
        with patcher:
            gen = CustomEndpointStreamGen(
                "https://ep.example", "k", "some-model",
                [{"role": "user", "content": "weather?"}],
                api_format="anthropic")
            names, args = [], []
            async for chunk in gen:
                for c in (chunk.choices or []):
                    for tc in (getattr(c.delta, "tool_calls", None) or []):
                        names.append(tc.function.name)
                        args.append(tc.function.arguments)
        assert "get_weather" in names
        assert json.loads("".join(args)) == {"city": "Paris"}
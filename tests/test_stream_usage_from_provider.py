"""Streaming usage must come from the provider, not from character counting.

A turn whose text never reaches the client — all-thinking, tool call, or a
safety stop — used to log `output_tokens=1`, which is what the dashboard showed
for an 8k-token reply.
"""
import asyncio
import types

from src.api.claude_proxy.handler.proxy_stream import ClaudeProxyStreamMixin


class _FakeChunk:
    def __init__(self, delta, finish_reason=None, usage=None):
        choice = types.SimpleNamespace(delta=delta, finish_reason=finish_reason, index=0)
        self.choices = [choice] if delta is not None or finish_reason else []
        self.usage = usage


async def _drain(items, body):
    proxy = ClaudeProxyStreamMixin()

    async def fake_stream(**_kwargs):
        for it in items:
            yield it

    # The mixin reaches the pool through this name; stub it for one call.
    import src.api.claude_proxy.handler.proxy_stream as mod
    original = mod.pool_manager
    mod.pool_manager = types.SimpleNamespace(call_stream=lambda **kw: fake_stream())
    try:
        chunks = []
        async for c in proxy._stream_message_impl(
            body=body,
            openai_messages=[{"role": "user", "content": "hi"}],
            openai_tools=None,
            model_alias="gemini-flash",
            temperature=0.7,
            max_tokens=256,
            thinking_config=None,
            thinking_params={},
            account=None,
            auth_key_prefix="",
            msg_id="msg_test",
            recursion_depth=0,
            start_block_index=0,
            emit_message_start=False,
        ):
            chunks.append(c)
        return chunks
    finally:
        mod.pool_manager = original


def _usage_from(chunks):
    import json
    for raw in chunks:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if "message_delta" not in text:
            continue
        payload = text.split("data: ", 1)[1].rsplit("}", 1)[0] + "}"
        return json.loads(payload)["usage"]
    raise AssertionError("no message_delta emitted")


def test_provider_usage_wins_over_char_count():
    """8k in / 1 out: the fix must report the provider's real count."""
    body = {"model": "gemini-flash", "messages": [{"role": "user", "content": "x" * 32000}]}
    empty_delta = types.SimpleNamespace(content=None, reasoning_content=None, tool_calls=None)
    items = [
        {"chunk": _FakeChunk(empty_delta), "api_key": "k", "model_id": "m"},
        # No text delta at all — output_chars stays 0.
        {"chunk": _FakeChunk(None, finish_reason="stop",
                             usage={"prompt_tokens": 8000, "completion_tokens": 512,
                                    "total_tokens": 8512}),
         "api_key": "k", "model_id": "m"},
    ]
    usage = _usage_from(asyncio.run(_drain(items, body)))
    assert usage["output_tokens"] == 512, usage


def test_char_count_still_used_when_provider_silent():
    """Providers that report nothing keep the existing character heuristic."""
    body = {"model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}]}
    delta = types.SimpleNamespace(content="a" * 400, reasoning_content=None, tool_calls=None)
    items = [
        {"chunk": _FakeChunk(delta), "api_key": "k", "model_id": "m"},
        {"chunk": _FakeChunk(None, finish_reason="stop"), "api_key": "k", "model_id": "m"},
    ]
    usage = _usage_from(asyncio.run(_drain(items, body)))
    assert usage["output_tokens"] == 100, usage



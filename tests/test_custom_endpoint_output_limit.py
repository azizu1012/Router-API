"""A custom endpoint that rejects the output size gets a smaller budget.

Some OpenAI-compatible backends cap the *response* rather than clamping it and
answer ``400 Response exceeded the output limit``. Nothing about the request is
wrong — only the requested ``max_tokens`` is larger than the provider allows —
so failing the whole turn is the wrong response, and so is retrying the exact
same payload.

These tests pin the retry on all four POST sites (OpenAI/Anthropic ×
non-stream/stream), plus the boundaries that keep it from becoming a re-raise
loop: only a 4xx, never 429, never 5xx, never a body that does not name the
output budget, and never past the floor or the retry cap.
"""

import asyncio
import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.providers.custom_endpoint import client as client_mod


# ── fakes ────────────────────────────────────────────────────────────────


class _EmptyContent:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeResponse:
    def __init__(self, status, body, payload=None):
        self.status = status
        self._body = body
        self._json = payload if payload is not None else json.loads(body)
        self.content = _EmptyContent()
        self.released = False
        self.closed = False

    async def text(self):
        return self._body

    async def json(self):
        return self._json

    def release(self):
        self.released = True

    def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.release()
        return False


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.posts = []
        self.closed = False

    async def post(self, url, json=None, timeout=None):
        self.posts.append(copy.deepcopy(json))
        if not self._responses:
            raise AssertionError("unexpected extra POST")
        return self._responses.pop(0)

    async def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _install_fake_session(monkeypatch, responses):
    sessions = []

    def factory(*args, **kwargs):
        session = _FakeSession(responses)
        sessions.append(session)
        return session

    monkeypatch.setattr(client_mod.aiohttp, "ClientSession", factory)
    return sessions


_OPENAI_OK = json.dumps(
    {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
    }
)

_ANTHROPIC_OK = json.dumps(
    {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "m",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
)

_OUTPUT_LIMIT_BODY = (
    '{"error":{"message":"Response exceeded the output limit","type":"invalid_request_error"}}'
)


def _reject(body=_OUTPUT_LIMIT_BODY, status=400):
    return _FakeResponse(status, body)


# ── classifier ───────────────────────────────────────────────────────────


class TestWhatCountsAsAnOutputLimitError:
    def test_the_reported_message_matches(self):
        assert client_mod.is_output_limit_error(400, _OUTPUT_LIMIT_BODY)

    @pytest.mark.parametrize(
        "body",
        [
            "Response exceeded the output limit",
            "output limit exceeded",
            "output_limit_reached",
            '{"error":"max_tokens is too large: 65536. This model supports at most 8192"}',
            '{"error":"Invalid max_tokens value, the valid range is [1, 8192]"}',
            '{"error":"max_completion_tokens out of range"}',
        ],
    )
    def test_output_budget_phrasings_match(self, body):
        assert client_mod.is_output_limit_error(400, body)

    @pytest.mark.parametrize(
        "status,body",
        [
            (401, "invalid api key"),
            (403, "permission denied"),
            (404, "model not found"),
            (422, "messages must not be empty"),
            (400, "invalid model"),
            (400, "maximum context length is 8192 tokens"),
            (429, "Response exceeded the output limit"),
            (500, "Response exceeded the output limit"),
            (503, "Response exceeded the output limit"),
        ],
    )
    def test_other_failures_are_left_alone(self, status, body):
        assert not client_mod.is_output_limit_error(status, body)

    def test_an_empty_body_is_not_a_match(self):
        assert not client_mod.is_output_limit_error(400, "")


class TestHowFarTheBudgetIsLowered:
    def test_halves_until_the_floor(self):
        budgets = []
        budget = 65536
        while True:
            budget = client_mod.next_output_retry_budget(budget)
            if budget is None:
                break
            budgets.append(budget)
        assert budgets == [32768, 16384, 8192, 4096]

    def test_the_floor_is_a_floor(self):
        assert client_mod.next_output_retry_budget(4097) == 4096
        assert client_mod.next_output_retry_budget(4096) is None
        assert client_mod.next_output_retry_budget(1024) is None

    @pytest.mark.parametrize("budget", [None, "", "not-a-number", {}, []])
    def test_a_missing_budget_is_not_retried(self, budget):
        assert client_mod.next_output_retry_budget(budget) is None

    def test_the_retry_cap_stops_the_loop(self):
        payload = {"max_tokens": 65536}
        assert client_mod._retry_budget_for_output_limit(
            400, _OUTPUT_LIMIT_BODY, payload, client_mod._OUTPUT_LIMIT_MAX_RETRIES
        ) is None


# ── non-stream ───────────────────────────────────────────────────────────


class TestNonStreamRetry:
    def test_openai_lowers_the_budget_until_the_endpoint_accepts(self, monkeypatch):
        sessions = _install_fake_session(
            monkeypatch,
            [_reject(), _reject(), _FakeResponse(200, _OPENAI_OK)],
        )

        resp = asyncio.run(
            client_mod.call_custom_nonstream(
                "http://endpoint.local/v1",
                "k",
                "m",
                [{"role": "user", "content": "hi"}],
                max_tokens=65536,
            )
        )

        assert resp.choices[0].message.content == "ok"
        assert [p["max_tokens"] for p in sessions[0].posts] == [65536, 32768, 16384]
        assert sessions[0].posts[0]["messages"][0]["content"] == "hi"

    def test_anthropic_lowers_the_budget_until_the_endpoint_accepts(self, monkeypatch):
        sessions = _install_fake_session(
            monkeypatch,
            [_reject(), _FakeResponse(200, _ANTHROPIC_OK)],
        )

        resp = asyncio.run(
            client_mod.call_anthropic_nonstream(
                "http://endpoint.local/v1",
                "k",
                "m",
                [{"role": "user", "content": "hi"}],
                max_tokens=8192,
            )
        )

        assert resp.choices[0].message.content == "ok"
        assert [p["max_tokens"] for p in sessions[0].posts] == [8192, 4096]

    def test_an_unrelated_4xx_is_raised_unchanged(self, monkeypatch):
        body = '{"error":"invalid model"}'
        sessions = _install_fake_session(monkeypatch, [_FakeResponse(400, body)])

        with pytest.raises(RuntimeError) as exc:
            asyncio.run(
                client_mod.call_custom_nonstream(
                    "http://endpoint.local/v1",
                    "k",
                    "m",
                    [{"role": "user", "content": "hi"}],
                    max_tokens=65536,
                )
            )

        assert f"Custom endpoint HTTP 400: {body}" in str(exc.value)
        assert len(sessions[0].posts) == 1

    def test_a_rate_limit_is_not_retried(self, monkeypatch):
        sessions = _install_fake_session(
            monkeypatch, [_FakeResponse(429, _OUTPUT_LIMIT_BODY)]
        )

        with pytest.raises(RuntimeError, match="HTTP 429"):
            asyncio.run(
                client_mod.call_custom_nonstream(
                    "http://endpoint.local/v1",
                    "k",
                    "m",
                    [{"role": "user", "content": "hi"}],
                    max_tokens=65536,
                )
            )

        assert len(sessions[0].posts) == 1

    def test_a_server_error_is_not_retried(self, monkeypatch):
        sessions = _install_fake_session(
            monkeypatch, [_FakeResponse(503, _OUTPUT_LIMIT_BODY)]
        )

        with pytest.raises(RuntimeError, match="HTTP 503"):
            asyncio.run(
                client_mod.call_custom_nonstream(
                    "http://endpoint.local/v1",
                    "k",
                    "m",
                    [{"role": "user", "content": "hi"}],
                    max_tokens=65536,
                )
            )

        assert len(sessions[0].posts) == 1

    def test_a_request_without_a_budget_is_not_retried(self, monkeypatch):
        sessions = _install_fake_session(monkeypatch, [_reject()])

        with pytest.raises(RuntimeError, match="HTTP 400"):
            asyncio.run(
                client_mod.call_custom_nonstream(
                    "http://endpoint.local/v1",
                    "k",
                    "m",
                    [{"role": "user", "content": "hi"}],
                )
            )

        assert len(sessions[0].posts) == 1
        assert "max_tokens" not in sessions[0].posts[0]

    def test_a_success_first_time_costs_exactly_one_post(self, monkeypatch):
        sessions = _install_fake_session(
            monkeypatch, [_FakeResponse(200, _OPENAI_OK)]
        )

        asyncio.run(
            client_mod.call_custom_nonstream(
                "http://endpoint.local/v1",
                "k",
                "m",
                [{"role": "user", "content": "hi"}],
                max_tokens=8192,
            )
        )

        assert [p["max_tokens"] for p in sessions[0].posts] == [8192]

    def test_the_extra_body_budget_is_the_one_that_is_lowered(self, monkeypatch):
        sessions = _install_fake_session(
            monkeypatch,
            [_reject(), _FakeResponse(200, _OPENAI_OK)],
        )

        asyncio.run(
            client_mod.call_custom_nonstream(
                "http://endpoint.local/v1",
                "k",
                "m",
                [{"role": "user", "content": "hi"}],
                max_tokens=16384,
                extra_body={"max_tokens": 60000},
            )
        )

        assert [p["max_tokens"] for p in sessions[0].posts] == [60000, 30000]

    def test_both_budget_keys_are_lowered_simultaneously(self, monkeypatch):
        sessions = _install_fake_session(
            monkeypatch,
            [_reject(), _FakeResponse(200, _OPENAI_OK)],
        )

        resp = asyncio.run(
            client_mod.call_custom_nonstream(
                "http://endpoint.local/v1",
                "k",
                "m",
                [{"role": "user", "content": "hi"}],
                max_tokens=65536,
                extra_body={"max_completion_tokens": 65536},
            )
        )

        assert resp.choices[0].message.content == "ok"
        first_post = sessions[0].posts[0]
        second_post = sessions[0].posts[1]
        assert first_post.get("max_tokens") == 65536
        assert first_post.get("max_completion_tokens") == 65536
        assert second_post.get("max_tokens") == 32768
        assert second_post.get("max_completion_tokens") == 32768


# ── stream ───────────────────────────────────────────────────────────────


class TestStreamRetry:
    def _gen(self, **kwargs):
        return client_mod.CustomEndpointStreamGen(
            "http://endpoint.local/v1",
            "k",
            "m",
            [{"role": "user", "content": "hi"}],
            **kwargs,
        )

    def test_openai_stream_retries_before_it_is_started(self, monkeypatch):
        sessions = _install_fake_session(
            monkeypatch, [_reject(), _FakeResponse(200, "", payload={})]
        )
        gen = self._gen(max_tokens=8192)

        asyncio.run(gen._start())

        assert gen._started is True
        assert [p["max_tokens"] for p in sessions[0].posts] == [8192, 4096]

    def test_anthropic_stream_retries_before_it_is_started(self, monkeypatch):
        sessions = _install_fake_session(
            monkeypatch, [_reject(), _FakeResponse(200, "", payload={})]
        )
        gen = self._gen(max_tokens=8192, api_format="anthropic")

        asyncio.run(gen._start())

        assert gen._started is True
        assert [p["max_tokens"] for p in sessions[0].posts] == [8192, 4096]

    def test_stream_stays_unstarted_and_bounded_when_every_attempt_fails(
        self, monkeypatch
    ):
        sessions = _install_fake_session(
            monkeypatch, [_reject() for _ in range(6)]
        )
        gen = self._gen(max_tokens=65536)

        with pytest.raises(RuntimeError, match="HTTP 400"):
            asyncio.run(gen._start())

        assert gen._started is False
        assert [
            p["max_tokens"] for p in sessions[0].posts
        ] == [65536, 32768, 16384, 8192, 4096]
        assert sessions[0].closed is True

    def test_an_unrelated_stream_error_is_raised_without_retry(self, monkeypatch):
        sessions = _install_fake_session(
            monkeypatch,
            [_FakeResponse(401, '{"error":"invalid api key"}')],
        )
        gen = self._gen(max_tokens=65536)

        with pytest.raises(RuntimeError, match="HTTP 401"):
            asyncio.run(gen._start())

        assert gen._started is False
        assert len(sessions[0].posts) == 1

    def test_stream_chunk_preserves_tool_call_index(self):
        tc_dict = {
            "index": 3,
            "id": "call_abc",
            "type": "function",
            "function": {"name": "test_func", "arguments": "{}"},
        }
        obj = client_mod._make_tool_call(tc_dict)
        assert getattr(obj, "index", None) == 3

        chunk = client_mod.NativeChunk({
            "choices": [{
                "delta": {"tool_calls": [tc_dict]},
                "finish_reason": None,
            }]
        })
        tc_out = chunk.choices[0].delta.tool_calls[0]
        assert tc_out.index == 3

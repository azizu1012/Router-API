import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.server.realtime.stats import StatsPusher
from src.core.providers.gemini.caller import generate_content_stream
from src.core.providers.genai_types import types


@pytest.mark.anyio
async def test_stats_pusher_snapshot_empty_and_populated():
    """Verify StatsPusher._snapshot() runs without UnboundLocalError when dicts are empty or populated."""
    pusher = StatsPusher(interval=10.0)

    # 1. Test when _custom_pool_usage and _key_model_requests are empty
    with patch("src.server.realtime.stats._rate_limiters", {}), \
         patch("src.core.providers.custom_endpoint.client._custom_pool_usage", {}), \
         patch("src.server.realtime.stats._key_model_requests", {}), \
         patch("src.server.realtime.stats._score_penalties", {}):
        snapshot = await pusher._snapshot()
        assert snapshot["type"] == "stats_snapshot"
        assert snapshot["models"] == {}
        assert snapshot["active_keys"] == 0

    # 2. Test when _custom_pool_usage and _key_model_requests have entries
    mock_limiter1 = MagicMock()
    mock_limiter1.rpm_limit = 10
    mock_limiter1._minute_req_ts = []
    mock_limiter1._minute_tokens = []
    mock_limiter1.tpm_limit = 1000
    mock_limiter1._rpd_count = 5
    mock_limiter1.rpd_limit = 100

    mock_limiter2 = MagicMock()
    mock_limiter2.rpm_limit = 20
    mock_limiter2._minute_req_ts = []
    mock_limiter2._minute_tokens = []
    mock_limiter2.tpm_limit = 2000
    mock_limiter2._rpd_count = 10
    mock_limiter2.rpd_limit = 200

    with patch("src.server.realtime.stats._rate_limiters", {"model1": mock_limiter1, "model2": mock_limiter2}), \
         patch("src.core.providers.custom_endpoint.client._custom_pool_usage", {"custom1": [1000.0, 1001.0]}), \
         patch("src.server.realtime.stats._key_model_requests", {"key1:model1": [1000.0]}), \
         patch("src.server.realtime.stats._score_penalties", {}):
        snapshot = await pusher._snapshot()
        assert "model1" in snapshot["models"]
        assert "model2" in snapshot["models"]
        assert "custom1" in snapshot["models"]
        assert snapshot["models"]["model1"]["rpm_limit"] == 10
        assert snapshot["models"]["model2"]["rpm_limit"] == 20
        assert snapshot["models"]["custom1"]["rpm_limit"] == 120
        assert snapshot["active_keys"] >= 0


@pytest.mark.anyio
async def test_responses_route_nonstream_resolves_imports():
    """Verify POST /v1/responses non-stream succeeds without undefined name errors."""
    from fastapi.testclient import TestClient
    from src.server.openai_server.routes.app_init import app
    from src.core.accounts import account_manager

    # Mock authentication to return a valid account
    mock_account = {
        "account_id": "acc_test",
        "name": "tester",
        "tier": "admin",
        "auth_key": "sk-test-key",
        "enabled": True,
        "token_key_id": "tok_1",
    }

    mock_chat_result = {
        "id": "chatcmpl-test",
        "model": "gemini-flash",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "Hello from Responses test"},
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 6}
    }

    with patch.object(account_manager, "find_by_key", return_value=mock_account), \
         patch("src.server.openai_server.auth._apply_account_limit", new_callable=AsyncMock), \
         patch("src.api.opencode_proxy.opencode_proxy.chat_completion", new_callable=AsyncMock, return_value=mock_chat_result), \
         patch("src.server.openai_server.routes.completions.responses.log_usage", new_callable=AsyncMock) as mock_log_usage:
        client = TestClient(app)
        resp = client.post(
            "/v1/responses",
            json={"model": "gemini-flash", "input": "Hello"},
            headers={"Authorization": "Bearer sk-test-key"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "response"
        assert len(data["output"]) == 1
        assert data["output"][0]["content"][0]["text"] == "Hello from Responses test"
        assert mock_log_usage.called


@pytest.mark.anyio
async def test_stream_executor_closes_gen():
    """Verify that stream_executor properly checks and closes `gen` without throwing NameError."""
    from src.api.opencode_proxy.handler.stream_executor import execute_stream

    mock_gen = MagicMock()
    mock_gen.aclose = AsyncMock()

    async def mock_call_stream(**kwargs):
        class MockDelta:
            content = "hi"
            reasoning_content = None
            thought = None
            reasoning = None
            tool_calls = None
            thought_signature = None

        class MockChoice:
            delta = MockDelta()
            finish_reason = None

        class MockChunk:
            choices = [MockChoice()]

        yield {"chunk": MockChunk(), "api_key": "k", "model_id": "m", "input_tokens": 10}

    body = {"model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}]}

    with patch("src.core.orchestration.pool_manager.pool_manager.call_stream", side_effect=mock_call_stream), \
         patch("src.core.observability.usage_logger.log_usage", new_callable=AsyncMock):
        chunks = []
        async for c in execute_stream(body, messages=body["messages"], tools=[], model_alias="gemini-flash"):
            chunks.append(c)
        assert len(chunks) > 0


@pytest.mark.anyio
async def test_gemini_caller_stream_close_logger():
    """Verify that if stream.aclose() raises an exception, logger.warning handles it without NameError."""
    mock_pool = MagicMock()
    mock_client = MagicMock()
    mock_pool.get_client = AsyncMock(return_value=mock_client)

    mock_stream = MagicMock()
    mock_stream.aclose = AsyncMock(side_effect=RuntimeError("connection reset by peer"))

    mock_client.aio.models.generate_content_stream = AsyncMock(return_value=mock_stream)

    with patch("src.core.providers.gemini.caller.logger.warning") as mock_warn:
        chunks = []
        try:
            async for chunk in generate_content_stream(
                pool=mock_pool,
                api_key="fake-key",
                model_id="gemini-flash",
                system_instruction="",
                contents=[types.Content(parts=[types.Part(text="test")])],
                max_tokens=100,
                temperature=0.7,
                top_p=1.0,
            ):
                chunks.append(chunk)
        except Exception:
            pass

        assert mock_warn.called
        assert "Error closing stream" in mock_warn.call_args[0][0]

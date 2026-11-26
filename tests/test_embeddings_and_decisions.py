"""Tests for Embeddings, Decisions API routes, provider handlers, and Gemini 4 discovery."""

import base64
import struct
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.core.providers.gemini import GeminiAPIManager, GeminiBadRequestError
from src.core.providers.gemini.caller import UpstreamStatusError


def _fake_account():
    return {
        "account_id": "acc_test",
        "name": "tester",
        "auth_key": "sk-tester-123456",
        "tier": "tier1",
        "rpm": 1000,
        "tpm": 1_000_000,
        "rpd": 100_000,
        "max_concurrent": 10,
    }


def _build_client():
    from src.server.openai_server.routes import app as real_app
    return TestClient(real_app)


def _auth_patches(acc):
    stack = ExitStack()
    stack.enter_context(patch("src.server.openai_server.auth._resolve_auth", return_value=acc["auth_key"]))
    stack.enter_context(patch("src.server.openai_server.auth._check_auth", return_value=acc))
    stack.enter_context(patch("src.server.openai_server.auth._enforce_token_limits", new=AsyncMock(return_value=None)))
    return stack


class TestGeminiAPIManagerEmbedAndDecisions:
    @pytest.mark.anyio
    async def test_call_embed_bad_request_does_not_freeze_key(self):
        mgr = GeminiAPIManager()
        mgr.pool._pool_size = 2

        mock_router = MagicMock()
        mock_router.reserve_key.return_value = {
            "key": "key-1",
            "tier": "free",
            "model_id": "gemini-embedding-2",
            "model_alias": "gemini-embedding-2",
        }
        mock_limiter = MagicMock()
        mock_limiter.acquire_quota = AsyncMock(return_value=True)

        with patch("src.core.providers.gemini.manager.router", mock_router), \
             patch.object(mgr.pool, "is_key_frozen_by_project", return_value=False), \
             patch.object(mgr.pool, "throttle", new=AsyncMock()), \
             patch("src.core.providers.gemini.manager.get_rate_limiter", return_value=mock_limiter), \
             patch("src.core.providers.gemini.manager.caller.embed_content", new=AsyncMock(side_effect=Exception("400 INVALID_ARGUMENT: bad dim"))), \
             patch("src.core.providers.gemini.manager.handle_error", new=AsyncMock()) as mock_handle_error:
            with pytest.raises(GeminiBadRequestError):
                await mgr.call_embed("gemini-embedding-2", "hello")

        mock_handle_error.assert_not_awaited()
        mock_router.release_key.assert_called_once_with("key-1")

    @pytest.mark.anyio
    async def test_call_embed_rotates_key_on_rate_limit(self):
        mgr = GeminiAPIManager()
        mgr.pool._pool_size = 2

        mock_router = MagicMock()
        mock_router.reserve_key.side_effect = [
            {"key": "key-1", "tier": "free", "model_id": "gemini-embedding-2", "model_alias": "gemini-embedding-2"},
            {"key": "key-2", "tier": "free", "model_id": "gemini-embedding-2", "model_alias": "gemini-embedding-2"},
        ]
        mock_limiter = MagicMock()
        mock_limiter.acquire_quota = AsyncMock(return_value=True)

        fake_emb = SimpleNamespace(values=[0.1, 0.2, 0.3], statistics=SimpleNamespace(token_count=5))
        fake_resp = SimpleNamespace(embeddings=[fake_emb])

        with patch("src.core.providers.gemini.manager.router", mock_router), \
             patch.object(mgr.pool, "is_key_frozen_by_project", return_value=False), \
             patch.object(mgr.pool, "throttle", new=AsyncMock()), \
             patch("src.core.providers.gemini.manager.get_rate_limiter", return_value=mock_limiter), \
             patch("src.core.providers.gemini.manager.caller.embed_content", new=AsyncMock(side_effect=[
                 Exception("429 RESOURCE_EXHAUSTED"),
                 fake_resp,
             ])), \
             patch("src.core.providers.gemini.manager.handle_error", new=AsyncMock()):
            res = await mgr.call_embed("gemini-embedding-2", "hello")

        assert [e.values for e in res["embeddings"]] == [[0.1, 0.2, 0.3]]
        assert res["api_key"] == "key-2"
        assert res["model_id"] == "gemini-embedding-2"
        assert res["input_tokens"] == 5

    @pytest.mark.anyio
    async def test_call_decisions_bad_request_does_not_freeze_key(self):
        mgr = GeminiAPIManager()
        mgr.pool._pool_size = 2

        mock_router = MagicMock()
        mock_router.reserve_key.return_value = {
            "key": "key-1",
            "tier": "free",
            "model_id": "gemini-decision-1",
            "model_alias": "gemini-decision-1",
        }
        mock_limiter = MagicMock()
        mock_limiter.acquire_quota = AsyncMock(return_value=True)

        err = UpstreamStatusError(400, "invalid payload")
        with patch("src.core.providers.gemini.manager.router", mock_router), \
             patch.object(mgr.pool, "is_key_frozen_by_project", return_value=False), \
             patch.object(mgr.pool, "throttle", new=AsyncMock()), \
             patch("src.core.providers.gemini.manager.get_rate_limiter", return_value=mock_limiter), \
             patch("src.core.providers.gemini.manager.caller.call_decisions_http", new=AsyncMock(side_effect=err)), \
             patch("src.core.providers.gemini.manager.handle_error", new=AsyncMock()) as mock_handle_error:
            with pytest.raises(GeminiBadRequestError):
                await mgr.call_decisions("gemini-decision-1", {"model": "gemini-decision-1"})

        mock_handle_error.assert_not_awaited()
        mock_router.release_key.assert_called_once_with("key-1")


class TestOpenAIEmbeddingsRoute:
    @pytest.mark.parametrize("path", ["/v1/embeddings", "/embeddings", "/opencode/v1/embeddings"])
    def test_embeddings_float_and_aliases(self, path):
        client = _build_client()
        acc = _fake_account()
        mock_call_embed = AsyncMock(return_value={
            "embeddings": [SimpleNamespace(values=[0.25, -0.5, 1.0])],
            "input_tokens": 4,
            "api_key": "key-12345678",
            "model_id": "gemini-embedding-2",
            "model_alias": "gemini-embedding-2",
        })
        with _auth_patches(acc), \
             patch("src.server.openai_server.routes.standard_routes.embeddings._apply_account_limit", new=AsyncMock()), \
             patch("src.server.openai_server.routes.standard_routes.embeddings.api_manager.call_embed", new=mock_call_embed), \
             patch("src.server.openai_server.routes.standard_routes.embeddings.log_usage", new=AsyncMock()) as mock_log:
            resp = client.post(
                path,
                headers={"Authorization": f"Bearer {acc['auth_key']}"},
                json={"model": "gemini-embedding-2", "input": "hello world", "dimensions": 3},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "list"
        assert body["model"] == "gemini-embedding-2"
        assert body["data"] == [{"object": "embedding", "index": 0, "embedding": [0.25, -0.5, 1.0]}]
        assert body["usage"] == {"prompt_tokens": 4, "total_tokens": 4}
        mock_call_embed.assert_awaited_once()
        assert mock_call_embed.call_args.kwargs["embed_config"] == {"output_dimensionality": 3}
        mock_log.assert_awaited_once()

    def test_embeddings_base64_and_batch_inputs(self):
        client = _build_client()
        acc = _fake_account()
        v1 = [1.5, -2.25]
        v2 = [0.0, 4.0]
        mock_call_embed = AsyncMock(return_value={
            "embeddings": [SimpleNamespace(values=v1), SimpleNamespace(values=v2)],
            "input_tokens": 8,
            "api_key": "key-12345678",
            "model_id": "gemini-embedding-001",
            "model_alias": "gemini-embedding-001",
        })
        with _auth_patches(acc), \
             patch("src.server.openai_server.routes.standard_routes.embeddings._apply_account_limit", new=AsyncMock()), \
             patch("src.server.openai_server.routes.standard_routes.embeddings.api_manager.call_embed", new=mock_call_embed), \
             patch("src.server.openai_server.routes.standard_routes.embeddings.log_usage", new=AsyncMock()):
            resp = client.post(
                "/v1/embeddings",
                headers={"Authorization": f"Bearer {acc['auth_key']}"},
                json={
                    "model": "gemini-embedding-001",
                    "input": ["first", "second"],
                    "encoding_format": "base64",
                },
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        decoded_0 = list(struct.unpack("<2f", base64.b64decode(data[0]["embedding"])))
        decoded_1 = list(struct.unpack("<2f", base64.b64decode(data[1]["embedding"])))
        assert decoded_0 == pytest.approx(v1)
        assert decoded_1 == pytest.approx(v2)

    def test_embeddings_rejects_oversized_input_and_bad_format(self):
        client = _build_client()
        acc = _fake_account()
        with _auth_patches(acc):
            bad_fmt = client.post(
                "/v1/embeddings",
                headers={"Authorization": f"Bearer {acc['auth_key']}"},
                json={"model": "gemini-embedding-2", "input": "hi", "encoding_format": "int8"},
            )
            assert bad_fmt.status_code == 400

            oversized = client.post(
                "/v1/embeddings",
                headers={"Authorization": f"Bearer {acc['auth_key']}"},
                json={"model": "gemini-embedding-001", "input": "a" * 10000},
            )
            assert oversized.status_code == 400


class TestNativeGeminiEmbedAndDecisionsRoutes:
    def test_native_embed_content_and_batch(self):
        client = _build_client()
        acc = _fake_account()
        mock_call_embed = AsyncMock(side_effect=[
            {
                "embeddings": [SimpleNamespace(values=[0.5, 0.6])],
                "input_tokens": 3,
                "api_key": "key-12345678",
                "model_id": "gemini-embedding-2",
                "model_alias": "gemini-embedding-2",
            },
            {
                "embeddings": [SimpleNamespace(values=[0.1, 0.2]), SimpleNamespace(values=[0.3, 0.4])],
                "input_tokens": 6,
                "api_key": "key-12345678",
                "model_id": "gemini-embedding-2",
                "model_alias": "gemini-embedding-2",
            },
        ])
        with _auth_patches(acc), \
             patch("src.server.pass_through_server.routes.gemini_handlers._check_auth", return_value=acc), \
             patch("src.server.pass_through_server.routes.gemini_handlers.account_limiter.acquire", new=AsyncMock(return_value=(True, None))), \
             patch("src.server.pass_through_server.routes.gemini_handlers.api_manager.call_embed", new=mock_call_embed), \
             patch("src.server.pass_through_server.routes.gemini_handlers.log_usage", new=AsyncMock()):
            single = client.post(
                "/v1beta/models/gemini-embedding-2:embedContent",
                headers={"x-goog-api-key": acc["auth_key"]},
                json={"content": {"parts": [{"text": "hello"}]}, "output_dimensionality": 2},
            )
            assert single.status_code == 200
            assert single.json() == {"embedding": {"values": [0.5, 0.6]}}

            batch = client.post(
                "/v1beta/models/gemini-embedding-2:batchEmbedContents",
                headers={"x-goog-api-key": acc["auth_key"]},
                json={"requests": [
                    {"model": "models/gemini-embedding-2", "content": {"parts": [{"text": "a"}]}},
                    {"model": "models/gemini-embedding-2", "content": {"parts": [{"text": "b"}]}},
                ]},
            )
            assert batch.status_code == 200
            assert batch.json() == {"embeddings": [{"values": [0.1, 0.2]}, {"values": [0.3, 0.4]}]}

    @pytest.mark.parametrize("path,expected_version", [
        ("/v1/decisions", "v1beta"),
        ("/decisions", "v1beta"),
        ("/v1beta/decisions", "v1beta"),
        ("/v1alpha/decisions", "v1alpha"),
    ])
    def test_decisions_routes_proxy_upstream(self, path, expected_version):
        client = _build_client()
        acc = _fake_account()
        mock_call_decisions = AsyncMock(return_value={
            "response": {"decision": "approve", "confidence": 0.98},
            "input_tokens": 12,
            "output_tokens": 4,
            "api_key": "key-12345678",
            "model_id": "gemini-decision-1",
            "model_alias": "gemini-decision-1",
        })
        with _auth_patches(acc), \
             patch("src.server.openai_server.routes.standard_routes.decisions._apply_account_limit", new=AsyncMock()), \
             patch("src.server.openai_server.routes.standard_routes.decisions.api_manager.call_decisions", new=mock_call_decisions), \
             patch("src.server.openai_server.routes.standard_routes.decisions.log_usage", new=AsyncMock()), \
             patch("src.server.pass_through_server.routes.gemini_handlers._check_auth", return_value=acc), \
             patch("src.server.pass_through_server.routes.gemini_handlers.account_limiter.acquire", new=AsyncMock(return_value=(True, None))), \
             patch("src.server.pass_through_server.routes.gemini_handlers.api_manager.call_decisions", new=mock_call_decisions), \
             patch("src.server.pass_through_server.routes.gemini_handlers.log_usage", new=AsyncMock()):
            resp = client.post(
                path,
                headers={"Authorization": f"Bearer {acc['auth_key']}"},
                json={"input": "should we ship?"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"decision": "approve", "confidence": 0.98}
        assert mock_call_decisions.call_args.kwargs["api_version"] == expected_version


class TestModelDiscoveryIncludesNewModels:
    def test_v1_models_and_v1beta_models(self):
        client = _build_client()
        acc = _fake_account()
        with _auth_patches(acc), \
             patch("src.server.openai_server.routes.standard_routes.models._resolve_auth", return_value=acc["auth_key"]), \
             patch("src.server.openai_server.routes.standard_routes.models._check_auth", return_value=acc), \
             patch("src.server.pass_through_server.routes.gemini_routes._check_auth", return_value=acc):
            r_openai = client.get("/v1/models", headers={"Authorization": f"Bearer {acc['auth_key']}"})
            r_gemini = client.get("/v1beta/models", headers={"x-goog-api-key": acc["auth_key"]})

        assert r_openai.status_code == 200
        by_id = {m["id"]: m for m in r_openai.json()["data"]}
        assert "gemini-4" in by_id
        assert "gemini-2" in by_id
        assert by_id["gemini-embedding-2"]["kind"] == "embedding"
        assert by_id["gemini-embedding-001"]["kind"] == "embedding"
        assert by_id["gemini-decision-1"]["kind"] == "decision"

        assert r_gemini.status_code == 200
        g_by_name = {m["name"]: m for m in r_gemini.json()["models"]}
        assert "models/gemini-4" in g_by_name
        assert "models/gemini-2" in g_by_name
        assert g_by_name["models/gemini-embedding-2"]["supportedGenerationMethods"] == ["embedContent", "batchEmbedContents"]
        assert g_by_name["models/gemini-embedding-2"]["outputTokenLimit"] == 0
        assert g_by_name["models/gemini-decision-1"]["supportedGenerationMethods"] == ["decide"]

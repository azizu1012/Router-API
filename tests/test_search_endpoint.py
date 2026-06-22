import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from src.server.openai_server.routes.app_init import app
# Make sure routes are imported so they are registered on the app
import src.server.openai_server.routes.standard_routes

client = TestClient(app)

@pytest.mark.anyio
async def test_search_endpoint_missing_auth():
    response = client.post("/v1/search", json={"query": "gold price"})
    assert response.status_code == 401
    assert "Missing API Key" in response.json()["detail"]["error"]["message"]

@pytest.mark.anyio
async def test_search_endpoint_invalid_query():
    with patch("src.server.openai_server.routes.standard_routes._check_auth") as mock_check_auth:
        mock_check_auth.return_value = {
            "name": "test-account",
            "auth_key": "some-key",
            "enabled": True,
        }
        
        # Test missing query
        response = client.post("/v1/search", json={}, headers={"Authorization": "Bearer test-key"})
        assert response.status_code == 400
        
        # Test empty query
        response = client.post("/v1/search", json={"query": "   "}, headers={"Authorization": "Bearer test-key"})
        assert response.status_code == 400

@pytest.mark.anyio
async def test_search_endpoint_success():
    with patch("src.server.openai_server.routes.standard_routes._check_auth") as mock_check_auth, \
         patch("src.core.providers.search_manager.execute_hybrid_search", new_callable=AsyncMock) as mock_search:
        
        mock_check_auth.return_value = {
            "name": "test-account",
            "auth_key": "test-key-long-enough",
            "enabled": True,
        }
        
        mock_search.return_value = ("Search results text", [{"title": "Example Title", "url": "https://example.com"}])
        
        response = client.post(
            "/v1/search",
            json={"query": "hello world", "search_engine": "duckduckgo"},
            headers={"Authorization": "Bearer test-key-long-enough"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["query"] == "hello world"
        assert data["results"] == "Search results text"
        assert len(data["citations"]) == 1
        assert data["citations"][0]["title"] == "Example Title"
        assert data["citations"][0]["url"] == "https://example.com"
        
        mock_search.assert_called_once_with(
            ["hello world"],
            search_engine="duckduckgo",
            auth_key_prefix="g-enough",
            account=mock_check_auth.return_value
        )

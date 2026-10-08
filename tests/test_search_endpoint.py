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


class TestTheErrorEnvelopeIsReachable:
    """A client reads `body.error.message`; FastAPI's `detail` hides it.

    `HTTPException(detail={"error": {...}})` puts the dict *inside* `detail`, so
    the message landed at `body.detail.error.message`. Every other route here
    returns `JSONResponse(content={"error": {...}})`, and an OpenAI-shaped client
    looks at the top level — so the reason was there and unreadable.
    """

    def _auth(self):
        return patch(
            "src.server.openai_server.routes.standard_routes._check_auth"
        )

    @pytest.mark.anyio
    async def test_a_missing_query_puts_the_message_at_the_top_level(self):
        with self._auth() as check:
            check.return_value = {"name": "t", "auth_key": "k", "enabled": True}
            r = client.post("/v1/search", json={},
                            headers={"Authorization": "Bearer test-key"})

        assert r.status_code == 400
        body = r.json()
        assert "error" in body, f"no top-level error: {body}"
        assert body["error"]["message"]
        assert body["error"]["type"] == "invalid_request_error"
        # The old shape nested it one level deeper.
        assert "error" not in body.get("detail", {})

    @pytest.mark.anyio
    async def test_a_blank_query_puts_the_message_at_the_top_level(self):
        with self._auth() as check:
            check.return_value = {"name": "t", "auth_key": "k", "enabled": True}
            r = client.post("/v1/search", json={"query": "   "},
                            headers={"Authorization": "Bearer test-key"})

        assert r.status_code == 400
        assert r.json()["error"]["message"]

    @pytest.mark.anyio
    async def test_a_search_failure_puts_the_message_at_the_top_level(self):
        with self._auth() as check, patch(
            "src.core.providers.search_manager.execute_hybrid_search",
            new_callable=AsyncMock,
        ) as search:
            check.return_value = {"name": "t", "auth_key": "k", "enabled": True}
            search.side_effect = RuntimeError("engine down")
            r = client.post("/v1/search", json={"query": "x", "search_engine": "duckduckgo"},
                            headers={"Authorization": "Bearer test-key"})

        assert r.status_code == 500
        body = r.json()
        assert body["error"]["type"] == "api_error"
        assert "engine down" in body["error"]["message"]

    @pytest.mark.anyio
    async def test_results_without_citations_are_logged_not_hidden(self):
        """Zero citations with real prose is legitimate; being silent about it is not."""
        with self._auth() as check, patch(
            "src.core.providers.search_manager.execute_hybrid_search",
            new_callable=AsyncMock,
        ) as search, patch(
            "src.server.openai_server.routes.standard_routes.logger_web"
        ) as log:
            check.return_value = {"name": "t", "auth_key": "k", "enabled": True}
            search.return_value = ("Grounded prose with no links.", [])
            r = client.post("/v1/search", json={"query": "x", "search_engine": "duckduckgo"},
                            headers={"Authorization": "Bearer test-key"})

        assert r.status_code == 200
        assert r.json()["results"] == "Grounded prose with no links."
        assert r.json()["citations"] == []
        warned = [c for c in log.warning.call_args_list
                  if "0 citations" in str(c)]
        assert warned, (
            "a search that returned prose with no sources was not logged, so "
            "it is indistinguishable from a search that found nothing")

    @pytest.mark.anyio
    async def test_a_search_with_citations_is_not_warned_about(self):
        with self._auth() as check, patch(
            "src.core.providers.search_manager.execute_hybrid_search",
            new_callable=AsyncMock,
        ) as search, patch(
            "src.server.openai_server.routes.standard_routes.logger_web"
        ) as log:
            check.return_value = {"name": "t", "auth_key": "k", "enabled": True}
            search.return_value = ("Prose.", [{"title": "T",
                                               "url": "https://e.com"}])
            r = client.post("/v1/search", json={"query": "x", "search_engine": "duckduckgo"},
                            headers={"Authorization": "Bearer test-key"})

        assert r.status_code == 200
        assert not [c for c in log.warning.call_args_list
                    if "0 citations" in str(c)]


class TestTheEngineDefault:
    """The default is DuckDuckGo, and a wrong engine is refused.

    `auto` used to be the default and it ran Google grounding first: ~90s
    against ~5s, fewer sources, and it spends the account's quota. So a caller
    who expressed no preference was paying, slowly, for a search they had not
    chosen. DuckDuckGo is the safe floor because it costs nothing either way.
    """

    def _post(self, body):
        with patch(
            "src.server.openai_server.routes.standard_routes._check_auth"
        ) as check:
            check.return_value = {"name": "t", "auth_key": "k",
                                  "enabled": True}
            return client.post("/v1/search", json=body,
                               headers={"Authorization": "Bearer test-key"})

    @pytest.mark.anyio
    async def test_no_engine_still_runs_a_search(self):
        r = self._post({"query": "capital of France"})

        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:160]}"

    @pytest.mark.anyio
    async def test_the_default_engine_is_not_charged(self):
        """The default must be the one that spends no quota."""
        import src.core.providers.search_manager as sm

        seen = []
        original = sm.execute_hybrid_search

        async def spy(*a, **kw):
            seen.append(kw.get("search_engine"))
            return ("text", [{"title": "T", "url": "https://e.com"}])

        sm.execute_hybrid_search = spy
        try:
            self._post({"query": "capital of France"})
        finally:
            sm.execute_hybrid_search = original

        assert seen == ["duckduckgo"], seen

    @pytest.mark.anyio
    async def test_an_unknown_engine_is_refused(self):
        r = self._post({"query": "x", "search_engine": "bing"})

        assert r.status_code == 400
        assert "duckduckgo" in r.json()["error"]["message"]

    @pytest.mark.anyio
    async def test_the_error_names_every_valid_engine(self):
        msg = self._post({"query": "x", "search_engine": "bing"}) \
            .json()["error"]["message"]

        for engine in ("duckduckgo", "google_grounding", "auto"):
            assert engine in msg, f"{engine} not offered to the client"

    @pytest.mark.anyio
    async def test_an_explicit_engine_is_honoured(self):
        import src.core.providers.search_manager as sm

        seen = []
        original = sm.execute_hybrid_search

        async def spy(*a, **kw):
            seen.append(kw.get("search_engine"))
            return ("text", [])

        sm.execute_hybrid_search = spy
        try:
            self._post({"query": "x", "search_engine": "google_grounding"})
        finally:
            sm.execute_hybrid_search = original

        assert seen == ["google_grounding"], seen


class TestOnlyTheGroundedSearchSpendsQuota:
    """A DuckDuckGo search makes no LLM call, so it must not be charged.

    The limiter call sat above the engine branch, so a plain HTTP scrape —
    which never touches a Gemini key — still consumed the account's RPM/TPM/RPD.
    That contradicts what the docs promise about this path.
    """

    def _charged(self, engine):
        import asyncio

        import src.core.limits.account_limiter as al
        from src.core.providers.search_manager import execute_hybrid_search

        charged = []

        async def fake_acquire(effective, tokens, pool, *a, **kw):
            charged.append(pool)
            return True, "ok"

        async def ddg(query):
            return ("text", [{"title": "T", "url": "https://e.com"}])

        # `al` is the limiter instance, not the module.
        original = al.acquire
        al.acquire = fake_acquire
        try:
            with patch("src.tools.duckduckgo.search_with_citations",
                       new=AsyncMock(side_effect=ddg)), \
                 patch("src.core.providers.search_manager.api_manager") as am:
                am.call_gemini = AsyncMock(return_value={
                    "response": None, "api_key": "k", "model_id": "m",
                    "input_tokens": 1, "output_tokens": 1})
                asyncio.run(execute_hybrid_search(
                    ["q"], search_engine=engine,
                    account={"name": "a", "tier": "admin", "enabled": True}))
        finally:
            al.acquire = original
        return len(charged)

    def test_duckduckgo_is_not_charged(self):
        assert self._charged("duckduckgo") == 0

    def test_google_grounding_is_charged(self):
        assert self._charged("google_grounding") == 1

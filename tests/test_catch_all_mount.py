"""The SPA's catch-all mount must not answer for API traffic.

``app.mount("", StaticFiles(...))`` matches every path, so anything the real
routes did not claim went to StaticFiles. Starlette returns 405 for a partial
method match and only considers the trailing-slash redirect afterwards — the
mount is a full match, so that branch was never reached:

    POST /v1/messages/   ->  405 Method Not Allowed
    GET  /health/        ->  404 (no redirect)

Both point at the wrong thing. ``/v1/messages`` is plainly registered as POST,
so "Method Not Allowed" sent people looking for a route that has existed all
along. The usual trigger is a base URL that already ends in ``/v1``: the client
appends ``/v1/messages`` and requests ``/v1/v1/messages``.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture()
def client(temp_db):
    from fastapi.testclient import TestClient
    from src.server.openai_server.routes.app_init import app

    with TestClient(app) as c:
        yield c


def _headers():
    from src.core.accounts import account_manager

    key = account_manager.create_account(name="slash1", tier="admin")["auth_key"]
    return {"x-api-key": key, "Authorization": f"Bearer {key}"}


BODY = {"model": "gemini-flash", "max_tokens": 8,
        "messages": [{"role": "user", "content": "hi"}]}


class TestATrailingSlashStillReachesTheRoute:
    def test_messages_with_a_slash_is_not_405(self, client):
        """The exact error reported: 405 on a route that exists."""
        r = client.post("/v1/messages/", headers=_headers(), json=BODY)

        assert r.status_code != 405, f"still {r.status_code}: {r.text[:160]}"
        # 429 means it reached the rate limiter, 503 means it reached the pool.
        # Either way, it reached the route.
        assert r.status_code not in (404, 405)

    def test_chat_completions_with_a_slash_is_not_405(self, client):
        r = client.post("/v1/chat/completions/", headers=_headers(), json=BODY)

        assert r.status_code not in (404, 405)

    def test_the_bare_messages_alias_too(self, client):
        r = client.post("/messages/", headers=_headers(), json=BODY)

        assert r.status_code not in (404, 405)

    def test_health_with_a_slash_redirects_or_serves(self, client):
        r = client.get("/health/", follow_redirects=False)

        assert r.status_code in (200, 307), f"got {r.status_code}"


class TestAMisconfiguredBaseUrlSaysSo:
    def test_a_doubled_v1_prefix_explains_itself(self, client):
        """A base URL ending in /v1 makes the client send /v1/v1/messages."""
        r = client.post("/v1/v1/messages", headers=_headers(), json=BODY)

        assert r.status_code == 404
        message = r.json()["error"]["message"]
        assert "ANTHROPIC_BASE_URL" in message
        assert "/v1/v1/messages" in message

    def test_it_is_a_404_not_a_405(self, client):
        """405 is what made this confusing: it names a method, not a path."""
        r = client.post("/v1/models", headers=_headers(), json=BODY)

        assert r.status_code == 404
        assert r.json()["error"]["type"] == "invalid_request_error"


class TestTheDashboardStillServesTheFrontend:
    def test_the_spa_is_not_broken_by_the_guard(self, client):
        """The mount stays; only API-shaped paths are intercepted."""
        assert client.get("/health").status_code == 200
        assert client.get("/stats").status_code == 200
"""The status a client is told must be the status that actually happened.

``_apply_account_limit`` raises ``HTTPException(429, ...)``. The route caught it
as a bare ``Exception`` and re-derived a status from ``str(e)``, which matched
none of its branches, so every rate limit left as::

    503 {"error": {"message": "Service temporarily unavailable"}}

That is worse than a wrong message. 503 tells a client the upstream is broken,
and clients retry 503 immediately and with backoff-as-if-fresh — so a pool that
is already at its limit gets hammered by the very clients it just throttled.
429 is the one status that means "you are the problem, wait".

Found while making the suite runnable in CI: with no keys registered, the
account limiter rejected every request, and the honest reason never reached the
caller.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture()
def client(temp_db):
    """The real app on conftest's throwaway database."""
    from fastapi.testclient import TestClient
    from src.server.openai_server.routes.app_init import app

    with TestClient(app) as c:
        yield c


def _account(name):
    from src.core.accounts import account_manager
    return account_manager.create_account(name=name, tier="admin")["auth_key"]


def _refuse(monkeypatch, status_code, detail):
    """Make the account limiter reject, exactly as a real limit would."""
    import src.server.openai_server.routes.completions_routes as routes
    from fastapi import HTTPException

    async def refuse(account, body, is_opencode=False):
        raise HTTPException(status_code=status_code, detail=detail)

    monkeypatch.setattr(routes, "_apply_account_limit", refuse)


def _post(client, key):
    return client.post("/v1/chat/completions", headers={"x-api-key": key},
                       json={"model": "gemini-flash",
                             "messages": [{"role": "user", "content": "hi"}]})


class TestTheStatusIsNotReinterpreted:
    def test_a_rate_limit_arrives_as_a_rate_limit(self, client, monkeypatch):
        _refuse(monkeypatch, 429, {"error": {
            "message": "Account rate limit exceeded: tokens per minute limit "
                       "exceeded for pool flash",
            "type": "rate_limit_error"}})
        key = _account("rl_status")

        r = _post(client, key)

        assert r.status_code == 429, f"got {r.status_code}: {r.text[:200]}"
        assert r.json()["error"]["type"] == "rate_limit_error"

    def test_the_limiter_message_survives(self, client, monkeypatch):
        """The reason is the useful part of the envelope."""
        _refuse(monkeypatch, 429, {"error": {
            "message": "tokens per minute limit exceeded for pool flash",
            "type": "rate_limit_error"}})
        key = _account("rl_message")

        body = _post(client, key).json()

        assert body["error"]["message"] == (
            "tokens per minute limit exceeded for pool flash")

    def test_a_plain_detail_still_gets_a_matching_type(self, client, monkeypatch):
        _refuse(monkeypatch, 401, "Token revoked")
        key = _account("rl_plain")

        r = _post(client, key)

        assert r.status_code == 401
        assert r.json()["error"]["type"] == "authentication_error"

    def test_a_permission_denial_is_not_an_api_error(self, client, monkeypatch):
        _refuse(monkeypatch, 403, "Tier not allowed")
        key = _account("rl_perm")

        r = _post(client, key)

        assert r.status_code == 403
        assert r.json()["error"]["type"] == "permission_error"


class TestTheTypeTable:
    """A status with no entry must not silently become api_error for a 4xx."""

    def test_every_4xx_we_can_raise_has_an_entry(self):
        import src.server.openai_server.routes.completions_routes as routes

        for code in (400, 401, 403, 404, 429):
            assert routes._error_type(code) != "api_error", (
                f"{code} has no error type; a client filtering on the type "
                "would treat it as a server fault and retry")

    def test_a_server_error_is_an_api_error(self):
        import src.server.openai_server.routes.completions_routes as routes

        assert routes._error_type(503) == "api_error"
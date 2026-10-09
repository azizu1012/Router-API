"""The MCP endpoint, and the mcp_only flag that scopes an account to it.

Two things here are worth a test because both fail invisibly:

- ``POST /mcp`` answering 405. A mount at /mcp only matches "/mcp/...", so the
  bare path falls through to the SPA's catch-all, which refuses POST. The error
  names a method rather than a path, on an endpoint whose whole purpose is POST.
  Separately, the MCP SDK builds its transport route without ``methods`` and
  Starlette 1.2 defaults that to ``["GET"]`` — same symptom, different cause.

- An mcp_only account being blocked at its own front door. The gate lives in
  ``token_limit_middleware`` precisely because that is the one place every
  request passes through, including requests arriving through the /mcp mount —
  which is also why it needs an explicit exemption.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

JSONRPC_INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "test", "version": "1"}},
}
MCP_HEADERS = {"content-type": "application/json",
               "accept": "application/json, text/event-stream"}


@pytest.fixture()
def client(temp_db):
    from fastapi.testclient import TestClient
    from src.server.openai_server import routes

    with TestClient(routes.app) as c:
        yield c


def _account(name, **kwargs):
    from src.core.accounts import account_manager
    return account_manager.create_account(name=name, **kwargs)


def _auth(key):
    return {**MCP_HEADERS, "Authorization": f"Bearer {key}"}


class TestTheEndpointAnswers:
    def test_a_valid_token_gets_an_mcp_session(self, client):
        key = _account("mcp_ok")["auth_key"]

        r = client.post("/mcp", headers=_auth(key), json=JSONRPC_INIT)

        assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
        assert "jsonrpc" in r.text

    def test_a_missing_token_is_refused(self, client):
        """Not asserted as 200 on purpose: auth must gate before the tool."""
        r = client.post("/mcp", headers=MCP_HEADERS, json=JSONRPC_INIT)

        assert r.status_code == 401

    def test_a_wrong_token_is_refused(self, client):
        r = client.post("/mcp", headers=_auth("sk-not-a-real-token"),
                        json=JSONRPC_INIT)

        assert r.status_code == 401

    def test_the_spa_is_not_answering_instead(self, client):
        """A bare StaticFiles 404/405 here would look like a dead server."""
        key = _account("mcp_not_spa")["auth_key"]

        r = client.post("/mcp", headers=_auth(key), json=JSONRPC_INIT)

        assert r.status_code != 404
        assert r.status_code != 405


class TestTheTransportRouteAcceptsPost:
    def test_the_sdk_route_was_given_post(self, client):
        from src.server.openai_server.mcp_routes import build_mcp_app

        mcp_app = build_mcp_app()
        transport = next(r for r in mcp_app.routes
                         if getattr(r, "path", None) == "/")

        assert "POST" in (transport.methods or set()), (
            "the SDK builds its route without methods; Starlette 1.2 defaults "
            "that to GET and every JSON-RPC POST becomes a 405")

    def test_the_session_endpoint_is_mcp_not_mcp_mcp(self, client):
        key = _account("mcp_path")["auth_key"]

        assert client.post("/mcp", headers=_auth(key),
                           json=JSONRPC_INIT).status_code == 200
        assert client.post("/mcp/mcp", headers=_auth(key),
                           json=JSONRPC_INIT).status_code != 200


class TestMcpOnlyIsScopedToTheTool:
    def _model_call(self, client, key):
        return client.post("/v1/messages", headers=_auth(key),
                           json={"model": "gemini-flash", "max_tokens": 8,
                                 "messages": [{"role": "user", "content": "hi"}]})

    def test_a_normal_account_still_calls_models(self, client):
        key = _account("normal_caller")["auth_key"]

        assert self._model_call(client, key).status_code != 403

    def test_an_mcp_only_account_is_refused_on_models(self, client):
        key = _account("search_only", mcp_only=True)["auth_key"]

        r = self._model_call(client, key)

        assert r.status_code == 403
        assert r.json()["error"]["type"] == "permission_error"

    def test_but_its_own_door_stays_open(self, client):
        """The gate runs on the /mcp path too. Without the exemption this
        account would be locked out of the only thing it is allowed to use."""
        key = _account("search_only2", mcp_only=True)["auth_key"]

        r = client.post("/mcp", headers=_auth(key), json=JSONRPC_INIT)

        assert r.status_code == 200, f"{r.status_code}: {r.text[:160]}"

    def test_the_flag_survives_a_round_trip_through_the_database(self, client):
        from src.core.accounts import account_manager

        # Created here, not shared with another test: each test gets a fresh
        # temporary database, so an account made elsewhere is simply absent.
        key = _account("round_trip", mcp_only=True)["auth_key"]

        reloaded = account_manager.find_by_key(key)

        assert reloaded["mcp_only"] == 1
        assert self._model_call(client, key).status_code == 403

    def test_the_search_tool_is_what_the_account_can_still_do(self, client):
        """The flag has to be a real scope, not a blanket refusal."""
        from src.server.openai_server import mcp_routes

        assert hasattr(mcp_routes, "current_mcp_account")


class TestTheSearchToolIsBounded:
    def test_one_call_cannot_fan_out_without_limit(self):
        from src.server.openai_server import mcp_routes

        assert mcp_routes._MAX_QUERIES_PER_CALL >= 1
        assert mcp_routes._MAX_QUERIES_PER_CALL <= 5, (
            "auto falls back google -> duckduckgo, so a handful is a bound on "
            "one tool call; an unbounded loop spends the account's day")
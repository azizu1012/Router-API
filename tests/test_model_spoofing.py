"""Tests for model spoofing, aliases, and member self-managed custom endpoints."""

import os
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(temp_db):
    """Run real app on an isolated throwaway database."""
    from src.server.openai_server.routes.app_init import app
    with TestClient(app) as c:
        yield c


def _create_user(client, name="alice", tier="free"):
    from src.core.accounts import account_manager
    from src.backend.account_keys import set_password_db, create_key_db
    from src.server.openai_server.routes.auth_session import _make_session_token

    acc = account_manager.create_account(name=name, tier=tier)
    set_password_db(acc["account_id"], "password123")
    token_row = create_key_db(acc["account_id"], name, tier=tier)
    session_token = _make_session_token(acc)
    return acc, session_token, token_row


def test_backend_alias_crud(client):
    """Verify backend model_aliases DB operations and precedence rules."""
    from src.backend import model_aliases

    # 1. Add alias
    alias_id = model_aliases.add_alias_db(
        account_id="acc1",
        alias_name="gpt-4",
        target_model="gemini-pro",
        account_key_id=None,
        label="General GPT-4",
    )
    assert alias_id

    # 2. Get by ID
    a = model_aliases.get_alias_by_id_db(alias_id)
    assert a is not None
    assert a["alias_name"] == "gpt-4"
    assert a["target_model"] == "gemini-pro"
    assert a["enabled"] is True

    # 3. Resolve account-wide
    res = model_aliases.resolve_alias_db(
        alias_name="gpt-4",
        account_id="acc1",
    )
    assert res is not None
    assert res["target_model"] == "gemini-pro"

    # 4. Add key-specific override
    k_alias_id = model_aliases.add_alias_db(
        account_id="acc1",
        alias_name="gpt-4",
        target_model="gemini-flash",
        account_key_id="key_premium",
        label="Key specific override",
    )
    assert k_alias_id

    # Resolve with key_premium -> gets gemini-flash
    res_k = model_aliases.resolve_alias_db(
        alias_name="gpt-4",
        account_id="acc1",
        account_key_id="key_premium",
    )
    assert res_k is not None
    assert res_k["target_model"] == "gemini-flash"

    # Resolve with key_other -> falls back to account-wide gemini-pro
    res_other = model_aliases.resolve_alias_db(
        alias_name="gpt-4",
        account_id="acc1",
        account_key_id="key_other",
    )
    assert res_other is not None
    assert res_other["target_model"] == "gemini-pro"

    # 5. Disable alias
    model_aliases.update_alias_db(alias_id, enabled=False)
    # Account-wide is now disabled, so resolve without key returns None
    res_disabled = model_aliases.resolve_alias_db(
        alias_name="gpt-4",
        account_id="acc1",
        account_key_id=None,
    )
    assert res_disabled is None

    # 6. Delete
    ok = model_aliases.delete_alias_db(k_alias_id)
    assert ok is True
    assert model_aliases.get_alias_by_id_db(k_alias_id) is None


def test_member_endpoint_self_management(client):
    """Members can create, list, refresh, and delete their own custom endpoints."""
    acc_a, sess_a, _ = _create_user(client, name="member_a", tier="free")
    acc_b, sess_b, _ = _create_user(client, name="member_b", tier="free")

    # Member A creates endpoint
    resp = client.post(
        "/api/me/endpoints",
        headers={"X-Dashboard-Token": sess_a},
        json={
            "name": "ep-a",
            "base_url": "https://api.example.com/v1",
            "auth_key": "sk-secret-a",
            "api_format": "openai",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "ep-a"
    assert data["account_id"] == acc_a["account_id"]

    # Member A lists endpoints -> sees ep-a
    list_a = client.get(
        "/api/me/endpoints",
        headers={"X-Dashboard-Token": sess_a},
    ).json()
    assert any(e["name"] == "ep-a" for e in list_a["endpoints"])

    # Member B lists endpoints -> does NOT see ep-a
    list_b = client.get(
        "/api/me/endpoints",
        headers={"X-Dashboard-Token": sess_b},
    ).json()
    assert not any(e["name"] == "ep-a" for e in list_b["endpoints"])

    # Member B tries to delete Member A's endpoint -> 403 Forbidden
    del_b = client.delete(
        "/api/me/endpoints/ep-a",
        headers={"X-Dashboard-Token": sess_b},
    )
    assert del_b.status_code == 403

    # Member A manually adds a model to ep-a
    toggle_resp = client.post(
        "/api/me/endpoints/ep-a/toggle-model",
        headers={"X-Dashboard-Token": sess_a},
        json={"model_id": "custom-claude-35", "enabled": True},
    )
    assert toggle_resp.status_code == 200

    # Member A deletes own endpoint
    del_a = client.delete(
        "/api/me/endpoints/ep-a",
        headers={"X-Dashboard-Token": sess_a},
    )
    assert del_a.status_code == 200


def test_member_model_alias_self_management(client):
    """Members can create, list, and delete their own model aliases."""
    acc_a, sess_a, _ = _create_user(client, name="coder_a", tier="free")
    acc_b, sess_b, _ = _create_user(client, name="coder_b", tier="free")

    # Member A gets templates (checks Sonnet=Flash and Haiku=Lite)
    templates_resp = client.get("/api/me/alias-templates")
    assert templates_resp.status_code == 200
    templates = templates_resp.json()
    flash_tmpl = next((t for t in templates if "Flash" in t["name"] and "Sonnet" in t["name"]), None)
    lite_tmpl = next((t for t in templates if "Lite" in t["name"] and "Haiku" in t["name"]), None)
    assert flash_tmpl is not None
    assert flash_tmpl["target_model"] == "gemini-flash"
    assert lite_tmpl is not None
    assert lite_tmpl["target_model"] == "gemini-flash-lite"

    # Member A creates pool alias
    resp_alias = client.post(
        "/api/me/aliases",
        headers={"X-Dashboard-Token": sess_a},
        json={
            "alias_name": "claude-3-5-sonnet-20241022",
            "target_model": "gemini-flash",
            "label": "Sonnet spoof",
        },
    )
    assert resp_alias.status_code == 200, resp_alias.text
    alias_data = resp_alias.json()
    alias_id = alias_data["alias_id"]

    # Member A lists aliases -> sees the alias
    list_a = client.get(
        "/api/me/aliases",
        headers={"X-Dashboard-Token": sess_a},
    ).json()
    assert any(a["alias_id"] == alias_id for a in list_a["aliases"])

    # Member B lists aliases -> does NOT see Member A's alias
    list_b = client.get(
        "/api/me/aliases",
        headers={"X-Dashboard-Token": sess_b},
    ).json()
    assert not any(a["alias_id"] == alias_id for a in list_b["aliases"])

    # Member B tries to delete Member A's alias -> 403 Forbidden
    del_b = client.delete(
        f"/api/me/aliases/{alias_id}",
        headers={"X-Dashboard-Token": sess_b},
    )
    assert del_b.status_code == 403

    # Member A deletes own alias -> 200 OK
    del_a = client.delete(
        f"/api/me/aliases/{alias_id}",
        headers={"X-Dashboard-Token": sess_a},
    )
    assert del_a.status_code == 200


def test_admin_aliases_routes(client):
    """Admin can list and manage all aliases across accounts."""
    admin_acc, admin_sess, _ = _create_user(client, name="admin_boss", tier="admin")
    user_acc, user_sess, _ = _create_user(client, name="regular_joe", tier="free")

    # Regular user creating alias
    client.post(
        "/api/me/aliases",
        headers={"X-Dashboard-Token": user_sess},
        json={"alias_name": "gpt-4o", "target_model": "gemini-flash"},
    )

    # Admin lists all aliases via /dashboard/admin/aliases
    resp = client.get(
        "/dashboard/admin/aliases",
        headers={"X-Dashboard-Token": admin_sess},
    )
    assert resp.status_code == 200
    aliases = resp.json()["aliases"]
    assert any(a["alias_name"] == "gpt-4o" for a in aliases)

    # Non-admin hitting admin endpoint -> 403 Forbidden
    forbidden = client.get(
        "/dashboard/admin/aliases",
        headers={"X-Dashboard-Token": user_sess},
    )
    assert forbidden.status_code == 403


def test_models_list_spoofing(client):
    """GET /v1/models reflects user aliases and hides aliased custom endpoint models."""
    from src.backend import endpoints, model_aliases

    user_acc, user_sess, token_row = _create_user(client, name="tester", tier="free")
    raw_key = f"sk-{user_acc['name']}-{token_row['token_code']}"

    # Add custom endpoint with two models
    endpoints.add_member_endpoint_db(
        name="my-ep",
        base_url="https://api.example.com/v1",
        auth_key="sk-test",
        account_id=user_acc["account_id"],
    )
    endpoints.update_endpoint_db(
        "my-ep",
        models=["claude-raw", "gpt-raw"],
        enabled_models=["claude-raw", "gpt-raw"],
    )

    # Alias ONE of them: "my-alias-model" -> "claude-raw" on "my-ep"
    model_aliases.add_alias_db(
        account_id=user_acc["account_id"],
        alias_name="my-alias-model",
        target_model="claude-raw",
        target_endpoint="my-ep",
    )

    # Fetch /v1/models using API key
    resp = client.get("/v1/models", headers={"Authorization": f"Bearer {raw_key}"})
    assert resp.status_code == 200
    model_ids = [m["id"] for m in resp.json()["data"]]

    # 1. Alias must be visible
    assert "my-alias-model" in model_ids
    # 2. Aliased model "claude-raw" must be HIDDEN
    assert "claude-raw" not in model_ids
    # 3. Un-aliased model "gpt-raw" must still be VISIBLE
    assert "gpt-raw" in model_ids


def test_chat_completion_response_spoofing(client, monkeypatch):
    """When an alias is used in /v1/chat/completions, response model is spoofed back to alias name."""
    from src.backend import model_aliases
    from src.api.opencode_proxy import opencode_proxy

    user_acc, _, token_row = _create_user(client, name="chat_tester", tier="free")
    raw_key = f"sk-{user_acc['name']}-{token_row['token_code']}"

    # Alias: "gpt-4-custom" -> "gemini-flash"
    model_aliases.add_alias_db(
        account_id=user_acc["account_id"],
        alias_name="gpt-4-custom",
        target_model="gemini-flash",
    )

    # Mock opencode_proxy.chat_completion
    async def mock_chat(body, account=None, is_opencode=False):
        assert body["model"] == "gemini-flash"
        return {
            "id": "cmpl-test",
            "model": "gemini-flash",
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }

    from src.server.openai_server.routes import completions_routes
    async def mock_limit(*args, **kwargs):
        pass
    monkeypatch.setattr(completions_routes, "_apply_account_limit", mock_limit)
    monkeypatch.setattr(opencode_proxy, "chat_completion", mock_chat)

    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "gpt-4-custom",
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Response model must be spoofed to gpt-4-custom, NOT gemini-flash!
    assert data["model"] == "gpt-4-custom"


def test_messages_response_spoofing(client, monkeypatch):
    """When an alias is used in /v1/messages, response model is spoofed back to alias name."""
    from src.backend import model_aliases
    from src.api.claude_proxy import claude_proxy
    from src.server.openai_server.routes import completions_routes

    user_acc, _, token_row = _create_user(client, name="claude_tester", tier="free")
    raw_key = f"sk-{user_acc['name']}-{token_row['token_code']}"

    # Alias: "claude-3-5-sonnet-20241022" -> "gemini-flash"
    model_aliases.add_alias_db(
        account_id=user_acc["account_id"],
        alias_name="claude-3-5-sonnet-20241022",
        target_model="gemini-flash",
    )

    async def mock_limit(*args, **kwargs):
        pass
    monkeypatch.setattr(completions_routes, "_apply_account_limit", mock_limit)

    async def mock_create(body, akp="", account=None):
        assert body["model"] == "gemini-flash"
        assert body.get("_original_model_name") == "claude-3-5-sonnet-20241022"
        return {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": body.get("_original_model_name") or body.get("model"),
            "content": [{"type": "text", "text": "Hello from Claude proxy"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    monkeypatch.setattr(claude_proxy, "create_message", mock_create)

    resp = client.post(
        "/v1/messages",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["model"] == "claude-3-5-sonnet-20241022"


def test_router_resolve_model_alias_with_account(client):
    """APIRouter.resolve_model_alias supports dynamic resolution when account is provided."""
    from src.core.router import router
    from src.backend import model_aliases

    user_acc, _, _ = _create_user(client, name="router_alias_tester", tier="free")
    model_aliases.add_alias_db(
        account_id=user_acc["account_id"],
        alias_name="my-cool-gpt",
        target_model="gemini-flash",
    )

    # Without account -> falls back to default
    assert router.resolve_model_alias("my-cool-gpt") == router.current_model
    # With account -> resolves to gemini-flash
    assert router.resolve_model_alias("my-cool-gpt", account=user_acc) == "gemini-flash"



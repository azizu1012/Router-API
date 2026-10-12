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

def _account(name):
    from src.core.accounts import account_manager
    return account_manager.create_account(name=name, tier="admin")["auth_key"]

def _refuse(monkeypatch, status_code, detail):
    import src.server.openai_server.routes.completions.legacy as routes
    import src.server.openai_server.routes.completions.anthropic as anthropic_routes
    from fastapi import HTTPException
    async def refuse(account, body, is_opencode=False):
        raise HTTPException(status_code=status_code, detail=detail)
    monkeypatch.setattr(routes, "_apply_account_limit", refuse)
    monkeypatch.setattr(anthropic_routes, "_apply_account_limit", refuse)

def test_completions_error_format(client, monkeypatch):
    _refuse(monkeypatch, 429, {"error": {"message": "Limit", "type": "rate_limit_error"}})
    key = _account("comp_limit")
    r = client.post("/v1/completions", headers={"x-api-key": key}, json={"model": "gemini-flash", "prompt": "hi"})
    assert r.status_code == 429
    # If the default FastAPI handler runs, we get {"detail": {"error": ...}}
    # An OpenAI client expects {"error": ...}
    body = r.json()
    assert "error" in body, f"Expected 'error' at top level, got {body}"

def test_anthropic_error_format(client, monkeypatch):
    _refuse(monkeypatch, 429, {"error": {"message": "Limit", "type": "rate_limit_error"}})
    key = _account("anth_limit")
    r = client.post("/v1/messages", headers={"x-api-key": key}, json={"model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 429
    body = r.json()
    assert "error" in body, f"Expected 'error' at top level, got {body}"



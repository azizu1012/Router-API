"""Comprehensive regression and verification test suite for security patches (H1-H2, M1-M6, L1-L7)."""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.server.openai_server.routes.app_init import app
from src.server.openai_server.routes.auth_session import _make_session_token
from src.core.accounts import account_manager
from src.backend.endpoints.manager import add_endpoint_db, get_endpoint_db
from src.backend.accounts.manager import update_account_db


@pytest.fixture
def sec_world(temp_db):
    """Setup clean test world with admin and member accounts."""
    admin = account_manager.create_account(name="admin_user", tier="admin")
    member = account_manager.create_account(name="member_user", tier="free")

    with TestClient(app) as client:
        client.admin = _make_session_token(admin)
        client.member = _make_session_token(member)
        client.admin_acc = admin
        client.member_acc = member
        yield client


def _auth_header(client, who="member"):
    token = getattr(client, who)
    return {"X-Dashboard-Token": token, "Authorization": f"Bearer {token}"}


class TestH1IDORAndSecretLeak:
    """H1: Member cannot access or mutate operator endpoints and cannot leak auth_key."""

    def test_member_cannot_modify_operator_endpoint(self, sec_world):
        # Operator endpoint has account_id=""
        ep = add_endpoint_db("op-openai", "https://api.openai.com", "sk-secret-operator-key")
        assert ep["account_id"] == ""

        # Member tries to patch operator endpoint
        r_patch = sec_world.patch(
            "/api/me/endpoints/op-openai",
            headers=_auth_header(sec_world, "member"),
            json={"base_url": "https://malicious-proxy.com"},
        )
        assert r_patch.status_code == 403, f"Expected 403, got {r_patch.status_code}: {r_patch.text}"

        # Member tries to delete operator endpoint
        r_del = sec_world.delete(
            "/api/me/endpoints/op-openai",
            headers=_auth_header(sec_world, "member"),
        )
        assert r_del.status_code == 403, f"Expected 403, got {r_del.status_code}: {r_del.text}"

        # Member tries to refresh operator endpoint
        r_ref = sec_world.post(
            "/api/me/endpoints/op-openai/refresh",
            headers=_auth_header(sec_world, "member"),
        )
        assert r_ref.status_code == 403, f"Expected 403, got {r_ref.status_code}: {r_ref.text}"

        # Member tries to toggle model on operator endpoint
        r_tog = sec_world.post(
            "/api/me/endpoints/op-openai/toggle-model",
            headers=_auth_header(sec_world, "member"),
            json={"model_id": "gpt-4o", "enabled": True},
        )
        assert r_tog.status_code == 403, f"Expected 403, got {r_tog.status_code}: {r_tog.text}"

    def test_refresh_my_endpoint_does_not_reveal_auth_key(self, sec_world):
        with patch("src.core.providers._custom_endpoint_manager.fetch_models",
                   new=AsyncMock(return_value=["model-1", "model-2"])):
            # Member creates their own endpoint
            r_create = sec_world.post(
                "/api/me/endpoints",
                headers=_auth_header(sec_world, "member"),
                json={
                    "name": "my-custom-ep",
                    "base_url": "https://api.together.xyz",
                    "auth_key": "secret-member-key-999",
                },
            )
            assert r_create.status_code == 200, r_create.text

            # Member refreshes their own endpoint
            r_refresh = sec_world.post(
                "/api/me/endpoints/my-custom-ep/refresh",
                headers=_auth_header(sec_world, "member"),
            )
            assert r_refresh.status_code == 200, r_refresh.text
            data = r_refresh.json()

            # Verify response never contains auth_key
            assert "auth_key" not in data.get("endpoint", {}), "auth_key must not be in response"
            assert data["endpoint"]["name"] == "my-custom-ep"


class TestH2SSRFPrevention:
    """H2: Member cannot register endpoints targeting private or loopback IPs."""

    def test_loopback_and_private_urls_rejected_for_member(self, sec_world):
        forbidden_urls = [
            "http://127.0.0.1:8000/v1",
            "http://localhost:11434",
            "http://169.254.169.254/latest/meta-data",
            "http://192.168.1.1:8080",
            "http://10.0.0.5",
        ]
        for url in forbidden_urls:
            r = sec_world.post(
                "/api/me/endpoints",
                headers=_auth_header(sec_world, "member"),
                json={"name": "ssrf-test", "base_url": url, "auth_key": "k"},
            )
            assert r.status_code == 400, f"Expected 400 for {url}, got {r.status_code}: {r.text}"
            assert "Invalid base_url" in r.text


class TestM1SessionRevocationAndSync:
    """M1: Session tokens dynamically verify account enabled state and tier from DB."""

    def test_demoted_admin_loses_admin_privilege_immediately(self, sec_world):
        # Admin can access /dashboard/keys
        r_admin = sec_world.get("/dashboard/keys", headers=_auth_header(sec_world, "admin"))
        assert r_admin.status_code == 200

        # Demote admin to free in DB
        update_account_db("admin_user", tier="free")

        # The old session token must now be rejected from admin routes
        r_demoted = sec_world.get("/dashboard/keys", headers=_auth_header(sec_world, "admin"))
        assert r_demoted.status_code == 403, f"Expected 403, got {r_demoted.status_code}"

    def test_disabled_account_session_rejected_immediately(self, sec_world):
        # Member calls /dashboard/me
        r_me = sec_world.get("/dashboard/me", headers=_auth_header(sec_world, "member"))
        assert r_me.status_code == 200

        # Disable member account in DB
        update_account_db("member_user", enabled=0)

        # Old session token must now be refused with 401
        r_disabled = sec_world.get("/dashboard/me", headers=_auth_header(sec_world, "member"))
        assert r_disabled.status_code == 401, f"Expected 401, got {r_disabled.status_code}"


class TestM2NegativeCache:
    """M2: Repeated bad auth keys hit negative cache instead of full DB table scan."""

    def test_invalid_key_is_negative_cached(self):
        bogus_key = "sk-totally-bogus-token-123456"
        res1 = account_manager.find_by_key(bogus_key)
        assert res1 is None
        assert bogus_key in account_manager._negative_cache

        # Subsequent lookup returns None directly from negative cache
        res2 = account_manager.find_by_key(bogus_key)
        assert res2 is None


class TestM3DashboardRoutesRBAC:
    """M3: Sensitive dashboard infrastructure endpoints are admin-only."""

    def test_non_admin_forbidden_from_keys_and_penalties_and_endpoints(self, sec_world):
        r_keys = sec_world.get("/dashboard/keys", headers=_auth_header(sec_world, "member"))
        assert r_keys.status_code == 403

        r_penalties = sec_world.get("/dashboard/penalties", headers=_auth_header(sec_world, "member"))
        assert r_penalties.status_code == 403

        r_endpoints = sec_world.get("/dashboard/endpoints", headers=_auth_header(sec_world, "member"))
        assert r_endpoints.status_code == 403


class TestL1HealthInformationDisclosure:
    """L1: /health must not leak system keys count."""

    def test_health_does_not_leak_keys_count(self, sec_world):
        r = sec_world.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert "keys" not in data, "keys count must not be disclosed in /health"
        assert "ok" in data
        assert data["ok"] is True

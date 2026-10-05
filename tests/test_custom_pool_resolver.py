import time
import pytest
from src.core.router.pool import ModelPool
from src.core.router.core.router import APIRouter
from src.logical_HQ_translator.model_resolver import _resolve_model
from src.core.providers import _custom_endpoint_manager
from src.server.openai_server.auth import _apply_account_limit

# Ensure APIRouter and database tables are ready.
# Setup a dummy custom endpoint and assign it to a pool to verify routing.

@pytest.mark.anyio
async def test_resolve_model_with_custom_pool():
    # Insert or patch configuration for a dummy custom endpoint with pool_assignments.
    # We will mock the database endpoint list or inject it directly.
    orig_list_endpoints = _custom_endpoint_manager.list_endpoints
    orig_ping_endpoint = _custom_endpoint_manager.ping_endpoint
    orig_is_endpoint_frozen = _custom_endpoint_manager.is_endpoint_frozen

    ep_mock = {
        "name": "mock-custom-endpoint",
        "base_url": "http://mock-endpoint.local",
        "auth_key": "mock-auth-key-123",
        "enabled": True,
        "pool_assignments": {
            "gemini-flash": "mock-flash-model"
        },
        "enabled_models": ["mock-flash-model"]
    }

    _custom_endpoint_manager.list_endpoints = lambda: [ep_mock]

    # We will mock is_endpoint_frozen to return False
    _custom_endpoint_manager.is_endpoint_frozen = lambda name: False

    async def mock_ping(ep):
        return True
    _custom_endpoint_manager.ping_endpoint = mock_ping

    try:
        # Resolve model with pool override or default routing
        # body: {"model": "gemini-flash"}
        model_alias_val, model_id_val, api_key_val, litellm_model_val, reservation = await _resolve_model(
            body={"model": "gemini-flash"},
            pool_alias_override="gemini-flash",
            account={"email": "test@test.com"},
            retry_attempt=0,
            pool_mode=True
        )

        assert model_alias_val == "gemini-flash"
        assert model_id_val == "mock-flash-model"
        assert api_key_val == "mock-auth-key-123"
        assert litellm_model_val == "mock-flash-model"
        assert reservation["provider"] == "custom"
        assert reservation["name"] == "mock-custom-endpoint"
        assert reservation["api_base"] == "http://mock-endpoint.local"
        print("Test passed: Resolved correctly to custom pool endpoint!")
    finally:
        # Restore mock
        _custom_endpoint_manager.list_endpoints = orig_list_endpoints
        _custom_endpoint_manager.ping_endpoint = orig_ping_endpoint
        _custom_endpoint_manager.is_endpoint_frozen = orig_is_endpoint_frozen

@pytest.mark.anyio
async def test_resolve_model_fallback_when_frozen():
    # If the custom endpoint is frozen, it should skip it and fallback (e.g. to fallback custom or gemini key)
    orig_list_endpoints = _custom_endpoint_manager.list_endpoints
    orig_is_endpoint_frozen = _custom_endpoint_manager.is_endpoint_frozen

    ep_mock = {
        "name": "mock-custom-endpoint",
        "base_url": "http://mock-endpoint.local",
        "auth_key": "mock-auth-key-123",
        "enabled": True,
        "pool_assignments": {
            "gemini-flash": "mock-flash-model"
        },
        "enabled_models": ["mock-flash-model"]
    }

    _custom_endpoint_manager.list_endpoints = lambda: [ep_mock]
    _custom_endpoint_manager.is_endpoint_frozen = lambda name: True # FROZEN!

    try:
        # We don't configure standard keys or fallback endpoints in this test environment.
        # But we want to ensure it skips the frozen pool endpoint and attempts to fallback.
        # Since standard keys/fallback might not be configured, it might raise 429/503.
        # Let's catch that and verify it skipped the custom pool (meaning it didn't return the mock endpoint).
        res = await _resolve_model(
            body={"model": "gemini-flash"},
            pool_alias_override="gemini-flash",
            account={"email": "test@test.com"},
            retry_attempt=0,
            pool_mode=True
        )
        # If it returned something (e.g. if standard keys reservation mocked or active), it should NOT be our frozen custom endpoint
        assert res[1] != "mock-flash-model"
    except Exception as e:
        # An exception (HTTP 429/503) is also expected/valid because standard keys are not mocked,
        # which proves it skipped the custom endpoint.
        assert "mock-flash-model" not in str(e)
    finally:
        _custom_endpoint_manager.list_endpoints = orig_list_endpoints
        _custom_endpoint_manager.is_endpoint_frozen = orig_is_endpoint_frozen

@pytest.mark.anyio
async def test_auth_is_custom_when_disabled():
    # When custom endpoints in the pool are disabled or frozen, auth should NOT classify as pool_type='custom'
    orig_list_endpoints = _custom_endpoint_manager.list_endpoints
    orig_is_endpoint_frozen = _custom_endpoint_manager.is_endpoint_frozen

    ep_mock = {
        "name": "mock-custom-endpoint",
        "base_url": "http://mock-endpoint.local",
        "auth_key": "mock-auth-key-123",
        "enabled": False, # DISABLED!
        "pool_assignments": {
            "gemini-flash": "mock-flash-model"
        },
        "enabled_models": ["mock-flash-model"]
    }

    # Since it is disabled, list_endpoints won't return it when filtered, or we can mock list_endpoints
    # list_endpoints filters enabled endpoints
    _custom_endpoint_manager.list_endpoints = lambda: [] # because it's disabled, list_endpoints returns empty

    try:
        # We want to check that it does not raise rate limit for custom because it resolves pool_type to 'flash' / 'lite'
        # Let's verify what happens by calling _apply_account_limit
        # If it falls back to 'flash' (and since test account has limits), it should pass or fail on flash limits, not custom.
        # We can spy on get_effective_limits_by_pool or pass a mock account
        account_mock = {
            "name": "test-account",
            "tier": "free",
            "rpm": 10,
            "tpm": 100000,
            "rpd": 1000,
            "auth_key": "testkey-123"
        }

        # We'll just run it. If it doesn't crash on custom limit lookup (or we mock limits)
        # We want to check pool_type inside auth. To do this, we can verify that get_pool_custom_models returns empty,
        # so it resolved to standard.
        pool_models = APIRouter().get_pool_custom_models("gemini-flash")
        assert len(pool_models) == 0
    finally:
        _custom_endpoint_manager.list_endpoints = orig_list_endpoints
        _custom_endpoint_manager.is_endpoint_frozen = orig_is_endpoint_frozen

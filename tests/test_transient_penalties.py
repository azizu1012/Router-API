import pytest
import time
from unittest.mock import AsyncMock, patch

from src.core.pool_manager import PoolManager
from src.core.limits import gemini_rate_limiter
from src.core.router import router

@pytest.mark.anyio
async def test_transient_rate_limit_adds_penalty_and_counts_stats():
    # Clear active penalties and reset transient counters
    gemini_rate_limiter._score_penalties.clear()
    gemini_rate_limiter._transient_429_count = 0

    # Ensure key is not frozen
    test_key = "AIzaSyFakeKey429_TransientTest"
    test_model = "gemini-2.5-flash"
    
    # Add dummy key to router state
    router._key_status[test_key] = {
        "enabled": 1,
        "usage": 0,
        "active_requests": 0,
        "frozen_until": 0.0,
        "consecutive_failures": 0,
        "last_success": 0.0,
        "per_model": {test_model: {"failures": 0, "frozen_until": 0.0}},
        "tier": "free",
    }

    # Mock router.reserve_key to return our test key and model
    reservation = {
        "key": test_key,
        "model_alias": "gemini-flash",
        "model_id": test_model,
        "provider": "gemini",
    }

    # Mock the acompletion function to raise a 429 error
    class MockAPIError(Exception):
        pass

    with patch("src.logical_HQ_translator.model_resolver.router.reserve_key", return_value=reservation), \
         patch("src.core.pool_manager.acompletion", new_callable=AsyncMock) as mock_acompletion:
         
        mock_acompletion.side_effect = MockAPIError("Resource has been exhausted (e.g. check quota). [429]")

        pm = PoolManager()
        
        # We call call_nonstream with standalone mode (no pool matches 'gemini-flash-standalone' if it has no pool configuration)
        # To force standalone mode, we use a non-pooled model alias, or mock resolve_pool to return None.
        with patch("src.core.router.router.resolve_pool", return_value=None):
            with pytest.raises(Exception):
                await pm.call_nonstream(
                    model_alias="gemini-flash",
                    messages=[{"role": "user", "content": "hello"}],
                )

        # 1. Verify transient 429 counter was incremented
        assert gemini_rate_limiter._transient_429_count >= 1

        # 2. Verify penalty was added to _score_penalties
        pkey = f"{test_key}::{test_model}"
        assert pkey in gemini_rate_limiter._score_penalties
        assert gemini_rate_limiter._score_penalties[pkey]["reason"] == "rate_limit"
        assert gemini_rate_limiter._score_penalties[pkey]["score_reduction"] == -86

        # 3. Verify key is frozen in router status
        assert router._key_status[test_key]["per_model"][test_model]["frozen_until"] > time.time()


@pytest.mark.anyio
async def test_dynamic_cooldowns_and_alignments():
    from src.core.config_n_logg import config
    from src.core.limits.gemini_rate_limiter import get_seconds_until_pacific_midnight, PENALTY_MAP
    
    # 1. Test Pacific midnight RPD remaining seconds calculation
    rem_sec = get_seconds_until_pacific_midnight()
    assert 300 <= rem_sec <= 86400
    
    # 2. Test adaptive cooldown returns midnight for rate_limit_rpd and project_quota_429
    rpd_cd = router._adaptive_cooldown("rate_limit_rpd", 1)
    quota_cd = router._adaptive_cooldown("project_quota_429", 1)
    assert rpd_cd == rem_sec
    assert quota_cd == rem_sec
    
    # 3. Test non-429 cooldowns scale based on KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS
    orig_unknown = config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS
    try:
        config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS = 50
        
        # Test adaptive cooldown scales
        assert router._adaptive_cooldown("unavailable", 1) == 50
        assert router._adaptive_cooldown("timeout", 1) == 50
        
        # Test PENALTY_MAP resolves dynamically and uses correct config
        assert PENALTY_MAP["unavailable"]["duration"] == 50 * 4
        assert PENALTY_MAP["server_error"]["duration"] == 50 * 3
        
        # Override config again and verify dynamic update
        config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS = 20
        assert PENALTY_MAP["unavailable"]["duration"] == 20 * 4
        assert PENALTY_MAP["server_error"]["duration"] == 20 * 3
    finally:
        config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS = orig_unknown


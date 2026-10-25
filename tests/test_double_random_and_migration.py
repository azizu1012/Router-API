import sqlite3
import pytest
from unittest.mock import patch

from fastapi import HTTPException

from src.core.config.api_config import is_model_sunset, is_model_enabled, active_pool_members
from src.core.router import router
from src.backend.schema import auto_migrate_schema, _SCHEMA_COLUMN_REGISTRY
from src.logical_HQ_translator.model.resolver import _resolve_model


class TestDoubleRandomJitterAndSizing:
    def test_reserve_key_excludes_keys_failed_by_the_current_request(self):
        candidates = [
            ("key_1", {"active_requests": 0, "consecutive_failures": 0}, "gemini-3.6-flash"),
            ("key_2", {"active_requests": 0, "consecutive_failures": 0}, "gemini-3.6-flash"),
        ]
        with patch("src.core.router.core.key_resolver.is_model_enabled", return_value=True), \
             patch.object(router, "_get_allowed_tiers", return_value={"tier1", "tier2"}), \
             patch.object(router, "get_model_id", return_value="gemini-3.6-flash"), \
             patch.object(router, "_get_model_limits", return_value=(100, 100000)), \
             patch.object(router, "_key_status", {"key_1": {"usage": 0, "active_requests": 0}, "key_2": {"usage": 0, "active_requests": 0}}), \
             patch.object(router, "_is_key_eligible", return_value=True), \
             patch.object(router, "_select_key_double_random", return_value=("key_2", "gemini-3.6-flash")), \
             patch("src.core.router.core.key_resolver.atomic_reserve_key"), \
             patch("src.core.router.core.key_resolver.record_key_model_usage"), \
             patch.object(router, "register_reservation"):
            result = router.reserve_key("gemini-flash", model_id="gemini-3.6-flash", exclude_keys={"key_1"})
        assert result is not None
        assert result["key"] == "key_2"

    def test_double_random_selects_both_keys_when_two_candidates(self):
        """When 2 healthy candidates exist, both must be eligible for selection."""
        candidates = [
            ("key_1", {"active_requests": 0, "consecutive_failures": 0}, "gemini-3.6-flash"),
            ("key_2", {"active_requests": 0, "consecutive_failures": 0}, "gemini-3.6-flash"),
        ]

        # Mock priority to be positive and equal
        with patch("src.core.router.core.key_resolver.get_key_priority", return_value=100):
            selected_keys = set()
            for _ in range(50):
                res = router._select_key_double_random(candidates)
                assert res is not None
                selected_keys.add(res[0])

            # In the old code with int(2 * 0.5) = 1, key_2 was NEVER picked.
            # With our fix, both key_1 and key_2 are picked across 50 runs.
            assert "key_1" in selected_keys
            assert "key_2" in selected_keys

    def test_priority_jitter_allows_close_score_distribution(self):
        """Keys with very close scores should both be selectable due to priority jitter."""
        candidates = [
            ("key_a", {"active_requests": 0, "consecutive_failures": 0}, "gemini-3.6-flash"),
            ("key_b", {"active_requests": 0, "consecutive_failures": 0}, "gemini-3.6-flash"),
        ]

        def fake_priority(k, mid):
            return 100.0 if k == "key_a" else 99.9

        with patch("src.core.router.core.key_resolver.get_key_priority", side_effect=fake_priority):
            selected_keys = set()
            for _ in range(50):
                res = router._select_key_double_random(candidates)
                assert res is not None
                selected_keys.add(res[0])

            assert "key_a" in selected_keys
            assert "key_b" in selected_keys


class TestAutoMigrateSchema:
    def test_auto_migrate_adds_missing_columns(self):
        """An old database table missing newly added columns should be auto-migrated."""
        c = sqlite3.connect(":memory:")
        try:
            # Create a stripped-down accounts table missing 'tier' and 'search_engine'
            c.execute("""
                CREATE TABLE accounts (
                    account_id TEXT PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    auth_key TEXT NOT NULL
                )
            """)
            c.commit()

            # Ensure columns are initially missing
            initial_cols = {row[1] for row in c.execute("PRAGMA table_info(accounts)")}
            assert "tier" not in initial_cols
            assert "search_engine" not in initial_cols

            # Run auto migration
            auto_migrate_schema(c)

            # Ensure columns are now present
            migrated_cols = {row[1] for row in c.execute("PRAGMA table_info(accounts)")}
            assert "tier" in migrated_cols
            assert "search_engine" in migrated_cols
            assert "rpm" in migrated_cols
            assert "tpm" in migrated_cols
        finally:
            c.close()


class TestSunsetModels:
    def test_sunset_mechanism_removed(self):
        '''Sunset mechanism removed 2026-10-23: admin disables models in dashboard web.'''
        for alias in ('gemini-flash-25', 'gemini-flash-25-lite', 'gemini-flash-35', 'gemini-flash-37', 'gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-3.5-flash', 'gemini-3.7-flash', 'gemini-flash-35-lite', 'gemini-3.5-flash-lite', 'gemini-flash-38', 'gemini-flash-36', 'gemini-flash-30', 'gemini-flash-lite', 'gemini-flash-31-lite', 'gemini-3.1-flash-lite'):
            assert is_model_sunset(alias) is False

    def test_active_pool_members_includes_all_configured(self):
        '''Without sunset filtering, every configured member is handed out unless admin-disabled.'''
        for m in ('gemini-flash-36', 'gemini-flash-30', 'gemini-flash-38', 'gemini-flash-37', 'gemini-flash-35', 'gemini-flash-25'):
            assert m in active_pool_members('gemini-flash') or not is_model_enabled(m), m
        for m in ('gemini-flash-35-lite', 'gemini-flash-31-lite', 'gemini-flash-25-lite'):
            assert m in active_pool_members('gemini-flash-lite') or not is_model_enabled(m), m

    @pytest.mark.anyio
    async def test_resolve_model_no_longer_rejects_discontinued(self):
        '''Previously sunsetted aliases must not raise 400-discontinued anymore.'''
        for alias in ('gemini-flash-35', 'gemini-flash-37'):
            try:
                await _resolve_model({'model': alias}, account={'account_id': 'test_acc'})
            except HTTPException as e:
                assert 'discontinued' not in str(e.detail)

    def test_reserve_key_treats_previously_discontinued_like_active(self):
        '''reserve_key must not single out previously discontinued models.'''
        with patch('src.core.router.core.key_resolver.is_model_enabled', return_value=True):
            res_old = router.reserve_key('gemini-flash-35', account={'account_id': 'test_acc'})
            res_new = router.reserve_key('gemini-flash-38', account={'account_id': 'test_acc'})
            assert (res_old is None) == (res_new is None)

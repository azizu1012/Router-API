import sqlite3
import pytest
from unittest.mock import patch

from fastapi import HTTPException

from src.core.config.api_config import is_model_sunset, active_pool_members
from src.core.router import router
from src.backend.schema import auto_migrate_schema, _SCHEMA_COLUMN_REGISTRY
from src.logical_HQ_translator.model.resolver import _resolve_model


class TestDoubleRandomJitterAndSizing:
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
    def test_sunset_models_identification(self):
        """Models 2.5, 3.5 (standard), and 3.7 must be recognized as sunsetted; 3.5-lite is preserved."""
        # Sunset aliases
        assert is_model_sunset("gemini-flash-25") is True
        assert is_model_sunset("gemini-flash-25-lite") is True
        assert is_model_sunset("gemini-flash-35") is True
        assert is_model_sunset("gemini-flash-37") is True

        # Sunset backing model IDs
        assert is_model_sunset("gemini-2.5-flash") is True
        assert is_model_sunset("gemini-3.5-flash") is True
        assert is_model_sunset("gemini-3.7-flash") is True

        # Active models must NOT be sunset (including 3.5 lite!)
        assert is_model_sunset("gemini-flash-35-lite") is False
        assert is_model_sunset("gemini-3.5-flash-lite") is False
        assert is_model_sunset("gemini-flash-38") is False
        assert is_model_sunset("gemini-flash-36") is False
        assert is_model_sunset("gemini-flash-30") is False
        assert is_model_sunset("gemini-flash-lite") is False
        assert is_model_sunset("gemini-3.1-flash-lite") is False

    def test_active_pool_members_excludes_sunset(self):
        """active_pool_members must exclude 25, 35, and 37; lite pool preserves 35-lite."""
        flash_members = active_pool_members("gemini-flash")
        assert "gemini-flash-35" not in flash_members
        assert "gemini-flash-37" not in flash_members
        assert "gemini-flash-25" not in flash_members
        assert "gemini-flash-38" in flash_members
        assert "gemini-flash-36" in flash_members
        assert "gemini-flash-30" in flash_members

        lite_members = active_pool_members("gemini-flash-lite")
        assert "gemini-flash-25-lite" not in lite_members
        assert "gemini-flash-35-lite" in lite_members
        assert "gemini-flash-lite" in lite_members

    @pytest.mark.anyio
    async def test_resolve_model_rejects_sunsetted_model(self):
        """Resolving a sunsetted model directly must raise HTTP 400."""
        for discontinued in ("gemini-flash-35", "gemini-flash-37", "gemini-flash-25"):
            with pytest.raises(HTTPException) as exc_info:
                await _resolve_model({"model": discontinued}, account={"account_id": "test_acc"})
            assert exc_info.value.status_code == 400
            assert "discontinued" in str(exc_info.value.detail)

    def test_reserve_key_rejects_sunsetted_model(self):
        """reserve_key must block reservations for sunsetted models."""
        for discontinued in ("gemini-flash-35", "gemini-flash-37", "gemini-flash-25"):
            res = router.reserve_key(discontinued, account={"account_id": "test_acc"})
            assert res is None


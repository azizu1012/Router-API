"""Single-source registry tests: model names live in exactly one place.

src/core/config/model_registry.py MODEL_SPECS is the only definition of
model aliases, pool membership and default prices. Everything else
(AVAILABLE_MODELS, MODEL_POOLS, BACKING_TO_ALIAS, DEFAULT_MODEL_PRICES,
search fallback chain) is derived. These tests pin that contract so a
future hardcoded model list fails loudly instead of silently forking.
"""
from src.backend.models.prices import DEFAULT_MODEL_PRICES
from src.core.config import api_config
from src.core.config.model_registry import (
    MODEL_SPECS,
    build_available_models,
    build_backing_to_alias,
    build_model_pools,
    price_rows,
)


def _specs_by_alias():
    return {s["alias"]: s for s in MODEL_SPECS}


class TestModelRegistrySingleSource:
    def test_spec_aliases_unique(self):
        aliases = [s["alias"] for s in MODEL_SPECS]
        assert len(aliases) == len(set(aliases))

    def test_pools_derived_from_specs(self):
        assert api_config.MODEL_POOLS == build_model_pools()

    def test_available_models_derived_from_specs(self):
        rebuilt = build_available_models(
            {"gemini": api_config.MODEL_CONTEXT_LENGTH, "gemma": api_config.GEMMA_CONTEXT_LENGTH}
        )
        assert set(rebuilt) == set(api_config.AVAILABLE_MODELS)
        for alias, cfg in rebuilt.items():
            if alias in api_config.AVAILABLE_MODELS:
                assert cfg["model_id"] == api_config.AVAILABLE_MODELS[alias]["model_id"]

    def test_every_member_has_pool_spec_and_exists(self):
        by_alias = _specs_by_alias()
        pools = {s["alias"] for s in MODEL_SPECS if s["role"] == "pool"}
        for pool_name, pool_cfg in api_config.MODEL_POOLS.items():
            assert pool_name in pools
            seen_index = set()
            for member in pool_cfg["members"]:
                assert member in by_alias, member
                assert by_alias[member].get("pool") == pool_name, member
                assert member in api_config.AVAILABLE_MODELS, member
                idx = by_alias[member].get("pool_index", 0)
                assert idx not in seen_index, member
                seen_index.add(idx)

    def test_backing_map_prefers_member_over_pool_row(self):
        assert api_config.BACKING_TO_ALIAS["gemini-3.1-flash-lite"] == "gemini-flash-31-lite"
        assert "gemini-flash-pool" not in api_config.BACKING_TO_ALIAS.values()
        assert api_config.BACKING_TO_ALIAS == build_backing_to_alias(api_config.AVAILABLE_MODELS)

    def test_get_model_meta_single_lookup(self):
        assert api_config.get_model_meta("gemini-flash-38") is api_config.AVAILABLE_MODELS["gemini-flash-38"]
        assert api_config.get_model_meta("  GEMINI-FLASH-38 ") is api_config.AVAILABLE_MODELS["gemini-flash-38"]
        assert api_config.get_model_meta("no-such-model") == {}
        assert api_config.get_model_meta("") == {}

    def test_pool_name_for_tier(self):
        assert api_config.pool_name_for_tier("lite") == "gemini-flash-lite"
        assert api_config.pool_name_for_tier("flash") == "gemini-flash"
        assert api_config.pool_name_for_tier("unknown-tier") == "gemini-flash"

    def test_search_chain_covers_lite_pool(self):
        chain = api_config.search_fallback_chain()
        assert chain[0] == "gemini-flash-lite"
        assert chain[-1] == "gemini-flash"
        for member in api_config.MODEL_POOLS["gemini-flash-lite"]["members"]:
            assert member in chain

    def test_price_rows_cover_every_priced_spec(self):
        rows = price_rows()
        by_name = {}
        for name, pin, pout, resp in rows:
            assert name not in by_name, name
            by_name[name] = (pin, pout, resp)
        for spec in MODEL_SPECS:
            if spec.get("price_in") is None:
                continue
            if spec["role"] == "price_only":
                assert spec["alias"] in by_name
            elif spec["role"] == "pool":
                if spec.get("price_alias_row"):
                    assert spec["alias"] in by_name
            else:
                assert spec["model"] in by_name
                assert spec["alias"] in by_name

    def test_default_prices_come_from_registry(self):
        assert list(DEFAULT_MODEL_PRICES) == price_rows()

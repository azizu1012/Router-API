import os
from typing import Any, Dict, List
from src.core.config.model_registry import (
    build_available_models,
    build_backing_to_alias,
    build_model_pools,
    default_search_chain as _default_search_chain,
    pool_for_tier as _pool_for_tier,
)


# Deprecated: sunset mechanism removed 2026-10-23. Admin disables models
# in dashboard web. Kept so existing imports keep working.
def is_sunset_25() -> bool:
    return False


MODEL_CONTEXT_LENGTH: int = int(os.getenv("MODEL_CONTEXT_LENGTH", "185000"))
GEMMA_CONTEXT_LENGTH: int = int(os.getenv("GEMMA_CONTEXT_LENGTH", "14000"))

AVAILABLE_MODELS: Dict[str, Dict[str, Any]] = build_available_models({"gemini": MODEL_CONTEXT_LENGTH, "gemma": GEMMA_CONTEXT_LENGTH})
MODEL_PRIORITY: List[str] = ["gemini-flash", "gemini-flash-lite", "gemma-4-26b", "gemma-4-31b"]

# Reverse map: backing model_id → virtual pool alias
BACKING_TO_ALIAS: Dict[str, str] = build_backing_to_alias(AVAILABLE_MODELS)

def resolve_model_alias(model_id: str) -> str:
    """Map backing model ID → virtual pool alias. Falls back to original if unknown."""
    return BACKING_TO_ALIAS.get(model_id, model_id)


def get_model_meta(alias: str) -> Dict[str, Any]:
    '''Single lookup for a model alias full metadata (display, backing model_id, limits).'''
    return AVAILABLE_MODELS.get(str(alias or "").strip().lower(), {})


def pool_name_for_tier(tier: str) -> str:
    '''Pool alias serving a tier (lite or flash). Unknown tiers fall back to flash.'''
    return _pool_for_tier(MODEL_POOLS, str(tier or "").strip().lower())


def search_fallback_chain() -> List[str]:
    '''Ordered aliases for cheap fallback calls (search query extraction).'''
    return _default_search_chain(MODEL_POOLS)

MODEL_POOLS: Dict[str, Dict[str, Any]] = build_model_pools()


def is_model_sunset(alias_or_model_id: str) -> bool:
    '''Sunset mechanism removed 2026-10-23.
    Admin disables models in dashboard web. Always returns False.
    Kept for backward-compatible imports.'''
    return False


def is_model_enabled(alias: str) -> bool:
    """A model the admin has not switched off on the dashboard.

    Gemini bắt buộc paid tier cho một số model (3.7 / 3.8), nên key free gọi vào
    đó chỉ nhận về lỗi. Admin tắt nó ở UI để pool xoay vòng qua model khác cho
    tới khi Google mở cho tất cả.
    """
    return bool(AVAILABLE_MODELS.get(str(alias), {}).get("enabled", True))


def active_pool_members(pool_name: str) -> List[str]:
    """Members a pool may actually hand out, honouring the admin switch.

    Every read of ``MODEL_POOLS[...]["members"]`` that feeds routing has to go
    through here. Reading the raw list is what let a disabled member keep
    serving traffic: the pool object was already built from the full list, and
    ``get_or_create`` cached it by name, so a runtime toggle changed nothing.
    """
    cfg = MODEL_POOLS.get(pool_name)
    if not cfg:
        return []
    members = []
    for m in cfg.get("members", []):
        m_str = str(m)
        if not is_model_enabled(m_str):
            continue
        members.append(m_str)
    return members


def _recompute_pool_aggregates() -> None:
    for pool_name, pool_cfg in MODEL_POOLS.items():
        total_rpm = 0
        total_tpm = 0
        total_rpd = 0
        for m in active_pool_members(pool_name):
            m_str = str(m)
            if m_str == pool_name and m_str in MODEL_POOLS:
                # Member name collides with pool name — AVAILABLE_MODELS[m]
                # may have been overwritten with pool aggregate or stale DB value.
                # Read individual per-key limit from env var directly.
                prefix = m_str.upper().replace("-", "_")
                rpm_env = f"{prefix}_RPM"
                tpm_env = f"{prefix}_TPM"
                rpd_env = f"{prefix}_RPD"
                total_rpm += int(os.getenv(rpm_env, "3"))
                total_tpm += int(os.getenv(tpm_env, "250000"))
                total_rpd += int(os.getenv(rpd_env, "500"))
            else:
                cfg = AVAILABLE_MODELS.get(m_str)
                if cfg:
                    total_rpm += int(cfg.get("rpm", 0) or 0)
                    total_tpm += int(cfg.get("tpm", 0) or 0)
                    total_rpd += int(cfg.get("rpd", 0) or 0)
        if pool_name in AVAILABLE_MODELS:
            AVAILABLE_MODELS[pool_name]["rpm"] = total_rpm
            AVAILABLE_MODELS[pool_name]["tpm"] = total_tpm
            AVAILABLE_MODELS[pool_name]["rpd"] = total_rpd
            pool_env_key = pool_name.upper().replace("-", "_") + "_DISPLAY"
            env_disp = os.getenv(pool_env_key)
            if env_disp:
                AVAILABLE_MODELS[pool_name]["display"] = env_disp
            else:
                # Just the tier name. The member list used to be appended here as
                # "Gemini Flash Pool (36↔35↔30↔25)", which is the one thing a
                # client reading this string cannot use: it needs the alias, and
                # the members are already listed in the dashboard's pool panel
                # and in MODEL_POOLS. Override the label with
                # GEMINI_FLASH_DISPLAY / GEMINI_FLASH_LITE_DISPLAY if you want
                # something more specific.
                title = "Gemini Flash Lite Pool" if "lite" in pool_name else "Gemini Flash Pool"
                AVAILABLE_MODELS[pool_name]["display"] = title


def merge_db_models() -> None:
    try:
        from src.backend.models.config import load_all_model_configs
        db_models = load_all_model_configs()
    except Exception:
        db_models = {}
    for alias, db_cfg in db_models.items():
        if alias in AVAILABLE_MODELS:
            existing = AVAILABLE_MODELS[alias]
            existing["display"] = db_cfg.get("display") or existing.get("display", alias)
            existing["model_id"] = db_cfg.get("model_id") or existing.get("model_id", alias)
            existing["rpm"] = db_cfg.get("rpm", existing.get("rpm", 10))
            existing["tpm"] = db_cfg.get("tpm", existing.get("tpm", 1000000))
            existing["rpd"] = db_cfg.get("rpd", existing.get("rpd", 1000))
            existing["rpd_enabled"] = db_cfg.get("rpd_enabled", False)
            existing["hidden"] = db_cfg.get("hidden", existing.get("hidden", False))
            existing["priority"] = db_cfg.get("priority", existing.get("priority", 1))
            existing["context_length"] = db_cfg.get("context_length", existing.get("context_length", 220000))
            # Read even when disabled: the flag itself is the thing being switched,
            # and skipping the row left the model serving traffic with env limits.
            existing["enabled"] = bool(db_cfg.get("enabled", True))
        else:
            AVAILABLE_MODELS[alias] = {
                "display": db_cfg.get("display", alias),
                "priority": db_cfg.get("priority", 1),
                "model_id": db_cfg.get("model_id", alias),
                "rpm": db_cfg.get("rpm", 10),
                "tpm": db_cfg.get("tpm", 1000000),
                "rpd": db_cfg.get("rpd", 1000),
                "rpd_enabled": db_cfg.get("rpd_enabled", False),
                "context_length": db_cfg.get("context_length", 220000),
                "hidden": db_cfg.get("hidden", False),
                "enabled": bool(db_cfg.get("enabled", True)),
            }

    _rebuild_backing_to_alias()
    _recompute_pool_aggregates()
    _rebuild_model_priority()


def _rebuild_backing_to_alias() -> None:
    BACKING_TO_ALIAS.clear()
    for _alias, _cfg in AVAILABLE_MODELS.items():
        _mid = str(_cfg.get("model_id", ""))
        if _mid and _mid not in ("gemini-flash-pool",):
            BACKING_TO_ALIAS[_mid] = _alias


def _rebuild_model_priority() -> None:
    seen = set()
    ordered = []
    for alias in MODEL_PRIORITY:
        if alias in AVAILABLE_MODELS and alias not in seen:
            ordered.append(alias)
            seen.add(alias)
    for alias in AVAILABLE_MODELS:
        if alias not in seen and not AVAILABLE_MODELS[alias].get("hidden", False):
            ordered.append(alias)
            seen.add(alias)
    MODEL_PRIORITY.clear()
    MODEL_PRIORITY.extend(ordered)


def reload_model_config() -> None:
    merge_db_models()
    from src.core.limits.gemini.limiter import clear_rate_limiters
    clear_rate_limiters()
    from src.core.router.pool import ModelPool
    ModelPool.reset_instances()


# Load DB overrides at import time
merge_db_models()


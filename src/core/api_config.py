import os
from datetime import date as _date
from typing import Any, Dict, List

SUNSET_DATE_25 = _date(2026, 10, 16)

def is_sunset_25() -> bool:
    return _date.today() >= SUNSET_DATE_25


MODEL_CONTEXT_LENGTH: int = int(os.getenv("MODEL_CONTEXT_LENGTH", "220000"))

AVAILABLE_MODELS: Dict[str, Dict[str, Any]] = {
    "gemini-flash-35": {
        "display": "Gemini Flash Latest",
        "priority": 1,
        "model_id": os.getenv("GEMINI_FLASH_35_MODEL", "gemini-3.5-flash"),
        "rpm": int(os.getenv("GEMINI_FLASH_35_RPM", "2")),
        "tpm": int(os.getenv("GEMINI_FLASH_35_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_35_RPD", "50")),
        "context_length": MODEL_CONTEXT_LENGTH,
        "hidden": True,
    },
    "gemini-flash-30": {
        "display": "Gemini Flash 3.0 Latest",
        "priority": 1,
        "model_id": os.getenv("GEMINI_FLASH_30_MODEL", "gemini-3-flash-preview"),
        "rpm": int(os.getenv("GEMINI_FLASH_30_RPM", "2")),
        "tpm": int(os.getenv("GEMINI_FLASH_30_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_30_RPD", "50")),
        "context_length": MODEL_CONTEXT_LENGTH,
        "hidden": True,
    },
    "gemini-flash": {
        "display": "Gemini Flash Pool (35↔30↔25)",
        "priority": 1,
        "model_id": "gemini-flash-pool",
        "rpm": int(os.getenv("GEMINI_FLASH_35_RPM", "2")) + int(os.getenv("GEMINI_FLASH_30_RPM", "2")) + int(os.getenv("GEMINI_FLASH_25_RPM", "5")),
        "tpm": int(os.getenv("GEMINI_FLASH_35_TPM", "250000")) + int(os.getenv("GEMINI_FLASH_30_TPM", "250000")) + int(os.getenv("GEMINI_FLASH_25_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_35_RPD", "50")) + int(os.getenv("GEMINI_FLASH_30_RPD", "50")) + int(os.getenv("GEMINI_FLASH_25_RPD", "20")),
        "context_length": MODEL_CONTEXT_LENGTH,
    },
    "gemini-flash-lite": {
        "display": "Gemini Flash Lite Pool (1.0↔2.5)",
        "priority": 2,
        "model_id": os.getenv("GEMINI_FLASH_LITE_MODEL", "gemini-3.1-flash-lite"),
        "rpm": int(os.getenv("GEMINI_FLASH_LITE_RPM", "3")),
        "tpm": int(os.getenv("GEMINI_FLASH_LITE_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_LITE_RPD", "500")),
        "context_length": MODEL_CONTEXT_LENGTH,
    },
    "gemini-flash-25": {
        "display": "Gemini Flash 2.5",
        "priority": 1,
        "model_id": os.getenv("GEMINI_FLASH_25_MODEL", "gemini-2.5-flash"),
        "rpm": int(os.getenv("GEMINI_FLASH_25_RPM", "5")),
        "tpm": int(os.getenv("GEMINI_FLASH_25_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_25_RPD", "20")),
        "context_length": MODEL_CONTEXT_LENGTH,
        "hidden": True,
    },
    "gemini-flash-25-lite": {
        "display": "Gemini Flash 2.5 Lite",
        "priority": 2,
        "model_id": os.getenv("GEMINI_FLASH_25_LITE_MODEL", "gemini-2.5-flash-lite"),
        "rpm": int(os.getenv("GEMINI_FLASH_25_LITE_RPM", "3")),
        "tpm": int(os.getenv("GEMINI_FLASH_25_LITE_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_25_LITE_RPD", "20")),
        "context_length": MODEL_CONTEXT_LENGTH,
        "hidden": True,
    },
}

MODEL_PRIORITY: List[str] = ["gemini-flash", "gemini-flash-lite"]

# Reverse map: backing model_id → virtual pool alias
BACKING_TO_ALIAS: Dict[str, str] = {}
for _alias, _cfg in AVAILABLE_MODELS.items():
    _mid = str(_cfg.get("model_id", ""))
    if _mid and _mid not in ("gemini-flash-pool",):
        BACKING_TO_ALIAS[_mid] = _alias

def resolve_model_alias(model_id: str) -> str:
    """Map backing model ID → virtual pool alias. Falls back to original if unknown."""
    return BACKING_TO_ALIAS.get(model_id, model_id)

MODEL_POOLS: Dict[str, Dict[str, Any]] = {
    "gemini-flash": {
        "pool_name": "gemini-flash",
        "members": ["gemini-flash-35", "gemini-flash-30", "gemini-flash-25"],
        "swap_failures": int(os.getenv("POOL_SWAP_FAILURES", "5")),
        "max_attempts": int(os.getenv("POOL_MAX_ATTEMPTS", "15")),
    },
    "gemini-flash-lite": {
        "pool_name": "gemini-flash-lite",
        "members": ["gemini-flash-lite", "gemini-flash-25-lite"],
        "swap_failures": int(os.getenv("POOL_SWAP_FAILURES", "5")),
        "max_attempts": int(os.getenv("POOL_MAX_ATTEMPTS", "15")),
    },
}


def _recompute_pool_aggregates() -> None:
    for pool_name, pool_cfg in MODEL_POOLS.items():
        members = pool_cfg["members"]
        total_rpm = 0
        total_tpm = 0
        total_rpd = 0
        for m in members:
            if m == pool_name and m in MODEL_POOLS:
                # Member name collides with pool name — AVAILABLE_MODELS[m]
                # may have been overwritten with pool aggregate or stale DB value.
                # Read individual per-key limit from env var directly.
                rpm_env = m.upper().replace("-", "_") + "_RPM"
                tpm_env = m.upper().replace("-", "_") + "_TPM"
                rpd_env = m.upper().replace("-", "_") + "_RPD"
                total_rpm += int(os.getenv(rpm_env, "3"))
                total_tpm += int(os.getenv(tpm_env, "250000"))
                total_rpd += int(os.getenv(rpd_env, "500"))
            else:
                cfg = AVAILABLE_MODELS.get(m)
                if cfg:
                    total_rpm += int(cfg.get("rpm", 0))
                    total_tpm += int(cfg.get("tpm", 0))
                    total_rpd += int(cfg.get("rpd", 0))
        if pool_name in AVAILABLE_MODELS:
            AVAILABLE_MODELS[pool_name]["rpm"] = total_rpm
            AVAILABLE_MODELS[pool_name]["tpm"] = total_tpm
            AVAILABLE_MODELS[pool_name]["rpd"] = total_rpd


def merge_db_models() -> None:
    try:
        from src.backend.model_config import load_all_model_configs
        db_models = load_all_model_configs()
    except Exception:
        db_models = {}
    for alias, db_cfg in db_models.items():
        if not db_cfg.get("enabled", True):
            continue
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
    from src.core.limits.gemini_rate_limiter import clear_rate_limiters
    clear_rate_limiters()


# Load DB overrides at import time
merge_db_models()

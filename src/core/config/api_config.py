import os
from datetime import date as _date
from typing import Any, Dict, List

SUNSET_DATE_25 = _date(2026, 10, 16)

def is_sunset_25() -> bool:
    return _date.today() >= SUNSET_DATE_25


MODEL_CONTEXT_LENGTH: int = int(os.getenv("MODEL_CONTEXT_LENGTH", "185000"))
GEMMA_CONTEXT_LENGTH: int = int(os.getenv("GEMMA_CONTEXT_LENGTH", "14000"))

AVAILABLE_MODELS: Dict[str, Dict[str, Any]] = {
    "gemini-flash-38": {
        "display": "Gemini Flash 3.8",
        "priority": 1,
        "model_id": os.getenv("GEMINI_FLASH_38_MODEL", "gemini-3.8-flash"),
        "rpm": int(os.getenv("GEMINI_FLASH_38_RPM", "5")),
        "tpm": int(os.getenv("GEMINI_FLASH_38_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_38_RPD", "20")),
        "context_length": MODEL_CONTEXT_LENGTH,
        "physical_context": 1048576,
        "hidden": True,
    },
    # 3.7 is capacity-constrained upstream: measured ~1/3 successful calls with
    # 503 "high demand" on the rest (2026-10-05). Token budget is left at the
    # standard ceiling so a single large request is not artificially truncated;
    # request rate is what gets throttled, since bursts are what the model drops.
    # Raise GEMINI_FLASH_37_RPM once the model stabilises.
    "gemini-flash-37": {
        "display": "Gemini Flash 3.7",
        "priority": 1,
        "model_id": os.getenv("GEMINI_FLASH_37_MODEL", "gemini-3.7-flash"),
        "rpm": int(os.getenv("GEMINI_FLASH_37_RPM", "1")),
        "tpm": int(os.getenv("GEMINI_FLASH_37_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_37_RPD", "15")),
        "context_length": MODEL_CONTEXT_LENGTH,
        "hidden": True,
    },
    "gemini-flash-36": {
        "display": "Gemini Flash 3.6",
        "priority": 1,
        "model_id": os.getenv("GEMINI_FLASH_36_MODEL", "gemini-3.6-flash"),
        "rpm": int(os.getenv("GEMINI_FLASH_36_RPM", "5")),
        "tpm": int(os.getenv("GEMINI_FLASH_36_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_36_RPD", "20")),
        "context_length": MODEL_CONTEXT_LENGTH,
        "physical_context": 1048576,
        "hidden": True,
    },
    "gemini-flash-35": {
        "display": "Gemini Flash Latest",
        "priority": 1,
        "model_id": os.getenv("GEMINI_FLASH_35_MODEL", "gemini-3.5-flash"),
        "rpm": int(os.getenv("GEMINI_FLASH_35_RPM", "2")),
        "tpm": int(os.getenv("GEMINI_FLASH_35_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_35_RPD", "50")),
        "context_length": MODEL_CONTEXT_LENGTH,
        "physical_context": 1048576,
        "hidden": True,
    },
    "gemini-flash-30": {
        "display": "Gemini Flash 3.0 Latest",
        "priority": 1,
        "model_id": os.getenv("GEMINI_FLASH_30_MODEL", "gemini-3-flash-preview"),
        "rpm": int(os.getenv("GEMINI_FLASH_30_RPM", "5")),
        "tpm": int(os.getenv("GEMINI_FLASH_30_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_30_RPD", "20")),
        "context_length": MODEL_CONTEXT_LENGTH,
        "physical_context": 1048576,
        "hidden": True,
    },
    "gemini-flash": {
        "display": os.getenv("GEMINI_FLASH_DISPLAY", "Gemini Flash Pool (38↔36↔30)"),
        "priority": 1,
        "model_id": "gemini-flash-pool",
        "rpm": int(os.getenv("GEMINI_FLASH_RPM", "15")),
        "tpm": int(os.getenv("GEMINI_FLASH_TPM", "750000")),
        "rpd": int(os.getenv("GEMINI_FLASH_RPD", "60")),
        "context_length": MODEL_CONTEXT_LENGTH,
        "physical_context": 1048576,
    },
    "gemini-flash-lite": {
        "display": "Gemini Flash Lite Pool (3.5↔3.1)",
        "priority": 2,
        "model_id": os.getenv("GEMINI_FLASH_LITE_MODEL", "gemini-3.1-flash-lite"),
        "rpm": int(os.getenv("GEMINI_FLASH_LITE_RPM", "15")),
        "tpm": int(os.getenv("GEMINI_FLASH_LITE_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_LITE_RPD", "500")),
        "context_length": MODEL_CONTEXT_LENGTH,
        "physical_context": 1048576,
    },
    "gemini-flash-35-lite": {
        "display": "Gemini Flash Lite 3.5",
        "priority": 2,
        "model_id": os.getenv("GEMINI_FLASH_35_LITE_MODEL", "gemini-3.5-flash-lite"),
        "rpm": int(os.getenv("GEMINI_FLASH_35_LITE_RPM", "15")),
        "tpm": int(os.getenv("GEMINI_FLASH_35_LITE_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_35_LITE_RPD", "500")),
        "context_length": MODEL_CONTEXT_LENGTH,
        "physical_context": 1048576,
        "hidden": True,
    },
    "gemma-4-26b": {
        "display": "Gemma 4 26B (Standalone)",
        "priority": 3,
        "model_id": os.getenv("GEMMA_4_26B_MODEL", "gemma-4-26b-a4b-it"),
        "rpm": int(os.getenv("GEMMA_4_26B_RPM", "30")),
        "tpm": int(os.getenv("GEMMA_4_26B_TPM", "16000")),
        "rpd": int(os.getenv("GEMMA_4_26B_RPD", "14400")),
        "context_length": GEMMA_CONTEXT_LENGTH,
        "physical_context": 262144,
        "hidden": False,
    },
    "gemma-4-31b": {
        "display": "Gemma 4 31B (Standalone)",
        "priority": 3,
        "model_id": os.getenv("GEMMA_4_31B_MODEL", "gemma-4-31b-it"),
        "rpm": int(os.getenv("GEMMA_4_31B_RPM", "30")),
        "tpm": int(os.getenv("GEMMA_4_31B_TPM", "16000")),
        "rpd": int(os.getenv("GEMMA_4_31B_RPD", "14400")),
        "context_length": GEMMA_CONTEXT_LENGTH,
        "physical_context": 262144,
        "hidden": False,
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

MODEL_PRIORITY: List[str] = ["gemini-flash", "gemini-flash-lite", "gemma-4-26b", "gemma-4-31b"]

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
        "members": ["gemini-flash-36", "gemini-flash-30", "gemini-flash-38",
                    "gemini-flash-37", "gemini-flash-35", "gemini-flash-25"],
        "swap_failures": int(os.getenv("POOL_SWAP_FAILURES", "5")),
        "max_attempts": int(os.getenv("POOL_MAX_ATTEMPTS", "15")),
        "max_retry_seconds": int(os.getenv("POOL_MAX_RETRY_SECONDS", "300")),
        "acquire_deadline": int(os.getenv("POOL_ACQUIRE_DEADLINE", "180")),
    },
    "gemini-flash-lite": {
        "pool_name": "gemini-flash-lite",
        "members": ["gemini-flash-35-lite", "gemini-flash-lite", "gemini-flash-25-lite"],
        "swap_failures": int(os.getenv("POOL_SWAP_FAILURES", "5")),
        "max_attempts": int(os.getenv("POOL_MAX_ATTEMPTS", "15")),
        "max_retry_seconds": int(os.getenv("POOL_MAX_RETRY_SECONDS", "300")),
        "acquire_deadline": int(os.getenv("POOL_ACQUIRE_DEADLINE", "180")),
    },
}


_SUNSET_MEMBERS = (
    "gemini-flash-25",
    "gemini-flash-25-lite",
    "gemini-flash-35",
    "gemini-flash-37",
)

_SUNSET_MODEL_IDS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
)


def is_model_sunset(alias_or_model_id: str) -> bool:
    """Check if a model alias or model_id has been sunsetted / discontinued.

    Covers:
    - Gemini 2.5: gemini-flash-25, gemini-flash-25-lite (sunset since 2026-10-16)
    - Gemini 3.5 Flash (standard): gemini-flash-35 (discontinued)
    - Gemini 3.7 Flash: gemini-flash-37 (discontinued / retired from active pool)

    Note: gemini-flash-35-lite (Gemini 3.5 Flash Lite) remains active in gemini-flash-lite pool.
    """
    alias = str(alias_or_model_id).strip().lower()
    resolved_alias = BACKING_TO_ALIAS.get(alias, alias)
    if resolved_alias in ("gemini-flash-25", "gemini-flash-25-lite") or alias in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
        return is_sunset_25()
    return (
        resolved_alias in ("gemini-flash-35", "gemini-flash-37")
        or alias in _SUNSET_MODEL_IDS
    )


def is_model_enabled(alias: str) -> bool:
    """A model the admin has not switched off on the dashboard.

    Gemini bắt buộc paid tier cho một số model (3.7 / 3.8), nên key free gọi vào
    đó chỉ nhận về lỗi. Admin tắt nó ở UI để pool xoay vòng qua model khác cho
    tới khi Google mở cho tất cả.
    """
    return bool(AVAILABLE_MODELS.get(str(alias), {}).get("enabled", True))


def active_pool_members(pool_name: str) -> List[str]:
    """Members a pool may actually hand out, honouring both sunset and the admin switch.

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
        if is_model_sunset(m_str):
            continue
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


"""Single source of truth for every model alias in the router.

Concept: model names and their metadata live in exactly one place
(MODEL_SPECS below). Everything else is derived at import time:

- AVAILABLE_MODELS   alias -> display, backing model_id, rpm/tpm/rpd, ...
- MODEL_POOLS        pool alias -> member aliases, in pool order
- BACKING_TO_ALIAS   backing model_id -> alias
- DEFAULT_MODEL_PRICES rows
- search fallback chain and pool-by-tier lookup

To add, rename or retire a model, edit ONE spec entry. To change pool
membership, edit the member spec pool and pool_index fields. No other file
defines model names or pool membership. Custom endpoints are intentionally
outside this registry: they are operator-defined at runtime.
"""
import os
from typing import Any, Dict, List

VIRTUAL_POOL_MODEL_ID = "gemini-flash-pool"

RETIRED_MODEL_ALIASES = frozenset({
    "gemini-flash-37",
    "gemini-flash-35",
    "gemma-4-31b",
})

MODEL_SPECS: List[Dict[str, Any]] = [
    {
        "alias": "gemini-flash-38",
        "display": "Gemini Flash 3.8",
        "env": "GEMINI_FLASH_38",
        "model": "gemini-3.8-flash",
        "rpm": 5, "tpm": 250000, "rpd": 20,
        "ctx": "gemini", "phys": 1048576,
        "hidden": True, "priority": 1,
        "role": "member", "pool": "gemini-flash", "pool_index": 2,
        "price_in": 0.00015, "price_out": 0.0006,
    },
    {
        "alias": "gemini-flash-36",
        "display": "Gemini Flash 3.6",
        "env": "GEMINI_FLASH_36",
        "model": "gemini-3.6-flash",
        "rpm": 5, "tpm": 250000, "rpd": 20,
        "ctx": "gemini", "phys": 1048576,
        "hidden": True, "priority": 1,
        "role": "member", "pool": "gemini-flash", "pool_index": 0,
        "price_in": 0.00015, "price_out": 0.0006,
    },
    {
        "alias": "gemini-flash-30",
        "display": "Gemini Flash 3.0 Latest",
        "env": "GEMINI_FLASH_30",
        "model": "gemini-3-flash-preview",
        "rpm": 5, "tpm": 250000, "rpd": 20,
        "ctx": "gemini", "phys": 1048576,
        "hidden": True, "priority": 1,
        "role": "member", "pool": "gemini-flash", "pool_index": 1,
        "price_in": 0.00015, "price_out": 0.0006,
    },
    {
        "alias": "gemini-flash",
        "display": "Gemini 3",
        "display_env": "GEMINI_FLASH_DISPLAY",
        "env": "GEMINI_FLASH",
        "model": "gemini-flash-pool",
        "fixed_model_id": "gemini-flash-pool",
        "rpm": 15, "tpm": 750000, "rpd": 60,
        "ctx": "gemini", "phys": 1048576,
        "hidden": None, "priority": 1,
        "role": "pool", "tier": "flash",
        "price_in": 0.00015, "price_out": 0.0006,
        "price_alias_row": True, "price_response": "gemini-flash-pool",
    },
    {
        "alias": "gemini-flash-lite",
        "display": "Lite",
        "display_env": "GEMINI_FLASH_LITE_DISPLAY",
        "env": "GEMINI_FLASH_LITE",
        "model": "gemini-flash-pool",
        "fixed_model_id": "gemini-flash-pool",
        "rpm": 40, "tpm": 750000, "rpd": 1020,
        "ctx": "gemini", "phys": 1048576,
        "hidden": None, "priority": 2,
        "role": "pool", "tier": "lite",
        "price_in": None, "price_out": None,
    },
    {
        "alias": "gemini-4",
        "display": "Gemini 4",
        "display_env": "GEMINI_4_DISPLAY",
        "env": "GEMINI_4",
        "model": "gemini-flash-pool",
        "fixed_model_id": "gemini-flash-pool",
        "rpm": 5, "tpm": 250000, "rpd": 20,
        "ctx": "gemini", "phys": 1048576,
        "hidden": None, "priority": 1,
        "role": "pool", "tier": "gemini4",
        "price_in": None, "price_out": None,
    },
    {
        "alias": "gemini-2",
        "display": "Gemini 2",
        "display_env": "GEMINI_2_DISPLAY",
        "env": "GEMINI_2",
        "model": "gemini-flash-pool",
        "fixed_model_id": "gemini-flash-pool",
        "rpm": 5, "tpm": 250000, "rpd": 20,
        "ctx": "gemini", "phys": 1048576,
        "hidden": None, "priority": 2,
        "role": "pool", "tier": "gemini2",
        "price_in": None, "price_out": None,
    },
    {
        "alias": "gemini-flash-31-lite",
        "display": "Gemini Flash Lite 3.1",
        "env": "GEMINI_FLASH_LITE",
        "model": "gemini-3.1-flash-lite",
        "rpm": 15, "tpm": 250000, "rpd": 500,
        "ctx": "gemini", "phys": 1048576,
        "hidden": True, "priority": 2,
        "role": "member", "pool": "gemini-flash-lite", "pool_index": 1,
        "price_in": 0.000075, "price_out": 0.0003,
    },
    {
        "alias": "gemini-flash-35-lite",
        "display": "Gemini Flash Lite 3.5",
        "env": "GEMINI_FLASH_35_LITE",
        "model": "gemini-3.5-flash-lite",
        "rpm": 15, "tpm": 250000, "rpd": 500,
        "ctx": "gemini", "phys": 1048576,
        "hidden": True, "priority": 2,
        "role": "member", "pool": "gemini-flash-lite", "pool_index": 0,
        "price_in": 0.000075, "price_out": 0.0003,
    },
    {
        "alias": "gemma-4-26b",
        "display": "Gemma 4 26B (Standalone)",
        "env": "GEMMA_4_26B",
        "model": "gemma-4-26b-a4b-it",
        "rpm": 30, "tpm": 16000, "rpd": 14400,
        "ctx": "gemma", "phys": 262144,
        "hidden": False, "priority": 3,
        "role": "standalone", "pool": None, "pool_index": 0,
        "price_in": None, "price_out": None,
    },
    {
        "alias": "gemini-4-hydrogen",
        "display": "Gemini 4 Hydrogen",
        "env": "GEMINI_4_HYDROGEN",
        "model": "gemini-4-hydrogen",
        "rpm": 5, "tpm": 250000, "rpd": 20,
        "ctx": "gemini", "phys": 1048576,
        "hidden": True, "priority": 1,
        "role": "member", "pool": "gemini-4", "pool_index": 0,
        "price_in": None, "price_out": None,
    },
    {
        "alias": "gemini-flash-25",
        "display": "Gemini Flash 2.5",
        "env": "GEMINI_FLASH_25",
        "model": "gemini-2.5-flash",
        "rpm": 5, "tpm": 250000, "rpd": 20,
        "ctx": "gemini", "phys": 1048576,
        "hidden": True, "priority": 2,
        "role": "member", "pool": "gemini-2", "pool_index": 0,
        "price_in": 0.0001, "price_out": 0.0004,
    },
    {
        "alias": "gemini-embedding-2",
        "display": "Gemini Embedding 2",
        "env": "GEMINI_EMBEDDING_2",
        "model": "gemini-embedding-2",
        "rpm": 100, "tpm": 30000, "rpd": 1000,
        "ctx": "gemini", "phys": 8192,
        "hidden": False, "priority": 4,
        "role": "standalone", "pool": None, "pool_index": 0,
        "kind": "embedding",
        "price_in": None, "price_out": None,
    },
    {
        "alias": "gemini-embedding-001",
        "display": "Gemini Embedding 1",
        "env": "GEMINI_EMBEDDING_001",
        "model": "gemini-embedding-001",
        "rpm": 100, "tpm": 30000, "rpd": 1000,
        "ctx": "gemini", "phys": 2048,
        "hidden": False, "priority": 4,
        "role": "standalone", "pool": None, "pool_index": 0,
        "kind": "embedding",
        "price_in": None, "price_out": None,
    },
    {
        "alias": "gemini-decision-1",
        "display": "Gemini Decision 1",
        "env": "GEMINI_DECISION_1",
        "model": "gemini-decision-1",
        "rpm": 200, "tpm": 50000, "rpd": 10000,
        "ctx": "gemini", "phys": 65536,
        "hidden": False, "priority": 4,
        "role": "standalone", "pool": None, "pool_index": 0,
        "kind": "decision",
        "price_in": None, "price_out": None,
    },
    {
        "alias": "gemini-flash-25-lite",
        "display": "Gemini Flash 2.5 Lite",
        "env": "GEMINI_FLASH_25_LITE",
        "model": "gemini-2.5-flash-lite",
        "rpm": 10, "tpm": 250000, "rpd": 20,
        "ctx": "gemini", "phys": None,
        "hidden": True, "priority": 2,
        "role": "member", "pool": "gemini-flash-lite", "pool_index": 2,
        "price_in": 0.000075, "price_out": 0.0003,
    },
    {
        "alias": "gemini-2.0-flash",
        "model": "gemini-2.0-flash",
        "role": "price_only",
        "price_in": 0.0001, "price_out": 0.0004,
    },
    {
        "alias": "gemini-3.1-flash",
        "model": "gemini-3.1-flash",
        "role": "price_only",
        "price_in": 0.00015, "price_out": 0.0006,
    },
    {
        "alias": "gemini-2.5-pro",
        "model": "gemini-2.5-pro",
        "role": "price_only",
        "price_in": 0.00125, "price_out": 0.005,
    },
    {
        "alias": "gemini-3.1-pro",
        "model": "gemini-3.1-pro",
        "role": "price_only",
        "price_in": 0.00125, "price_out": 0.005,
    },
    {
        "alias": "custom-model",
        "model": "custom-model",
        "role": "price_only",
        "price_in": 0.0, "price_out": 0.0,
    },
]

POOL_TIERS: Dict[str, str] = {
    spec["alias"]: spec["tier"] for spec in MODEL_SPECS if spec["role"] == "pool"
}

POOL_DEFAULT_DISPLAYS: Dict[str, str] = {
    spec["alias"]: spec["display"] for spec in MODEL_SPECS if spec["role"] == "pool"
}


def build_available_models(context_lengths: Dict[str, int]) -> Dict[str, Dict[str, Any]]:
    models: Dict[str, Dict[str, Any]] = {}
    for spec in MODEL_SPECS:
        if spec["role"] == "price_only":
            continue
        env = spec.get("env")
        if spec["role"] == "pool":
            if spec.get("fixed_model_id"):
                model_id = spec["fixed_model_id"]
            else:
                model_id = os.getenv(env + "_MODEL", spec["model"])
            if spec.get("display_env"):
                display = os.getenv(spec["display_env"], spec["display"])
            else:
                display = spec["display"]
        else:
            model_id = os.getenv(env + "_MODEL", spec["model"])
            display = spec["display"]
        ctx_len = context_lengths[spec["ctx"]]
        if spec.get("kind") == "embedding" and spec.get("phys") is not None:
            ctx_len = min(ctx_len, int(spec["phys"]))
        entry: Dict[str, Any] = {
            "display": display,
            "priority": spec["priority"],
            "model_id": model_id,
            "rpm": int(os.getenv(env + "_RPM", spec["rpm"])),
            "tpm": int(os.getenv(env + "_TPM", spec["tpm"])),
            "rpd": int(os.getenv(env + "_RPD", spec["rpd"])),
            "context_length": ctx_len,
        }
        if spec.get("kind") is not None:
            entry["kind"] = spec["kind"]
        if spec.get("phys") is not None:
            entry["physical_context"] = spec["phys"]
        if spec.get("hidden") is not None:
            entry["hidden"] = spec["hidden"]
        models[spec["alias"]] = entry
    return models


def build_model_pools() -> Dict[str, Dict[str, Any]]:
    pools: Dict[str, Dict[str, Any]] = {}
    for spec in MODEL_SPECS:
        if spec["role"] != "pool":
            continue
        members = [
            m["alias"]
            for m in sorted(
                [s for s in MODEL_SPECS if s.get("pool") == spec["alias"]],
                key=lambda s: s.get("pool_index", 0),
            )
        ]
        pools[spec["alias"]] = {
            "pool_name": spec["alias"],
            "members": members,
            "swap_failures": int(os.getenv("POOL_SWAP_FAILURES", "5")),
            "max_attempts": int(os.getenv("POOL_MAX_ATTEMPTS", "60")),
            "max_retry_seconds": int(os.getenv("POOL_MAX_RETRY_SECONDS", "300")),
            "acquire_deadline": int(os.getenv("POOL_ACQUIRE_DEADLINE", "180")),
        }
    return pools


def build_backing_to_alias(models: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    reverse: Dict[str, str] = {}
    for alias, cfg in models.items():
        mid = str(cfg.get("model_id", ""))
        if mid and mid != VIRTUAL_POOL_MODEL_ID:
            reverse[mid] = alias
    return reverse


def pool_for_tier(pools: Dict[str, Dict[str, Any]], tier: str) -> str:
    for name, pool_tier in POOL_TIERS.items():
        if pool_tier == tier and name in pools:
            return name
    for name, pool_tier in POOL_TIERS.items():
        if pool_tier == "flash" and name in pools:
            return name
    for name in pools:
        return name
    return ""


def default_search_chain(pools: Dict[str, Dict[str, Any]]) -> List[str]:
    lite = pool_for_tier(pools, "lite")
    flash = pool_for_tier(pools, "flash")
    chain: List[str] = []
    if lite:
        chain.append(lite)
        chain.extend(pools.get(lite, {}).get("members", []))
    if flash and flash not in chain:
        chain.append(flash)
    return chain


def price_rows() -> List[Any]:
    rows: List[Any] = []
    for spec in MODEL_SPECS:
        if spec.get("price_in") is None:
            continue
        pin = spec["price_in"]
        pout = spec["price_out"]
        if spec["role"] == "price_only":
            rows.append((spec["alias"], pin, pout, spec["model"]))
        elif spec["role"] == "pool":
            if spec.get("price_alias_row"):
                rows.append((spec["alias"], pin, pout, spec.get("price_response", spec["model"])))
        else:
            rows.append((spec["model"], pin, pout, spec["model"]))
            if spec["alias"] != spec["model"]:
                rows.append((spec["alias"], pin, pout, spec["model"]))
    return rows

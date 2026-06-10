import os
from datetime import date as _date
from typing import Any, Dict, List

SUNSET_DATE_25 = _date(2026, 10, 16)

def is_sunset_25() -> bool:
    return _date.today() >= SUNSET_DATE_25


AVAILABLE_MODELS: Dict[str, Dict[str, Any]] = {
    "gemini-flash-35": {
        "display": "Gemini Flash Latest",
        "priority": 1,
        "model_id": os.getenv("GEMINI_FLASH_35_MODEL", "gemini-3.5-flash"),
        "rpm": int(os.getenv("GEMINI_FLASH_35_RPM", "2")),
        "tpm": int(os.getenv("GEMINI_FLASH_35_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_35_RPD", "50")),
        "hidden": True,
    },
    "gemini-flash-30": {
        "display": "Gemini Flash 3.0 Latest",
        "priority": 1,
        "model_id": os.getenv("GEMINI_FLASH_30_MODEL", "gemini-3-flash-preview"),
        "rpm": int(os.getenv("GEMINI_FLASH_30_RPM", "2")),
        "tpm": int(os.getenv("GEMINI_FLASH_30_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_30_RPD", "50")),
        "hidden": True,
    },
    "gemini-flash": {
        "display": "Gemini Flash Pool (35↔30↔25)",
        "priority": 1,
        "model_id": "gemini-flash-pool",
        "rpm": int(os.getenv("GEMINI_FLASH_35_RPM", "2")) + int(os.getenv("GEMINI_FLASH_30_RPM", "2")) + int(os.getenv("GEMINI_FLASH_25_RPM", "5")),
        "tpm": int(os.getenv("GEMINI_FLASH_35_TPM", "250000")) + int(os.getenv("GEMINI_FLASH_30_TPM", "250000")) + int(os.getenv("GEMINI_FLASH_25_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_35_RPD", "50")) + int(os.getenv("GEMINI_FLASH_30_RPD", "50")) + int(os.getenv("GEMINI_FLASH_25_RPD", "20")),
    },
    "gemini-flash-lite": {
        "display": "Gemini Flash Lite Pool (1.0↔2.5)",
        "priority": 2,
        "model_id": os.getenv("GEMINI_FLASH_LITE_MODEL", "gemini-3.1-flash-lite"),
        "rpm": int(os.getenv("GEMINI_FLASH_LITE_RPM", "3")) + int(os.getenv("GEMINI_FLASH_25_LITE_RPM", "3")),
        "tpm": int(os.getenv("GEMINI_FLASH_LITE_TPM", "250000")) + int(os.getenv("GEMINI_FLASH_25_LITE_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_LITE_RPD", "500")) + int(os.getenv("GEMINI_FLASH_25_LITE_RPD", "20")),
    },
    "gemini-flash-25": {
        "display": "Gemini Flash 2.5",
        "priority": 1,
        "model_id": os.getenv("GEMINI_FLASH_25_MODEL", "gemini-2.5-flash"),
        "rpm": int(os.getenv("GEMINI_FLASH_25_RPM", "5")),
        "tpm": int(os.getenv("GEMINI_FLASH_25_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_25_RPD", "20")),
        "hidden": True,
    },
    "gemini-flash-25-lite": {
        "display": "Gemini Flash 2.5 Lite",
        "priority": 2,
        "model_id": os.getenv("GEMINI_FLASH_25_LITE_MODEL", "gemini-2.5-flash-lite"),
        "rpm": int(os.getenv("GEMINI_FLASH_25_LITE_RPM", "3")),
        "tpm": int(os.getenv("GEMINI_FLASH_25_LITE_TPM", "250000")),
        "rpd": int(os.getenv("GEMINI_FLASH_25_LITE_RPD", "20")),
        "hidden": True,
    },
}

MODEL_PRIORITY: List[str] = ["gemini-flash", "gemini-flash-lite"]

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

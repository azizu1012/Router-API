import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from .logger import logger_system

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ENV_PATH = _PROJECT_ROOT / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=True)


def _get_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int, min_v=None, max_v=None) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    if min_v is not None:
        value = max(min_v, value)
    if max_v is not None:
        value = min(max_v, value)
    return value


def _load_gemini_keys() -> List[str]:
    seen: set[str] = set()
    keys: List[str] = []
    env_names = sorted(
        name for name in os.environ
        if name.upper().startswith("GEMINI_API_KEY_") and "TOMTAT" not in name.upper()
    )
    for name in env_names:
        val = (os.getenv(name) or "").strip()
        if val and val not in seen:
            seen.add(val)
            keys.append(val)
    return keys


def _load_gemini_oauth_files() -> List[str]:
    return []


@dataclass
class RouterApiConfig:
    PROJECT_ROOT: Path = _PROJECT_ROOT

    # Server
    HOST: str = os.getenv("ROUTER_API_HOST", "127.0.0.1")
    PORT: int = _get_int("ROUTER_API_PORT", 58100, 1, 65535)
    RELOAD: bool = _get_bool("ROUTER_API_RELOAD", False)
    AUTH_TOKEN: str = field(default_factory=lambda: (os.getenv("ROUTER_API_AUTH_TOKEN") or "").strip())

    # Account / Auth
    ACCOUNTS_FILE: str = field(default_factory=lambda: str(
        (_PROJECT_ROOT / os.getenv("ROUTER_API_ACCOUNTS_FILE", "accounts.json")).resolve()
    ))
    DEFAULT_ACCOUNT_RPM: int = _get_int("ROUTER_API_DEFAULT_ACCOUNT_RPM", 300)
    DEFAULT_ACCOUNT_TPM: int = _get_int("ROUTER_API_DEFAULT_ACCOUNT_TPM", 6000000)
    DEFAULT_ACCOUNT_RPD: int = _get_int("ROUTER_API_DEFAULT_ACCOUNT_RPD", 20000)

    # Gemini keys
    GEMINI_API_KEYS: List[str] = field(default_factory=_load_gemini_keys)
    GEMINI_OAUTH_FILES: List[str] = field(default_factory=_load_gemini_oauth_files)

    # Model defaults
    DEFAULT_MODEL_ALIAS: str = field(default_factory=lambda: (os.getenv("ROUTER_API_DEFAULT_MODEL_ALIAS") or "gemini-flash-35").strip())
    REQUEST_TIMEOUT_SECONDS: int = _get_int("ROUTER_API_REQUEST_TIMEOUT_SEC", 120, 5, 600)
    MAX_OUTPUT_TOKENS: int = _get_int("ROUTER_API_MAX_OUTPUT_TOKENS", 8192, 128, 65536)
    MAX_RETRIES: int = _get_int("ROUTER_API_MAX_RETRIES", 5, 1, 20)

    # Compaction settings
    COMPACTION_TOKEN_THRESHOLD: int = _get_int("COMPACTION_TOKEN_THRESHOLD", 160000)
    CLAUDE_CODE_COMPACTION_THRESHOLD: int = _get_int("CLAUDE_CODE_COMPACTION_THRESHOLD", 80000)
    COMPACTION_TARGET_LIMIT: int = _get_int("COMPACTION_TARGET_LIMIT", 90000)
    CLAUDE_CODE_COMPACTION_TARGET_LIMIT: int = _get_int("CLAUDE_CODE_COMPACTION_TARGET_LIMIT", 45000)
    EMERGENCY_MAX_INPUT_TOKENS: int = _get_int("EMERGENCY_MAX_INPUT_TOKENS", 180000)
    LITE_EMERGENCY_MAX_INPUT_TOKENS: int = _get_int("LITE_EMERGENCY_MAX_INPUT_TOKENS", 180000)
    CLAUDE_CODE_EMERGENCY_MAX_INPUT_TOKENS: int = _get_int("CLAUDE_CODE_EMERGENCY_MAX_INPUT_TOKENS", 90000)
    CLAUDE_CODE_LITE_EMERGENCY_MAX_INPUT_TOKENS: int = _get_int("CLAUDE_CODE_LITE_EMERGENCY_MAX_INPUT_TOKENS", 90000)

    # Proxy / Relay
    PROXY_ENABLED: bool = _get_bool("GEMINI_PROXY_ENABLED", False)
    PROXY_RELAY_URL: str = field(default_factory=lambda: (os.getenv("GEMINI_RELAY_URL") or "").strip())
    PROXY_RELAY_SECRET: str = field(default_factory=lambda: (os.getenv("GEMINI_RELAY_SECRET") or "").strip())
    PROXY_FALLBACK_DIRECT: bool = _get_bool("GEMINI_PROXY_FALLBACK_DIRECT", True)
    PROXY_TIMEOUT_SECONDS: int = _get_int("GEMINI_RELAY_TIMEOUT", 60, 5, 600)

    # Circuit breaker
    CIRCUIT_ENABLED: bool = _get_bool("ROUTER_API_CIRCUIT_ENABLED", True)
    CIRCUIT_FAILURE_THRESHOLD: int = _get_int("ROUTER_API_CIRCUIT_FAILURE_THRESHOLD", 10, 1, 50)
    CIRCUIT_WINDOW_SECONDS: int = _get_int("ROUTER_API_CIRCUIT_WINDOW_SECONDS", 30, 2, 300)
    CIRCUIT_OPEN_SECONDS: int = _get_int("ROUTER_API_CIRCUIT_OPEN_SECONDS", 60, 5, 600)

    # Safety
    SAFETY_SETTINGS: List[dict] = field(default_factory=lambda: [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ])

    # Client rate limiting
    CLIENT_DEFAULT_RPM: int = _get_int("CLIENT_DEFAULT_RPM", 5, 1, 60)
    CLIENT_BURST_RPM: int = _get_int("CLIENT_BURST_RPM", 10, 1, 120)

    # Pool (model merge)
    POOL_SWAP_FAILURES: int = _get_int("POOL_SWAP_FAILURES", 2, 1, 10)
    POOL_MAX_ATTEMPTS: int = _get_int("POOL_MAX_ATTEMPTS", 13, 1, 30)

    # Key tier isolation
    FREE_KEY_END: int = _get_int("FREE_KEY_END", 30, 1, 200)
    PREMIUM_KEY_END: int = _get_int("PREMIUM_KEY_END", 55, 1, 300)

    # Key rotation tuning
    KEY_429_COOLDOWN_SECONDS: int = _get_int("KEY_429_COOLDOWN_SECONDS", 8, 5, 300)
    KEY_INVALID_COOLDOWN_SECONDS: int = _get_int("KEY_INVALID_COOLDOWN_SECONDS", 3600, 60, 86400)
    KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS: int = _get_int("KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS", 30, 5, 300)

    # Per-key throttle tuning (gemini_api_manager)
    GEMINI_API_GLOBAL_INTERVAL: float = _get_int("GEMINI_API_GLOBAL_INTERVAL", 3, 0, 100) / 10.0
    GEMINI_API_KEY_INTERVAL: float = _get_int("GEMINI_API_KEY_INTERVAL", 8, 1, 100) / 10.0
    GEMINI_API_MAX_CONCURRENT: int = _get_int("GEMINI_API_MAX_CONCURRENT", 0, 0, 500)
    GEMINI_GLOBAL_COOLDOWN_SEC: int = _get_int("GEMINI_GLOBAL_COOLDOWN_SEC", 5, 1, 60)
    GEMINI_PROJECT_FREEZE_SEC: int = _get_int("GEMINI_PROJECT_FREEZE_SEC", 60, 5, 600)
    GEMINI_UNAVAILABLE_DELAY_SEC: float = _get_int("GEMINI_UNAVAILABLE_DELAY_SEC", 50, 5, 300) / 10.0
    GEMINI_AUTO_GROUNDING: bool = _get_bool("GEMINI_AUTO_GROUNDING", False)

    def __post_init__(self):
        if not self.GEMINI_API_KEYS:
            print("[WARN] No Gemini API keys loaded!")


config = RouterApiConfig()
logger = logger_system
ENV_PATH = _ENV_PATH


def reload_config() -> List[str]:
    for name in list(os.environ.keys()):
        if name.upper().startswith("GEMINI_API_KEY_") and "TOMTAT" not in name.upper():
            os.environ.pop(name, None)

    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH, override=True)

    new_keys = _load_gemini_keys()
    config.GEMINI_API_KEYS.clear()
    config.GEMINI_API_KEYS.extend(new_keys)

    logger.info("Config reloaded dynamically. Loaded %d Gemini API keys.", len(new_keys))
    return new_keys


def remove_banned_key_from_env(api_key: str) -> None:
    from datetime import datetime

    if not ENV_PATH.exists():
        return

    try:
        content = ENV_PATH.read_text(encoding="utf-8")
        lines = content.splitlines()
        new_lines = []
        found = False
        var_name = "GEMINI_API_KEY_UNKNOWN"
        for line in lines:
            if "=" in line and not line.strip().startswith("#"):
                parts = line.split("=", 1)
                val = parts[1].strip()
                if val == api_key:
                    var_name = parts[0].strip()
                    if var_name.upper().startswith("GEMINI_API_KEY_"):
                        today_str = datetime.now().strftime("%Y-%m-%d")
                        line = f"# BANNED_{today_str}: {var_name}={api_key}"
                        found = True
                        logger.warning("[SelfHealing] Automatically commented out banned key %s in .env", var_name)
            new_lines.append(line)

        if found:
            ENV_PATH.write_text("\n".join(new_lines), encoding="utf-8")

            banned_file = ENV_PATH.parent / "banned-keys.txt"
            if banned_file.exists():
                banned_content = banned_file.read_text(encoding="utf-8")
                if api_key not in banned_content:
                    with open(banned_file, "a", encoding="utf-8") as f:
                        f.write(f"\n{var_name}={api_key}\n")
                    logger.warning("[SelfHealing] Appended banned key %s to banned-keys.txt", var_name)
    except Exception as e:
        logger.error("[SelfHealing] Error removing banned key from .env: %s", e)

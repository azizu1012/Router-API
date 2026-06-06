from typing import Any, Dict

from src.core.config_n_logg import config
from src.core.accounts import account_manager
from src.core.router import router


def run_preflight() -> Dict[str, Any]:
    models = router.list_models()
    return {
        "ok": bool(config.GEMINI_API_KEYS and models),
        "checks": {
            "gemini_keys_loaded": len(config.GEMINI_API_KEYS),
            "models_loaded": [m["id"] for m in models],
            "default_model_alias": router.current_model,
            "auth_enabled": bool(config.AUTH_TOKEN),
            "account_auth_enabled": account_manager.has_accounts(),
            "accounts_file": config.ACCOUNTS_FILE,
            "relay_enabled": bool(config.PROXY_ENABLED and config.PROXY_RELAY_URL),
        },
    }

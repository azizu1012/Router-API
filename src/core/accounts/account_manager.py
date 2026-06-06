import secrets
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.config_n_logg import config
from src.backend.accounts import (
    find_account_by_key as _find_by_key,
    find_account_by_name as _find_by_name,
    list_accounts_db as _list_accounts,
    create_account_db as _create_account,
    update_account_db as _update_account,
    delete_account_db as _delete_account,
)
from src.backend.key_status import get_key_usage_db, update_key_usage_batch_db


class AccountManager:
    def __init__(self, accounts_file: str):
        self._lock = threading.RLock()
        Path(accounts_file).parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def generate_key() -> str:
        return "sk-" + secrets.token_urlsafe(32)

    def list_accounts(self, include_disabled: bool = True) -> List[Dict[str, Any]]:
        return _list_accounts(include_disabled)

    def find_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        return _find_by_name(name)

    def find_by_key(self, auth_key: str) -> Optional[Dict[str, Any]]:
        return _find_by_key(auth_key)

    def create_account(
        self,
        name: str,
        rpm: Optional[int] = None,
        tpm: Optional[int] = None,
        rpd: Optional[int] = None,
        tier: str = "free",
    ) -> Dict[str, Any]:
        return _create_account(name, rpm, tpm, rpd, tier=tier)

    def set_tier(self, name: str, tier: str) -> Dict[str, Any]:
        return _update_account(name, tier=tier)

    def update_account(self, name: str, **updates: Any) -> Dict[str, Any]:
        return _update_account(name, **updates)

    def delete_account(self, name: str) -> Dict[str, Any]:
        return _delete_account(name)

    def rotate_key(self, name: str) -> Dict[str, Any]:
        return _update_account(name, auth_key=self.generate_key())

    def has_accounts(self) -> bool:
        return bool(_list_accounts(include_disabled=True))

    def get_gemini_key_usage(self) -> Dict[str, Any]:
        return get_key_usage_db()

    def set_gemini_key_usage(self, usage: Dict[str, Any]) -> None:
        update_key_usage_batch_db(usage)


account_manager = AccountManager(config.ACCOUNTS_FILE)

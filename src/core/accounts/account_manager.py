import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.config_n_logg import config
from src.backend.accounts import (
    find_account_by_key as _find_by_key,
    find_account_by_name,
    list_accounts_db as _list_accounts,
    create_account_db as _create_account,
    update_account_db as _update_account,
    delete_account_db as _delete_account,
)
from src.backend.account_keys import (
    all_keys_db as _all_keys_db,
    compose_token,
    create_key_db,
    delete_key_db,
    is_valid_master_key,
    list_keys_db,
    update_key_db,
    MASTER_KEY_BYTES,
)
from src.backend.key_status import get_key_usage_db, update_key_usage_batch_db


class AccountManager:
    _cache: Dict[str, Dict[str, Any]] = {}
    _cache_ts: float = 0.0
    _cache_ttl: float = 10.0
    _cache_lock = threading.RLock()

    def __init__(self, accounts_file: str):
        self._lock = threading.RLock()
        Path(accounts_file).parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def generate_key() -> str:
        return "sk-" + secrets.token_urlsafe(MASTER_KEY_BYTES)

    def _refresh_cache(self) -> None:
        """Cache both legacy whole-key accounts and sk-<name>-<code> tokens.

        The cache key is always the wire token string, so find_by_key stays a
        single dict lookup regardless of which storage form backs the account.
        """
        now = time.time()
        if now - self._cache_ts > self._cache_ttl:
            with self._cache_lock:
                if now - self._cache_ts > self._cache_ttl:
                    cache: Dict[str, Dict[str, Any]] = {}
                    for acc in _list_accounts(include_disabled=False):
                        legacy = acc.get("auth_key")
                        if legacy:
                            cache[legacy] = acc
                    for key in _all_keys_db():
                        acc = find_account_by_name(key["name"])
                        if acc and acc.get("enabled"):
                            cache[compose_token(key["name"], key["token_code"])] = {
                                **acc,
                                # token-level quota overrides the account default
                                "tier": key.get("tier") or acc.get("tier", "free"),
                                "rpm": key.get("rpm", acc.get("rpm", 0)),
                                "tpm": key.get("tpm", acc.get("tpm", 0)),
                                "rpd": key.get("rpd", acc.get("rpd", 0)),
                                "token_key_id": key["key_id"],
                                "token_code": key["token_code"],
                                "auth_key": compose_token(key["name"], key["token_code"]),
                            }
                    self._cache = cache
                    self._cache_ts = now

    def invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache_ts = 0.0

    def find_by_key(self, auth_key: str) -> Optional[Dict[str, Any]]:
        self._refresh_cache()
        raw = str(auth_key or "").strip()
        if not raw:
            return None
        acc = self._cache.get(raw)
        if acc:
            return acc
        return _find_by_key(auth_key)

    def create_account(
        self,
        name: str,
        rpm: Optional[int] = None,
        tpm: Optional[int] = None,
        rpd: Optional[int] = None,
        tier: str = "free",
        search_engine: str = "auto",
        web_search_enabled: bool = False,
    ) -> Dict[str, Any]:
        result = _create_account(name, rpm, tpm, rpd, tier=tier, search_engine=search_engine, web_search_enabled=web_search_enabled)
        self.invalidate_cache()
        return result

    def set_tier(self, name: str, tier: str) -> Dict[str, Any]:
        result = _update_account(name, tier=tier)
        self.invalidate_cache()
        return result

    def update_account(self, name: str, **updates: Any) -> Dict[str, Any]:
        result = _update_account(name, **updates)
        self.invalidate_cache()
        return result

    def delete_account(self, name: str) -> Dict[str, Any]:
        # Drop the account's tokens too, otherwise they would keep resolving to a
        # deleted account once the name is reused.
        existing = find_account_by_name(name)
        if existing:
            for key in list_keys_db(existing["account_id"]):
                delete_key_db(key["key_id"])
        result = _delete_account(name)
        self.invalidate_cache()
        return result

    # ── auth tokens (account_keys) ───────────────────────────────────────

    def list_keys(self, account_id: str, include_disabled: bool = True) -> List[Dict[str, Any]]:
        return list_keys_db(account_id, include_disabled=include_disabled)

    def create_key(self, account_id: str, **kwargs: Any) -> Dict[str, Any]:
        result = create_key_db(account_id, **kwargs)
        self.invalidate_cache()
        return result

    def update_key(self, key_id: str, **updates: Any) -> Optional[Dict[str, Any]]:
        result = update_key_db(key_id, **updates)
        self.invalidate_cache()
        return result

    def delete_key(self, key_id: str) -> Optional[Dict[str, Any]]:
        result = delete_key_db(key_id)
        self.invalidate_cache()
        return result

    def rotate_key(self, name: str) -> Dict[str, Any]:
        # The replacement must be indistinguishable from what it replaces. If
        # the current key does not match the canonical master-key shape it was
        # set by hand or by an older scheme, and swapping it would silently
        # change its format — so say so instead of quietly rewriting it.
        from src.backend.accounts import find_account_by_name as _find
        current = _find(name)
        if current and not is_valid_master_key(str(current.get("auth_key") or "")):
            from src.core.config_n_logg.logger import logger_system as _log
            _log.warning(
                "[Account] rotating %r: existing auth_key does not match the "
                "canonical master-key format, so the new key will look different",
                name,
            )
        result = _update_account(name, auth_key=self.generate_key())
        self.invalidate_cache()
        return result

    def has_accounts(self) -> bool:
        return bool(_list_accounts(include_disabled=True))

    def list_accounts(self, include_disabled: bool = True) -> List[Dict[str, Any]]:
        return _list_accounts(include_disabled=include_disabled)

    def get_gemini_key_usage(self) -> Dict[str, Any]:
        return get_key_usage_db()

    def set_gemini_key_usage(self, usage: Dict[str, Any]) -> None:
        update_key_usage_batch_db(usage)


account_manager = AccountManager(config.ACCOUNTS_FILE)

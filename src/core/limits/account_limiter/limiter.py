import asyncio
import datetime
import time
from collections import deque
from typing import Any, Dict

class AccountRateLimiter:
    CLEANUP_INTERVAL = 3600

    def __init__(self) -> None:
        self._minute_req_ts: Dict[str, deque] = {}
        self._minute_tokens: Dict[str, deque] = {}
        self._rpd_date = datetime.date.today()
        self._rpd_count: Dict[str, int] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._last_activity: Dict[str, float] = {}
        self._dict_lock = asyncio.Lock()
        self._last_cleanup = 0.0

    def _get_lock(self, account_id: str) -> asyncio.Lock:
        if account_id not in self._locks:
            self._locks[account_id] = asyncio.Lock()
        return self._locks[account_id]

    def _ensure_account_state(self, account_id: str) -> None:
        self._minute_req_ts.setdefault(account_id, deque())
        self._minute_tokens.setdefault(account_id, deque())
        self._rpd_count.setdefault(account_id, 0)

    async def _maybe_cleanup(self) -> None:
        now = time.time()
        if now - self._last_cleanup < self.CLEANUP_INTERVAL:
            return
        self._last_cleanup = now
        cutoff = now - 86400
        stale = [aid for aid, last in self._last_activity.items() if last < cutoff]
        for aid in stale:
            self._minute_req_ts.pop(aid, None)
            self._minute_tokens.pop(aid, None)
            self._rpd_count.pop(aid, None)
            self._last_activity.pop(aid, None)

    @staticmethod
    def estimate_text_tokens(text: str) -> int:
        if not text:
            return 1
        ascii_chars = sum(1 for ch in text if ord(ch) < 128)
        non_ascii_chars = len(text) - ascii_chars
        return max(1, int((ascii_chars / 3.6) + (non_ascii_chars / 1.6) + 0.999999))

    def estimate_messages_tokens(self, messages: list[dict[str, Any]], max_output_tokens: int) -> int:
        chunks = []
        for message in messages or []:
            content = message.get("content", "") if isinstance(message, dict) else ""
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        chunks.append(str(item.get("text") or ""))
        return self.estimate_text_tokens("\n".join(chunks)) + max(1, int(max_output_tokens or 0))

    async def acquire(self, account: Dict[str, Any], estimated_tokens: int, pool_type: str = "flash") -> tuple[bool, str]:
        account_id = str(account.get("account_id") or account.get("name") or "anonymous")
        pool_account_id = f"{account_id}::{pool_type}"
        rpm = int(account.get("rpm") or 0)
        tpm = int(account.get("tpm") or 0)
        rpd = int(account.get("rpd") or 0)

        async with self._dict_lock:
            account_lock = self._get_lock(pool_account_id)
            self._ensure_account_state(pool_account_id)

        async with account_lock:
            now = time.time()
            today = datetime.date.today()
            if today != self._rpd_date:
                self._rpd_date = today
                self._rpd_count.clear()
            self._ensure_account_state(pool_account_id)

            req_q = self._minute_req_ts[pool_account_id]
            tok_q = self._minute_tokens[pool_account_id]
            while req_q and now - req_q[0] >= 60:
                req_q.popleft()
            while tok_q and now - tok_q[0][0] >= 60:
                tok_q.popleft()

            if rpd > 0 and self._rpd_count[pool_account_id] >= rpd:
                return False, f"daily request limit exceeded for pool {pool_type}"
            if rpm > 0 and len(req_q) >= rpm:
                return False, f"requests per minute limit exceeded for pool {pool_type}"
            used_tokens = sum(item[1] for item in tok_q)
            if tpm > 0 and used_tokens + estimated_tokens > tpm:
                return False, f"tokens per minute limit exceeded for pool {pool_type}"

            req_q.append(now)
            tok_q.append((now, estimated_tokens))
            self._rpd_count[pool_account_id] += 1
            self._last_activity[pool_account_id] = now

        async with self._dict_lock:
            await self._maybe_cleanup()

        return True, "ok"

    async def snapshot(self, account: Dict[str, Any]) -> Dict[str, Any]:
        account_id = str(account.get("account_id") or account.get("name") or "anonymous")
        res = {}
        for pkey in ["flash", "lite", "custom"]:
            pool_aid = f"{account_id}::{pkey}"
            async with self._dict_lock:
                account_lock = self._get_lock(pool_aid)
            async with account_lock:
                now = time.time()
                self._ensure_account_state(pool_aid)
                req_q = self._minute_req_ts.get(pool_aid, deque())
                tok_q = self._minute_tokens.get(pool_aid, deque())
                rpm_used = sum(1 for ts in req_q if now - ts < 60)
                tpm_used = sum(tokens for ts, tokens in tok_q if now - ts < 60)
                rpd_used = self._rpd_count.get(pool_aid, 0) if datetime.date.today() == self._rpd_date else 0
                res[pkey] = {
                    "rpm_used": rpm_used,
                    "tpm_used": tpm_used,
                    "rpd_used": rpd_used,
                }
        return res

    async def restore_rpd_counts(self) -> None:
        import datetime
        import aiosqlite
        from src.core.usage_logger import _DB as db_path
        today_start = datetime.date.today().isoformat() + "T00:00:00"
        
        try:
            from src.backend.accounts import list_accounts_db
            accs = await asyncio.to_thread(list_accounts_db, True)
            prefix_to_id = {}
            for a in accs:
                ak = a.get("auth_key", "")
                pref = ak[-8:] if len(ak) >= 8 else ak
                if pref:
                    prefix_to_id[pref] = str(a.get("account_id") or a.get("name"))
                    
            async with self._dict_lock:
                self._rpd_date = datetime.date.today()
                self._rpd_count.clear()
                
                async with aiosqlite.connect(db_path) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT auth_key_prefix, model_alias, COUNT(*) as count FROM usage_logs WHERE timestamp >= ? GROUP BY auth_key_prefix, model_alias",
                        (today_start,)
                    ) as cursor:
                        rows = await cursor.fetchall()
                        for row in rows:
                            pref = row["auth_key_prefix"]
                            model = row["model_alias"] or ""
                            count = row["count"]
                            aid = prefix_to_id.get(pref)
                            if aid:
                                pool_type = "lite" if ("lite" in model.lower() or "flash-lite" in model.lower()) else "flash"
                                pool_aid = f"{aid}::{pool_type}"
                                self._rpd_count[pool_aid] = self._rpd_count.get(pool_aid, 0) + count
                                self._last_activity[pool_aid] = time.time()
            from src.core.config_n_logg.logger import logger_system as logger
            logger.info("[AccountLimiter] Restored RPD counts from DB for today: %s", self._rpd_count)
        except Exception as e:
            from src.core.config_n_logg.logger import logger_system as logger
            logger.error("[AccountLimiter] Failed to restore RPD counts: %s", e)

account_limiter = AccountRateLimiter()

"""Per-token rate limiting: concurrency, RPM, TPM, RPD.

Two independent layers, deliberately not conflated:

- **Concurrency** caps how many requests of one token may be *in flight* at
  once. It bounds load on the router, not on the upstream provider.
- **RPM / TPM / RPD** cap how much traffic a token may *send*. These are the
  token's own commercial allowance, entirely separate from whatever quota
  Google (or any other provider) imposes on the key pool behind the router.

``min_interval_seconds`` is kept as an optional extra spacing floor. It is only
meaningful when ``max_concurrency == 1`` — with more than one slot the interval
competes with the concurrency check rather than adding to it, so it is reported
as ignored instead of silently pretending to throttle.

State is in-memory. It describes in-flight pressure and a rolling 60s window,
both of which reset on restart, so there is no reason to write to SQLite per
request.
"""

import asyncio
import time
from collections import deque
from typing import Any, Dict, Optional

from src.backend.account_keys import DEFAULT_MAX_CONCURRENCY, DEFAULT_MIN_INTERVAL_SECONDS


class _TokenState:
    __slots__ = ("in_flight", "req_ts", "tok_events", "rpd_count", "rpd_date", "last_start")

    def __init__(self) -> None:
        self.in_flight = 0
        self.req_ts: deque = deque()      # request start timestamps, 60s window
        self.tok_events: deque = deque()  # (ts, tokens), 60s window
        self.rpd_count = 0
        self.rpd_date = ""
        self.last_start = 0.0


class TokenRateLimiter:
    def __init__(self) -> None:
        self._states: Dict[str, _TokenState] = {}
        self._lock = asyncio.Lock()

    def _state(self, key_id: str) -> _TokenState:
        st = self._states.get(key_id)
        if st is None:
            st = _TokenState()
            self._states[key_id] = st
        return st

    @staticmethod
    def _int(row: Optional[Dict[str, Any]], field: str, default: int) -> int:
        try:
            v = int((row or {}).get(field) or 0)
        except (TypeError, ValueError):
            return default
        return v if v > 0 else 0

    def limits_for(self, row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not row:
            return {
                "max_concurrency": DEFAULT_MAX_CONCURRENCY,
                "rpm": 0, "tpm": 0, "rpd": 0,
                "min_interval_seconds": DEFAULT_MIN_INTERVAL_SECONDS,
            }
        return {
            "max_concurrency": max(1, self._int(row, "max_concurrency", DEFAULT_MAX_CONCURRENCY)),
            "rpm": self._int(row, "rpm", 0),
            "tpm": self._int(row, "tpm", 0),
            "rpd": self._int(row, "rpd", 0),
            "min_interval_seconds": max(0.0, float(row.get("min_interval_seconds") or 0.0)),
        }

    async def acquire(
        self,
        row: Optional[Dict[str, Any]],
        key_id: Optional[str],
        estimated_tokens: int = 0,
    ) -> tuple:
        """Reserve capacity for one request.

        Returns (ok, reason). ``reason`` is a machine-readable slug; a caller can
        also inspect the snapshot for the exact numbers.
        """
        if not row or not key_id:
            return True, ""
        lim = self.limits_for(row)
        est = max(0, int(estimated_tokens or 0))
        today = time.strftime("%Y-%m-%d")

        async with self._lock:
            st = self._state(key_id)
            now = time.monotonic()

            if st.rpd_date != today:
                st.rpd_date = today
                st.rpd_count = 0

            while st.req_ts and now - st.req_ts[0] >= 60:
                st.req_ts.popleft()
            while st.tok_events and now - st.tok_events[0][0] >= 60:
                st.tok_events.popleft()

            # 1. concurrency — how many are in flight right now
            if st.in_flight >= lim["max_concurrency"]:
                return False, "token_concurrency_limit"

            # 2. requests per minute — this token's own allowance
            if lim["rpm"] and len(st.req_ts) >= lim["rpm"]:
                return False, "token_rpm_exceeded"

            # 3. tokens per minute
            used_tokens = sum(t for _, t in st.tok_events)
            if lim["tpm"] and used_tokens + est > lim["tpm"]:
                return False, "token_tpm_exceeded"

            # 4. daily cap
            if lim["rpd"] and st.rpd_count >= lim["rpd"]:
                return False, "token_rpd_exceeded"

            # 5. spacing floor — only meaningful with a single slot
            interval = lim["min_interval_seconds"]
            if interval > 0 and lim["max_concurrency"] == 1 and st.last_start:
                if (now - st.last_start) < interval:
                    wait = interval - (now - st.last_start)
                    return False, f"token_min_interval:{wait:.2f}"

            st.in_flight += 1
            st.last_start = now
            st.req_ts.append(now)
            st.tok_events.append((now, est))
            st.rpd_count += 1

        return True, ""

    async def release(self, row: Optional[Dict[str, Any]], key_id: Optional[str] = None,
                      actual_tokens: int = 0) -> None:
        """Release the slot, correcting the token estimate with the real usage."""
        if not row or not key_id:
            return
        async with self._lock:
            st = self._state(key_id)
            if st.in_flight > 0:
                st.in_flight -= 1
            # Backfill real usage against the most recent event we recorded.
            if actual_tokens > 0 and st.tok_events:
                ts, est = st.tok_events.pop()
                st.tok_events.append((ts, actual_tokens))

    def snapshot(self, row: Optional[Dict[str, Any]], key_id: str) -> Dict[str, Any]:
        lim = self.limits_for(row)
        st = self._states.get(key_id)
        now = time.monotonic()
        if not st:
            return {**lim, "in_flight": 0, "rpm_used": 0, "tpm_used": 0, "rpd_used": 0,
                    "interval_effective": lim["max_concurrency"] == 1}
        req_used = sum(1 for ts in st.req_ts if now - ts < 60)
        tok_used = sum(t for ts, t in st.tok_events if now - ts < 60)
        return {
            **lim,
            "in_flight": st.in_flight,
            "rpm_used": req_used,
            "tpm_used": tok_used,
            "rpd_used": st.rpd_count if st.rpd_date == time.strftime("%Y-%m-%d") else 0,
            "interval_effective": lim["max_concurrency"] == 1,
        }

    def reset(self, key_id: Optional[str] = None) -> None:
        if key_id:
            self._states.pop(key_id, None)
        else:
            self._states.clear()


token_limiter = TokenRateLimiter()
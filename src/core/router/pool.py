import time
from typing import Any, Dict


class ModelPool:
    TRANSIENT_FAILURE_REASONS = {
        "rate_limit",
        "unavailable",
        "server_error",
        "timeout",
        "unknown_error",
    }
    HARD_FAILURE_REASONS = {
        "rate_limit_rpd",
        "billing_error",
        "invalid_key",
        "permission_denied",
    }

    def __init__(self, pool_config: dict):
        self.members = list(pool_config["members"])
        self.swap_failures = int(pool_config["swap_failures"])
        self.max_retry_seconds = int(pool_config.get("max_retry_seconds", 120))
        self.current_idx = 0
        self.model_failures: Dict[str, int] = {m: 0 for m in self.members}
        self.total_attempts = 0
        self._exhausted_models: set = set()
        self._first_attempt_time = 0.0
        self._consecutive_transient = 0

    @property
    def current_model(self) -> str:
        return self.members[self.current_idx]

    def start(self) -> None:
        self._first_attempt_time = time.time()

    def effective_swap_threshold(self, reason: str = "") -> int:
        if reason in self.HARD_FAILURE_REASONS:
            return 1
        return self.swap_failures

    def failure_state_after_next(self, model_alias: str, reason: str = "") -> Dict[str, Any]:
        threshold = self.effective_swap_threshold(reason)
        failures_after = self.model_failures.get(model_alias, 0) + 1
        will_swap = failures_after >= threshold
        return {
            "failures_after": failures_after,
            "threshold": threshold,
            "will_swap": will_swap,
            "action": "swap after this failure" if will_swap else "retry same model",
        }

    def record_failure(self, model_alias: str, reason: str = "") -> bool:
        self.total_attempts += 1
        self.model_failures[model_alias] = self.model_failures.get(model_alias, 0) + 1
        effective_swap = self.effective_swap_threshold(reason)
        if self.model_failures[model_alias] >= effective_swap:
            self._exhausted_models.add(model_alias)
            return True
        return False

    def swap(self) -> bool:
        if len(self._exhausted_models) >= len(self.members):
            return False
        for offset in range(1, len(self.members)):
            idx = (self.current_idx + offset) % len(self.members)
            if self.members[idx] not in self._exhausted_models:
                self.current_idx = idx
                return True
        return False

    def reset_cycle(self) -> None:
        self._exhausted_models.clear()
        self.model_failures = {m: 0 for m in self.members}

    @property
    def exhausted(self) -> bool:
        if self._first_attempt_time <= 0:
            return False
        return time.time() - self._first_attempt_time >= self.max_retry_seconds

    @property
    def elapsed(self) -> float:
        if self._first_attempt_time <= 0:
            return 0.0
        return time.time() - self._first_attempt_time

    def remaining_time(self) -> float:
        if self._first_attempt_time <= 0:
            return float(self.max_retry_seconds)
        return max(0.0, self.max_retry_seconds - self.elapsed)

    def record_success(self) -> None:
        self.model_failures = {m: 0 for m in self.members}
        self.total_attempts = 0
        self._exhausted_models.clear()
        self._first_attempt_time = 0.0
        self._consecutive_transient = 0

import time
import pytest

from src.core.router.pool import ModelPool


def make_pool(max_retry_seconds=120, swap_failures=5):
    return ModelPool({
        "members": ["gemini-flash-35", "gemini-flash-30", "gemini-flash-25"],
        "swap_failures": swap_failures,
        "max_retry_seconds": max_retry_seconds,
    })


class TestPoolInit:
    def test_defaults(self):
        p = make_pool()
        assert p.members == ["gemini-flash-35", "gemini-flash-30", "gemini-flash-25"]
        assert p.swap_failures == 5
        assert p.max_retry_seconds == 120
        assert p.current_model == "gemini-flash-35"
        assert p.total_attempts == 0
        assert p._first_attempt_time == 0.0

    def test_not_exhausted_before_start(self):
        p = make_pool()
        assert not p.exhausted

    def test_elapsed_zero_before_start(self):
        p = make_pool()
        assert p.elapsed == 0.0
        assert p.remaining_time() == 120.0


class TestStart:
    def test_start_sets_time(self):
        p = make_pool()
        p.start()
        assert p._first_attempt_time > 0
        assert not p.exhausted
        assert p.elapsed < 1.0

    def test_remaining_time_after_start(self):
        p = make_pool(max_retry_seconds=10)
        p.start()
        time.sleep(0.5)
        assert 9.0 < p.remaining_time() <= 10.0


class TestRecordFailure:
    def test_record_failure_increments_attempts(self):
        p = make_pool()
        p.start()
        p.record_failure("gemini-flash-35", "rate_limit")
        assert p.total_attempts == 1
        assert p.model_failures["gemini-flash-35"] == 1

    def test_swap_threshold_not_reached(self):
        p = make_pool(swap_failures=5)
        p.start()
        for _ in range(4):
            p.record_failure("gemini-flash-35", "rate_limit")
        assert "gemini-flash-35" not in p._exhausted_models

    def test_swap_threshold_reached_at_5(self):
        p = make_pool(swap_failures=5)
        p.start()
        for _ in range(5):
            p.record_failure("gemini-flash-35", "rate_limit")
        assert "gemini-flash-35" in p._exhausted_models

    def test_hard_failure_swaps_immediately(self):
        p = make_pool()
        p.start()
        p.record_failure("gemini-flash-35", "billing_error")
        assert "gemini-flash-35" in p._exhausted_models

    @pytest.mark.parametrize("reason", ["rate_limit", "server_error", "unavailable", "timeout", "unknown_error"])
    def test_transient_failure_uses_swap_failures(self, reason):
        p = make_pool(swap_failures=3)
        p.start()
        for _ in range(2):
            p.record_failure("gemini-flash-35", reason)
        assert "gemini-flash-35" not in p._exhausted_models
        p.record_failure("gemini-flash-35", reason)
        assert "gemini-flash-35" in p._exhausted_models


class TestSwap:
    def test_swap_to_next_model(self):
        p = make_pool()
        p.start()
        p._exhausted_models.add("gemini-flash-35")
        assert p.swap()
        assert p.current_model == "gemini-flash-30"

    def test_swap_skips_exhausted(self):
        p = make_pool()
        p.start()
        p._exhausted_models.update(["gemini-flash-35", "gemini-flash-30"])
        assert p.swap()
        assert p.current_model == "gemini-flash-25"

    def test_swap_returns_false_all_exhausted(self):
        p = make_pool()
        p.start()
        p._exhausted_models.update(p.members)
        assert not p.swap()

    def test_swap_returns_false_no_members(self):
        p = ModelPool({
            "members": ["only-one"],
            "swap_failures": 5,
            "max_retry_seconds": 120,
        })
        p.start()
        p._exhausted_models.add("only-one")
        assert not p.swap()


class TestResetCycle:
    def test_clears_exhausted(self):
        p = make_pool()
        p.start()
        p._exhausted_models.update(p.members)
        p.reset_cycle()
        assert len(p._exhausted_models) == 0
        assert p.model_failures == {m: 0 for m in p.members}

    def test_after_reset_swap_works(self):
        p = make_pool()
        p.start()
        p._exhausted_models.update(p.members)
        p.reset_cycle()
        assert p.swap()  # finds gemini-flash-30


class TestExhaustedTimeBased:
    def test_not_exhausted_within_time(self):
        p = make_pool(max_retry_seconds=5)
        p.start()
        assert not p.exhausted

    def test_exhausted_after_timeout(self):
        p = make_pool(max_retry_seconds=0.1)
        p.start()
        time.sleep(0.2)
        assert p.exhausted

    def test_exhausted_after_max_retry_seconds(self):
        p = make_pool(max_retry_seconds=0.05)
        p.start()
        time.sleep(0.1)
        assert p.exhausted


class TestRecordSuccess:
    def test_resets_all_counters(self):
        p = make_pool()
        p.start()
        p.record_failure("gemini-flash-35", "rate_limit")
        p.record_failure("gemini-flash-35", "rate_limit")
        p._exhausted_models.add("gemini-flash-35")
        p.record_success()
        assert p.total_attempts == 0
        assert p.model_failures["gemini-flash-35"] == 0
        assert len(p._exhausted_models) == 0
        assert p._first_attempt_time == 0.0

    def test_not_exhausted_after_success(self):
        p = make_pool(max_retry_seconds=0.05)
        p.start()
        time.sleep(0.1)
        p.record_success()
        assert not p.exhausted


class TestFailureStateAfterNext:
    def test_failure_state_swap_after_threshold(self):
        p = make_pool(swap_failures=3)
        p.start()
        p.record_failure("gemini-flash-35", "rate_limit")
        state = p.failure_state_after_next("gemini-flash-35", "rate_limit")
        assert state["failures_after"] == 2
        assert state["threshold"] == 3
        assert not state["will_swap"]

        p.record_failure("gemini-flash-35", "rate_limit")
        state = p.failure_state_after_next("gemini-flash-35", "rate_limit")
        assert state["failures_after"] == 3
        assert state["will_swap"]

    def test_failure_state_hard_failure(self):
        p = make_pool()
        p.start()
        state = p.failure_state_after_next("gemini-flash-35", "billing_error")
        assert state["threshold"] == 1
        assert state["will_swap"]


class TestEndToEnd:
    def test_full_cycle_with_5_failures_per_model(self):
        p = make_pool(swap_failures=5, max_retry_seconds=10)
        p.start()
        models_tried = []

        while not p.exhausted:
            alias = p.current_model
            models_tried.append(alias)
            p.record_failure(alias, "rate_limit")
            if alias in p._exhausted_models:
                if not p.swap():
                    # all exhausted, backoff
                    p.reset_cycle()
                    break

        assert models_tried[:10] == ["gemini-flash-35"] * 5 + ["gemini-flash-30"] * 5
        assert not p.exhausted

    def test_swap_returns_false_and_exhausted(self):
        p = make_pool(max_retry_seconds=0.05, swap_failures=2)
        p.start()
        for m in p.members:
            p.record_failure(m, "rate_limit")
            p.record_failure(m, "rate_limit")
        assert not p.swap()
        time.sleep(0.1)
        assert p.exhausted

    def test_reset_after_all_exhausted_lifecycle(self):
        p = make_pool(max_retry_seconds=60, swap_failures=2)
        p.start()
        # Exhaust all 3 models
        for m in p.members:
            for _ in range(2):
                p.record_failure(m, "rate_limit")
        assert not p.swap()
        # Reset and continue
        p.reset_cycle()
        assert p.swap()
        assert p.current_model != "gemini-flash-35"

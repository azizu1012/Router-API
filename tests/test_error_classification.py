"""Error classification decides whether a key is frozen or retried.

_get_reason feeds retry_attempt, freeze_key and apply_error_penalty, so a
mispairing here either burns a good key for an hour or keeps hammering a dead
one. The text fallback exists because provider error strings vary, which makes
it exactly the kind of code that looks fine and is quietly wrong.

The Gemini classifier runs first and wins when it recognises the error; these
tests cover the fallback branch, and pin which way an ambiguous string is
resolved so the ordering cannot drift.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.pool_manager import _classify_error


def err(msg):
    return RuntimeError(msg)


class TestClassifyByStatusCode:
    @pytest.mark.parametrize("code,expected", [
        ("429", "rate_limit"),
        ("401", "invalid_key"),
        ("403", "permission_denied"),
        ("400", "bad_request"),
        ("503", "unavailable"),
    ])
    def test_numeric_code_is_recognised(self, code, expected):
        assert _classify_error(err(f"API returned {code} from upstream")) == expected


class TestClassifyByMessage:
    @pytest.mark.parametrize("msg,expected", [
        ("rate limit exceeded, slow down", "rate_limit"),
        ("Too Many Requests", "rate_limit"),
        ("quota_exhausted", "rate_limit"),
        ("billing is not enabled for this project", "billing_error"),
        ("precondition not met", "billing_error"),
        ("invalid key supplied", "invalid_key"),
        ("permission denied for this model", "permission_denied"),
        ("bad request payload", "bad_request"),
        ("model is overloaded", "unavailable"),
        ("service unavailable", "unavailable"),
    ])
    def test_phrase_is_recognised(self, msg, expected):
        assert _classify_error(err(msg)) == expected


class TestUnrecognised:
    def test_unknown_message_is_unknown(self):
        assert _classify_error(err("something nobody anticipated")) == "unknown"

    def test_empty_message_is_unknown(self):
        assert _classify_error(err("")) == "unknown"

    @pytest.mark.parametrize("value", [None, 0, [], {}])
    def test_non_exception_input_does_not_raise(self, value):
        assert _classify_error(value) in (
            "unknown", "rate_limit", "invalid_key", "permission_denied",
            "bad_request", "unavailable", "billing_error",
            "rate_limit_rpd",
        )


class TestOrdering:
    """Tier 1 runs first and wins; the text fallback only sees what it rejects.

    Verified rather than assumed: the Gemini classifier claims most quota
    strings before the fallback branch is reached, so pinning "which rule won"
    for those strings would be pinning the wrong layer.
    """

    def test_tier_one_claims_quota_strings_before_the_fallback(self):
        from src.core.providers.gemini.error import classify as gemini_classify
        msg = "quota exceeded for this project"
        assert gemini_classify(RuntimeError(msg)) != "unknown", (
            "precondition for this test: tier 1 must claim it"
        )
        assert _classify_error(err(msg)) == gemini_classify(RuntimeError(msg))

    def test_fallback_only_handles_what_tier_one_rejects(self):
        from src.core.providers.gemini.error import classify as gemini_classify
        msg = "rate limit exceeded"          # tier 1 returns unknown here
        assert gemini_classify(RuntimeError(msg)) == "unknown"
        assert _classify_error(err(msg)) == "rate_limit"

    def test_daily_quota_keeps_its_own_reason(self):
        """Rate-per-minute and rate-per-day freeze differently; do not merge."""
        assert _classify_error(err("quota exceeded, daily limit")) == "project_quota_429"

    def test_bad_request_is_not_reported_as_unavailable(self):
        got = _classify_error(err("400 bad request: model unavailable"))
        assert got == "bad_request"


    def test_fallback_rpd_branch_is_shadowed_by_tier_one(self):
        """The fallback's `return "rate_limit_rpd"` cannot currently be reached.

        Every "quota exceeded" phrasing is claimed by the Gemini classifier
        before the text fallback is consulted, so the branch is dead today. It
        is kept deliberately: the reason string is still produced elsewhere
        (gemini/utils.py) and relied on by key_resolver, so removing the branch
        would turn any future relaxation of tier 1 into a silent behaviour
        change. This test exists so that relaxation shows up as a failure here
        rather than as an unlabelled freeze somewhere downstream.
        """
        from src.core.providers.gemini.error import classify as gemini_classify
        for msg in ("quota exceeded", "quota exceeded today",
                    "daily quota exceeded", "quota exceeded for project"):
            assert gemini_classify(err(msg)) != "unknown", (
                f"{msg!r} is now handled by tier 1; decide whether the fallback "
                f"branch should still return rate_limit_rpd"
            )
            assert _classify_error(err(msg)) != "rate_limit_rpd"


class TestStability:
    @pytest.mark.parametrize("msg", [
        "rate limit", "invalid key", "permission denied",
        "bad request", "unavailable", "billing",
        "too many requests", "overloaded",
    ])
    def test_every_known_phrase_maps_to_a_stable_reason(self, msg):
        first = _classify_error(err(msg))
        assert _classify_error(err(msg)) == first
        assert first != "unknown", f"{msg!r} should be recognised"
"""Tier caps bound what an admin can hand out.

An admin may loosen a user's limits. Without a ceiling, "give this account more
headroom" has no upper bound and one account can draw against the whole key pool
on its own — the only remaining limit is how many keys happen to be unfrozen,
which is a coincidence rather than a policy.

Two rules that are easy to confuse:

    the tier ceiling   caps the account row. The account limiter reads these
                      three numbers on every request, so this is the one that
                      actually binds.

    token limits      a separate layer. docs/account_auth.md promises an admin
                      may configure a token up to 100k rpm even for a free
                      account, because the account limiter is what caps the
                      traffic. Clamping tokens to the tier would break that
                      promise without lowering any real ceiling.

Clamping is down-only in both cases: raising a tier must never silently raise a
limit an admin had already lowered on purpose.
"""

import pytest

from src.core.config_n_logg import config
from src.core.tier_limits import (
    clamp_to_account,
    clamp_to_tier,
    normalise_tier,
    tier_cap,
)


class TestTierCapTable:
    def test_every_tier_has_all_three_limits(self):
        for tier in ("free", "premium", "admin"):
            caps = tier_cap(tier)
            assert set(caps) == {"rpm", "tpm", "rpd"}, tier
            assert all(int(v) > 0 for v in caps.values()), caps

    def test_admin_is_not_smaller_than_premium_is_not_smaller_than_free(self):
        free, premium, admin = (tier_cap(t) for t in ("free", "premium", "admin"))
        for field in ("rpm", "tpm", "rpd"):
            assert free[field] <= premium[field] <= admin[field], (
                f"{field}: free={free[field]} premium={premium[field]} admin={admin[field]}"
            )

    def test_admin_caps_match_the_legacy_defaults(self):
        """Existing admin accounts must not suddenly gain a lower ceiling."""
        caps = tier_cap("admin")
        assert caps["rpm"] == config.DEFAULT_ACCOUNT_RPM
        assert caps["tpm"] == config.DEFAULT_ACCOUNT_TPM
        assert caps["rpd"] == config.DEFAULT_ACCOUNT_RPD


class TestNormaliseTier:
    @pytest.mark.parametrize("bad", [None, "", "gold", "premiumd", "adm1n", 7, [], {}])
    def test_unknown_tiers_fall_back_to_free(self, bad):
        assert normalise_tier(bad) == "free"

    def test_a_known_tier_survives_case_and_space(self):
        assert normalise_tier(" Premium ") == "premium"

    def test_an_unknown_tier_gets_frees_cap_not_admins(self):
        """A typo must grant the smallest budget, never the largest."""
        assert tier_cap("premiumd") == tier_cap("free")


class TestClampToTier:
    def test_a_value_above_the_ceiling_is_reduced(self):
        out = clamp_to_tier("free", {"rpm": 999_999})
        assert out["rpm"] == tier_cap("free")["rpm"]

    def test_a_value_under_the_ceiling_is_untouched(self):
        assert clamp_to_tier("free", {"rpm": 5})["rpm"] == 5

    def test_a_value_exactly_at_the_ceiling_is_untouched(self):
        assert clamp_to_tier("free", {"rpm": tier_cap("free")["rpm"]})["rpm"] == \
            tier_cap("free")["rpm"]

    def test_absent_limits_are_left_absent(self):
        out = clamp_to_tier("free", {"rpm": 999_999})
        assert "tpm" not in out and "rpd" not in out

    def test_none_values_are_left_none(self):
        out = clamp_to_tier("free", {"rpm": None})
        assert out["rpm"] is None

    def test_a_non_numeric_value_is_passed_through_not_crashed_on(self):
        out = clamp_to_tier("free", {"rpm": "abc"})
        assert out["rpm"] == "abc"

    def test_all_three_limits_are_clamped_together(self):
        caps = tier_cap("free")
        out = clamp_to_tier("free", {"rpm": 10 ** 6, "tpm": 10 ** 9, "rpd": 10 ** 6})
        assert out == {"rpm": caps["rpm"], "tpm": caps["tpm"], "rpd": caps["rpd"]}

    def test_the_input_is_not_mutated(self):
        original = {"rpm": 999_999}
        clamp_to_tier("free", original)
        assert original["rpm"] == 999_999

    def test_raising_a_tier_does_not_raise_an_already_low_value(self):
        """Clamping down only. Otherwise promoting a user silently inflates them."""
        assert clamp_to_tier("admin", {"rpm": 7})["rpm"] == 7

    def test_promoting_a_user_reclamps_against_the_new_tier(self):
        out = clamp_to_tier("premium", {"rpm": 10 ** 6})
        assert out["rpm"] == tier_cap("premium")["rpm"]


class TestClampToAccount:
    def test_the_lower_of_token_ceiling_and_account_wins(self):
        caps = tier_cap("free")
        account = {"rpm": 10, "tpm": 20, "rpd": 30}
        out = clamp_to_account("free", {"rpm": 10 ** 6, "tpm": 10 ** 9, "rpd": 10 ** 6}, account)
        assert out == {"rpm": 10, "tpm": 20, "rpd": 30}

    def test_a_token_below_its_account_is_not_raised(self):
        out = clamp_to_account("admin", {"rpm": 5}, {"rpm": 100_000})
        assert out["rpm"] == 5

    def test_an_account_missing_a_field_does_not_clamp_it(self):
        out = clamp_to_account("free", {"rpm": 10 ** 6}, {})
        assert out["rpm"] == tier_cap("free")["rpm"]


class TestConfigIsEnvDriven:
    def test_the_cap_names_are_the_ones_in_the_env_example(self):
        import pathlib
        example = pathlib.Path(".env.example")
        if not example.exists():
            pytest.skip("no .env.example in this checkout")
        text = example.read_text(encoding="utf-8")
        for tier in ("FREE", "PREMIUM", "ADMIN"):
            for field in ("RPM", "TPM", "RPD"):
                name = f"ROUTER_API_TIER_{tier}_{field}"
                assert name in text, f"{name} is not documented in .env.example"

"""Per-tier ceilings on account and token limits.

An admin may loosen a user's limits, but not without bound. Without a ceiling
"give this account more headroom" eventually consumes the whole key pool on its
own, and the only remaining limit is how many keys happen to be unfrozen at that
moment — which is not a policy, it is a coincidence.

Two distinct rules, and the difference matters:

    clamp_to_tier   the tier ceiling. An admin asking for more than the tier
                    allows gets the tier's maximum.

    clamp_to_account a token can never exceed the account that owns it. This is
                    separate from the tier ceiling: a free account's token
                    clamped to free's ceiling could still exceed the account's
                    own rpm if the account was itself set below that.

Both clamp down only. Lowering a tier must never silently raise an existing
limit, so an account whose stored value is already under the ceiling is left
alone rather than raised to meet it.

Unknown tiers fall back to free. That is deliberate: a typo in a tier string
should grant the smallest budget, not the largest.
"""

from typing import Any, Dict, Optional

from src.core.config_n_logg import config

FREE = "free"
VALID_TIERS = ("free", "premium", "admin")


def normalise_tier(tier: Optional[str]) -> str:
    t = str(tier or "").strip().lower()
    return t if t in VALID_TIERS else FREE


def tier_cap(tier: Optional[str]) -> Dict[str, int]:
    """The ceiling for a tier. Unknown tiers get free's."""
    return config.TIER_CAPS[normalise_tier(tier)]


def clamp_to_tier(tier: Optional[str], limits: Dict[str, Any]) -> Dict[str, Any]:
    """Cap each of rpm/tpm/rpd at the tier maximum, clamping down only.

    A limit that is None or absent is left absent — the caller's default
    applies. Only values that are actually above the ceiling get reduced.
    """
    caps = tier_cap(tier)
    out = dict(limits)
    for field, ceiling in caps.items():
        value = out.get(field)
        if value is None:
            continue
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        out[field] = min(n, int(ceiling))
    return out


def clamp_to_account(tier: Optional[str], limits: Dict[str, Any],
                     account_limits: Dict[str, Any]) -> Dict[str, Any]:
    """Cap a token's limits by both the tier ceiling and its owning account."""
    out = clamp_to_tier(tier, limits)
    for field in ("rpm", "tpm", "rpd"):
        account_value = account_limits.get(field)
        if account_value is None:
            continue
        current = out.get(field)
        if current is None:
            continue
        try:
            out[field] = min(int(current), int(account_value))
        except (TypeError, ValueError):
            continue
    return out

"""Model alias resolution helper.

This module provides utilities to resolve model aliases before routing requests.
It integrates with the model_resolver to support seamless alias → target mapping.
"""

import json
import random
from typing import Optional, Dict, Any, Tuple, List, Union

from src.backend.models import aliases as model_aliases
from src.core.config_n_logg.logger import logger_proxy as logger


def resolve_model_alias(
    model_name: str,
    account_id: str,
    account_key_id: Optional[str] = None
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Resolve a model alias to its target model and endpoint.

    This function is called BEFORE routing to determine if the model name
    is an alias that should be resolved to a different target.

    Args:
        model_name: Model name from request
        account_id: Account ID from authentication
        account_key_id: Optional key ID for key-specific aliases

    Returns:
        Tuple of (resolved_model_name, alias_info)
        - If alias found: (target_model, alias_dict)
        - If no alias: (original_model_name, None)

    Examples:
        >>> resolve_model_alias("claude-3-5-sonnet", "user123", "key1")
        ("gemini-flash", {"target_endpoint": None, ...})

        >>> resolve_model_alias("gpt-4", "user123", "key1")
        ("claude-3-5-sonnet", {"target_endpoint": "my-anthropic", ...})

        >>> resolve_model_alias("gemini-flash", "user123", "key1")
        ("gemini-flash", None)  # No alias, use original
    """
    alias = model_aliases.resolve_alias_db(
        alias_name=model_name,
        account_id=account_id,
        account_key_id=account_key_id,
    )

    if not alias:
        return model_name, None

    # Alias found - if combo mix, dynamically resolve target model & endpoint
    if alias.get("targets"):
        target_model, target_endpoint = resolve_combo_target(alias)
        alias["target_model"] = target_model
        alias["target_endpoint"] = target_endpoint
    else:
        target_model = alias["target_model"]

    logger.info(
        "[AliasResolve] %s → %s (account=%s, key=%s, endpoint=%s, combo=%s)",
        model_name,
        target_model,
        account_id,
        account_key_id or "ALL",
        alias.get("target_endpoint") or "POOL",
        bool(alias.get("targets")),
    )

    return target_model, alias


def should_route_to_custom_endpoint(alias_info: Optional[Dict[str, Any]]) -> bool:
    """Check if alias should route to custom endpoint (not pool)."""
    if not alias_info:
        return False
    return alias_info.get("target_endpoint") is not None


def get_target_endpoint_name(alias_info: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract target endpoint name from alias info."""
    if not alias_info:
        return None
    return alias_info.get("target_endpoint")


def get_original_model_name(alias_info: Optional[Dict[str, Any]]) -> Optional[str]:
    """Get the original alias name (for response spoofing)."""
    if not alias_info:
        return None
    return alias_info.get("alias_name")


def get_model_safe_context_length(model_name: str, endpoint_name: Optional[str] = None) -> int:
    """Returns the safe client-facing context length for a given model or custom endpoint."""
    from src.core.config.api_config import AVAILABLE_MODELS, MODEL_CONTEXT_LENGTH, GEMMA_CONTEXT_LENGTH

    if endpoint_name:
        try:
            from src.backend.endpoints.manager import get_endpoint_db
            ep = get_endpoint_db(endpoint_name)
            if ep and ep.get("context_length"):
                return int(ep["context_length"])
        except Exception:
            pass
        return 200000  # Default standard custom endpoint context

    m_lower = (model_name or "").strip().lower()
    if "gemma" in m_lower:
        return GEMMA_CONTEXT_LENGTH
    cfg = AVAILABLE_MODELS.get(m_lower)
    if cfg and cfg.get("context_length"):
        return int(cfg["context_length"])
    return MODEL_CONTEXT_LENGTH


def get_alias_context_length(alias_info: Optional[Dict[str, Any]]) -> int:
    """Calculates the safe client context length for an alias.

    1. If alias_info specifies explicit context_length > 0, return it.
    2. If alias represents a Combo Model Mix (targets list/JSON), return
       the MINIMUM context length across all targets in the combo.
    3. If alias is a 1:1 spoof, return the underlying target model's safe context length.
    """
    if not alias_info:
        from src.core.config.api_config import MODEL_CONTEXT_LENGTH
        return MODEL_CONTEXT_LENGTH

    explicit_cl = alias_info.get("context_length")
    if explicit_cl and int(explicit_cl) > 0:
        return int(explicit_cl)

    targets_raw = alias_info.get("targets")
    if targets_raw:
        try:
            targets_list = json.loads(targets_raw) if isinstance(targets_raw, str) else targets_raw
            if isinstance(targets_list, list) and targets_list:
                contexts = []
                for t in targets_list:
                    if isinstance(t, str):
                        contexts.append(get_model_safe_context_length(t))
                    elif isinstance(t, dict):
                        # Explicit per-target context length takes top priority
                        t_cl = t.get("context_length")
                        if t_cl and int(t_cl) > 0:
                            contexts.append(int(t_cl))
                        else:
                            t_model = t.get("target") or t.get("model") or ""
                            t_ep = t.get("endpoint") or t.get("target_endpoint")
                            contexts.append(get_model_safe_context_length(t_model, t_ep))
                if contexts:
                    return min(contexts)
        except Exception as e:
            logger.warning("[AliasContext] Failed to parse combo targets: %s", e)

    # 1:1 spoof: use target_model and target_endpoint
    target_model = alias_info.get("target_model", "")
    target_ep = alias_info.get("target_endpoint")
    return get_model_safe_context_length(target_model, target_ep)


def resolve_combo_target(
    alias_info_or_targets: Union[Dict[str, Any], List[Any], str],
    strategy: Optional[str] = None,
    attempt_index: int = 0,
) -> Any:
    """Resolves which target model & endpoint to dispatch to from a Combo Model Mix.

    Args:
        alias_info_or_targets: Either the full alias_info dictionary, or raw targets list/string.
        strategy: Override strategy ("waterfall" or "load_balance"). If None, read from alias_info.
        attempt_index: 0-based retry/attempt counter for waterfall fallback rotation.

    Returns:
        If called with alias_info dict: Tuple[str, Optional[str]] -> (target_model, target_endpoint)
        If called directly with targets list: Dict[str, Any] or Tuple matching format.
    """
    targets_raw = alias_info_or_targets
    fallback_model = ""
    fallback_ep = None
    default_strategy = strategy or "waterfall"

    if isinstance(alias_info_or_targets, dict):
        if "targets" in alias_info_or_targets:
            targets_raw = alias_info_or_targets.get("targets")
            fallback_model = alias_info_or_targets.get("target_model", "")
            fallback_ep = alias_info_or_targets.get("target_endpoint")
            default_strategy = strategy or str(alias_info_or_targets.get("strategy") or "waterfall").lower()

    if not targets_raw:
        if isinstance(alias_info_or_targets, dict) and "targets" not in alias_info_or_targets and "model" in alias_info_or_targets:
            return alias_info_or_targets
        return fallback_model, fallback_ep

    try:
        targets_list = json.loads(targets_raw) if isinstance(targets_raw, str) else targets_raw
        if not isinstance(targets_list, list) or not targets_list:
            return fallback_model, fallback_ep

        strat = (default_strategy or "waterfall").lower()

        if strat == "load_balance":
            weights = []
            candidates = []
            for t in targets_list:
                if isinstance(t, str):
                    candidates.append({"model": t, "endpoint": None})
                    weights.append(100)
                elif isinstance(t, dict):
                    candidates.append(t)
                    weights.append(int(t.get("weight") or 100))
            if candidates:
                chosen = random.choices(candidates, weights=weights, k=1)[0]
                if isinstance(alias_info_or_targets, dict) and "targets" in alias_info_or_targets:
                    return chosen.get("target") or chosen.get("model") or "", chosen.get("endpoint") or chosen.get("target_endpoint")
                return chosen

        # Default waterfall: select candidate based on attempt_index (with wrap-around)
        idx = attempt_index % len(targets_list)
        chosen_t = targets_list[idx]
        if isinstance(alias_info_or_targets, dict) and "targets" in alias_info_or_targets:
            if isinstance(chosen_t, str):
                return chosen_t, None
            if isinstance(chosen_t, dict):
                return chosen_t.get("target") or chosen_t.get("model") or "", chosen_t.get("endpoint") or chosen_t.get("target_endpoint")
        return chosen_t
    except Exception as e:
        logger.warning("[ComboTarget] Error resolving combo target: %s", e)

    return fallback_model, fallback_ep


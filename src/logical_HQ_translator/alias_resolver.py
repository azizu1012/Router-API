"""Model alias resolution helper.

This module provides utilities to resolve model aliases before routing requests.
It integrates with the model_resolver to support seamless alias → target mapping.
"""

from typing import Optional, Dict, Any, Tuple

from src.backend import model_aliases
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

    # Alias found - return target model and full alias info
    target_model = alias["target_model"]

    logger.info(
        "[AliasResolve] %s → %s (account=%s, key=%s, endpoint=%s)",
        model_name,
        target_model,
        account_id,
        account_key_id or "ALL",
        alias.get("target_endpoint") or "POOL"
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

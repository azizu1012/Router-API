"""Pure passthrough client for custom endpoints.

Custom endpoints are simple translation bridges, not pool members:
- Translate OpenAI ↔ Anthropic format
- Forward request to target endpoint
- Return response with original error codes (no circuit breaker, no retry)

Assignment hierarchy:
1. account_key_id takes precedence (key-level assignment)
2. account_id fallback (account-level assignment)
3. No assignment = endpoint not available for this request

This module is peer to pool_manager, not inside it.
"""

import types
from typing import Any, Dict, List, Optional, Tuple

from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.providers import _custom_endpoint_manager as endpoint_manager
from src.core.providers.custom_endpoint_client import (
    call_custom_nonstream,
    CustomEndpointStreamGen,
)


async def resolve_custom_endpoint_for_request(
    account: Optional[Dict[str, Any]],
    account_key_id: Optional[str] = None,
    requested_model: Optional[str] = None,
    alias_info: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Find custom endpoint assigned to this request.

    Priority:
    1. Explicit alias target_endpoint
    2. Endpoint containing requested_model for this account
    3. Key-level assignment (account_key_id) if requested_model is not a pool model
    4. Account-level assignment (account_id) if requested_model is not a pool model

    Returns endpoint dict with metadata or None.
    """
    from src.core.api_config import AVAILABLE_MODELS, MODEL_POOLS

    # Priority 1: Alias handling
    if alias_info:
        if alias_info.get("target_endpoint"):
            from src.backend.endpoints import get_endpoint_db
            ep = get_endpoint_db(alias_info["target_endpoint"])
            if ep and ep.get("enabled", True):
                logger.info(
                    "[CustomEndpointPassthrough] Found alias target_endpoint %s",
                    ep.get("name")
                )
                return ep
        else:
            # Alias explicitly targets pool (target_endpoint is None), do not route to custom endpoint
            return None

    if not account:
        return None

    account_id = account.get("account_id")
    if not account_id:
        return None

    # If requested_model is a known Gemini pool or built-in model, don't hijack it!
    req_norm = (requested_model or "").strip().lower()
    if req_norm in MODEL_POOLS or req_norm in AVAILABLE_MODELS or "flash" in req_norm or "gemini" in req_norm:
        return None

    # Priority 2: Endpoint containing requested_model for this account
    if requested_model:
        for ep in endpoint_manager.get_endpoints_for_account(account):
            if not ep.get("enabled", True):
                continue
            raw_models = ep.get("enabled_models")
            models = raw_models if isinstance(raw_models, list) and len(raw_models) > 0 else (ep.get("models") or [])
            if isinstance(models, list) and requested_model in models:
                logger.info(
                    "[CustomEndpointPassthrough] Found model match %s on endpoint %s",
                    requested_model, ep.get("name")
                )
                return ep

    # Priority 3: Key-level assignment
    if account_key_id:
        from src.backend.endpoints import get_endpoint_by_key_db
        ep = get_endpoint_by_key_db(account_key_id)
        if ep and ep.get("enabled", True):
            logger.info(
                "[CustomEndpointPassthrough] Found key-level endpoint %s for account_key_id=%s",
                ep.get("name"), account_key_id
            )
            return ep

    # Priority 4: Account-level assignment (no pool_assignments)
    for ep in endpoint_manager.get_endpoints_for_account(account):
        if not ep.get("enabled", True):
            continue

        # Skip endpoints that are pool members
        pool_assignments = ep.get("pool_assignments", {})
        if pool_assignments:
            continue

        if ep.get("account_key_id"):
            continue

        logger.info(
            "[CustomEndpointPassthrough] Found account-level endpoint %s for account_id=%s",
            ep.get("name"), account_id
        )
        return ep

    return None


async def call_custom_endpoint_passthrough(
    endpoint: Dict[str, Any],
    model: str,
    messages: List[Dict[str, Any]],
    stream: bool = False,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
    tool_choice: Any = None,
    parallel_tool_calls: Optional[bool] = None,
    response_format: Any = None,
) -> Tuple[Any, Optional[CustomEndpointStreamGen]]:
    """Call custom endpoint with pure passthrough (no retry, no circuit breaker).

    Returns:
        (response, stream_gen) - one will be None depending on stream parameter
    """
    ep_name = endpoint.get("name", "unknown")
    api_base = endpoint["base_url"]
    api_key = endpoint["auth_key"]
    api_format = endpoint.get("api_format") or "openai"

    # Check if endpoint is frozen by circuit breaker
    if endpoint_manager.is_endpoint_frozen(ep_name):
        logger.warning(
            "[CustomEndpointPassthrough] %s is frozen by circuit breaker, rejecting request",
            ep_name
        )
        raise RuntimeError(
            f"Custom endpoint '{ep_name}' is temporarily unavailable (circuit breaker active). "
            "This is usually caused by repeated upstream errors. Try again in 15-120s."
        )

    # Pick target model
    enabled_models = endpoint.get("enabled_models", [])
    all_models = endpoint.get("models", [])
    if model and model.strip():
        target_model = model.strip()
    elif enabled_models:
        target_model = enabled_models[0]
    elif all_models:
        target_model = all_models[0]
    else:
        target_model = model or "default"

    logger.info(
        "[CustomEndpointPassthrough] Routing to endpoint=%s model=%s api_format=%s stream=%s",
        ep_name, target_model, api_format, stream
    )

    if stream:
        stream_gen = CustomEndpointStreamGen(
            api_base=api_base,
            api_key=api_key,
            model=target_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            extra_body=extra_body,
            api_format=api_format,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            response_format=response_format,
        )
        return None, stream_gen
    else:
        response = await call_custom_nonstream(
            api_base=api_base,
            api_key=api_key,
            model=target_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            extra_body=extra_body,
            api_format=api_format,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            response_format=response_format,
        )
        return response, None

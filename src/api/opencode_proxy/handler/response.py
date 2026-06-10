"""Response builders for OpenCode proxy chat completions.

Handles building the final response dict, error responses,
cost estimation, and client model name mapping.
"""

import time
import uuid
from typing import Any, Dict, Optional

from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.usage_logger import log_usage
from src.api.claude_proxy.handler.helpers import get_system_status_summary
from src.api.claude_proxy.utils import _get_simulated_cache_usage


def get_client_model_name(requested_model: str) -> str:
    """Return the model name as-is — OpenCode matches against ``opencode.json``."""
    return requested_model


def estimate_cost(input_tokens: int, output_tokens: int, model_alias: str) -> float:
    """Calculate estimated cost for this request using DB prices or defaults."""
    is_lite = "lite" in str(model_alias).lower()
    input_rate = 0.001 if is_lite else 0.0025
    output_rate = 0.004 if is_lite else 0.010

    try:
        from src.backend.model_prices import get_model_price
        cfg = get_model_price(model_alias)
        if not cfg:
            pool_key = "gemini-flash-lite" if is_lite else "gemini-flash"
            cfg = get_model_price(pool_key)
        if cfg:
            input_rate = float(cfg.get("input_rate_per_1k", input_rate))
            output_rate = float(cfg.get("output_rate_per_1k", output_rate))
    except Exception as e:
        logger.warning("[Cost] DB lookup failed: %s", e)

    return round(input_tokens * input_rate / 1000 + output_tokens * output_rate / 1000, 6)


def build_response(
    body: Dict[str, Any], resp: Any, model_alias: str, api_key: str, input_tokens: int,
) -> Dict[str, Any]:
    """Construct the final chat completion response dict."""
    if isinstance(resp, dict):
        return resp

    choice = resp.choices[0] if resp.choices else None
    if not choice:
        text, finish = "", "stop"
    else:
        text = _extract_text(choice)
        finish = getattr(choice, "finish_reason", "stop")

    out_tokens = 0
    usage = getattr(resp, "usage", None)
    if usage:
        out_tokens = getattr(usage, "completion_tokens", 0) or 0
        input_tokens = getattr(usage, "prompt_tokens", 0) or input_tokens

    cost = estimate_cost(input_tokens, out_tokens, model_alias)
    kp = api_key[-8:] if api_key else ""
    cache_usage = _get_simulated_cache_usage(body, input_tokens)
    cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
    cr = cache_usage.get("cache_read_input_tokens", 0) or 0
    import asyncio
    asyncio.ensure_future(log_usage(model_alias, kp, input_tokens, out_tokens, "", cc, cr))

    requested_model = body.get("model") or model_alias
    model_name = get_client_model_name(requested_model)

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": input_tokens, "completion_tokens": out_tokens, "total_tokens": input_tokens + out_tokens, "cost": cost},
    }


def error_response(body: Dict[str, Any], model_name: str, reason: str = "pool_exhausted") -> Dict[str, Any]:
    """Build a graceful error response with a system status summary."""
    text = get_system_status_summary(model_name, reason)
    mapped_model = get_client_model_name(model_name)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": mapped_model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
    }


def _extract_text(choice: Any) -> str:
    content = getattr(choice.message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        return "\n".join(parts)
    return str(content or "")

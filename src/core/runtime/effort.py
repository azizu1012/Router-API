"""Reasoning-effort normalization for custom endpoints.

Different clients express the same intent in four incompatible spellings:
``reasoning_effort`` (OpenAI), ``reasoning.effort`` (OpenCode/Codex),
``output_config.effort`` (Claude Code) and ``thinking`` blocks. Two of the
values they send — ``auto`` and ``default`` — are client-side placeholders, not
provider values, and OpenAI-compatible endpoints reject the request outright::

    400 invalid_value: "Invalid value: 'auto'. Supported values are:
    'none', 'minimal', 'low', 'medium', 'high', 'xhigh', and 'max'."

The fix is to drop the placeholder so the upstream model picks its own default.
These functions are pure — no config, no state, no I/O — so both the provider
layer and the protocol adapters can import them without breaking layering.
"""

from typing import Any, Dict, Optional

_VALID_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# Spellings that mean "let the model decide". Sending them as-is fails.
_PLACEHOLDERS = ("", "auto", "default")


def normalize_reasoning_effort(effort: Any) -> Optional[str]:
    """Map any client spelling to a provider-valid effort, or ``None``.

    ``None`` means "omit the field". That is deliberate: omitting lets the
    upstream endpoint apply its own default, while forwarding ``"auto"`` is a
    400 on every strict OpenAI-compatible server.
    """
    if not isinstance(effort, str):
        return None
    value = effort.strip().lower()
    if value in _PLACEHOLDERS:
        return None
    if value in ("none", "off", "false", "disabled"):
        return "none"
    if value in ("minimal", "min"):
        return "minimal"
    if value == "low":
        return "low"
    if value == "medium":
        return "medium"
    if value == "high":
        return "high"
    if value in ("xhigh", "max", "maximum", "highest"):
        return "max"
    if value in _VALID_EFFORTS:
        return value
    return None


def _thinking_to_effort(thinking: Any) -> Optional[str]:
    """Translate an Anthropic ``thinking`` block into an effort level."""
    if not isinstance(thinking, dict):
        return None
    if str(thinking.get("type") or "").strip().lower() == "disabled":
        return "none"
    budget = thinking.get("budget_tokens")
    try:
        budget = int(budget)
    except (TypeError, ValueError):
        return None
    if budget >= 8192:
        return "high"
    if budget >= 4096:
        return "medium"
    if budget > 0:
        return "low"
    if budget == 0:
        return "none"
    return None


def extract_reasoning_effort(body: Dict[str, Any]) -> Optional[str]:
    """Read the first *present* effort source in the body and normalize it.

    Order follows the task contract: explicit ``reasoning_effort`` wins, then
    ``reasoning.effort``, then ``output_config.effort``, then a ``thinking``
    block. ``None`` means no usable value, and callers must omit the field.
    """
    if not isinstance(body, dict):
        return None

    if body.get("reasoning_effort") is not None:
        return normalize_reasoning_effort(body.get("reasoning_effort"))

    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort") is not None:
        return normalize_reasoning_effort(reasoning.get("effort"))

    output_config = body.get("output_config")
    if isinstance(output_config, dict) and output_config.get("effort") is not None:
        return normalize_reasoning_effort(output_config.get("effort"))

    if body.get("thinking") is not None:
        return _thinking_to_effort(body.get("thinking"))

    return None


def sanitize_custom_endpoint_payload(
    payload: Dict[str, Any], api_format: str = "openai"
) -> None:
    """Strip or normalize reasoning fields so a custom endpoint accepts them.

    Mutates ``payload`` in place. Anthropic-format endpoints reject the OpenAI
    spellings, and OpenAI-format endpoints reject Anthropic's ``thinking`` and
    ``output_config`` — so which fields survive depends on the dialect.

    ``auto``/``default`` are removed rather than translated: forwarding them is
    exactly the 400 this function exists to prevent.
    """
    if not isinstance(payload, dict):
        return

    effort = extract_reasoning_effort(payload)

    if api_format == "anthropic":
        # Anthropic has no reasoning_effort field; the equivalent is the
        # thinking block, which the caller may already have set.
        payload.pop("reasoning_effort", None)
        payload.pop("reasoning", None)
        if "output_config" not in payload and effort in ("low", "medium", "high", "max"):
            payload["output_config"] = {"effort": effort}
        return

    # OpenAI-compatible endpoints reject fields they do not declare.
    payload.pop("thinking", None)
    payload.pop("output_config", None)

    if payload.get("reasoning_effort") is not None:
        normalized = normalize_reasoning_effort(payload.get("reasoning_effort"))
        if normalized is None:
            payload.pop("reasoning_effort", None)
        else:
            payload["reasoning_effort"] = normalized

    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict):
        if reasoning.get("effort") is not None:
            normalized = normalize_reasoning_effort(reasoning.get("effort"))
            if normalized is None:
                reasoning.pop("effort", None)
            else:
                reasoning["effort"] = normalized
        if not reasoning:
            payload.pop("reasoning", None)

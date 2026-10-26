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

_MAX_TOOL_NAME_LENGTH = 64


def _truncate_tool_name(value: Any) -> Any:
    """Keep provider-facing tool names within strict endpoint limits."""
    if isinstance(value, str):
        return value[:_MAX_TOOL_NAME_LENGTH]
    return value


def _sanitize_tool_names(payload: Dict[str, Any]) -> None:
    """Normalize tool names in schemas, choices, and prior tool blocks."""
    tools = payload.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function")
            if isinstance(function, dict) and "name" in function:
                function["name"] = _truncate_tool_name(function["name"])
            elif "name" in tool:
                tool["name"] = _truncate_tool_name(tool["name"])

    choice = payload.get("tool_choice")
    if isinstance(choice, dict):
        function = choice.get("function")
        if isinstance(function, dict) and "name" in function:
            function["name"] = _truncate_tool_name(function["name"])
        elif "name" in choice:
            choice["name"] = _truncate_tool_name(choice["name"])

    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") in ("tool_use", "agent_use"):
                    if "name" in block:
                        block["name"] = _truncate_tool_name(block["name"])


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
    _sanitize_tool_names(payload)

    if api_format == "anthropic":
        # Anthropic has no reasoning_effort field; the equivalent is the
        # thinking block, which the caller may already have set.
        payload.pop("reasoning_effort", None)
        payload.pop("reasoning", None)
        if "output_config" not in payload and effort in ("low", "medium", "high", "max"):
            payload["output_config"] = {"effort": effort}
        # Same dangling-choice 400 on this dialect: a forced tool_choice with
        # no tools to choose from fails validation before the model runs.
        if not payload.get("tools") and "tool_choice" in payload:
            choice = payload.get("tool_choice")
            keep = False
            if isinstance(choice, dict):
                keep = choice.get("type") == "auto"
            elif choice == "auto":
                keep = True
            if not keep:
                payload.pop("tool_choice", None)
        return
    # OpenAI-compatible endpoints reject fields they do not declare.
    payload.pop("thinking", None)
    payload.pop("output_config", None)

    # An interrupt can strip the tools list (converter drops stale tool
    # definitions) while the client's tool_choice rides along in extra_body.
    # A forced/named choice with no tools to choose from is a 400 on every
    # strict upstream (OpenAI, 9router, Muse). Keep plain "auto" — it is a
    # no-op without tools — and drop anything that names a tool.
    if not payload.get("tools") and "tool_choice" in payload:
        choice = payload.get("tool_choice")
        keep = choice == "auto" or (isinstance(choice, dict) and choice.get("tool_choice") == "auto")
        if not keep:
            payload.pop("tool_choice", None)
            payload.pop("parallel_tool_calls", None)
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

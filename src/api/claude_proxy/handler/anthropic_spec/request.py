"""Anthropic request parameter helpers."""

from typing import Any, Dict, List



def extract_sampling_params(body: Dict[str, Any]) -> Dict[str, Any]:
    """Pull Anthropic sampling params out of the request body.

    Gemini accepts stop_sequences / top_p / top_k but the facade never received
    them, so every one of these was silently dropped before.

    Returns a dict suitable for spreading into `acompletion` kwargs.
    """
    out: Dict[str, Any] = {}

    stops = body.get("stop_sequences")
    if isinstance(stops, list):
        cleaned = [str(s) for s in stops if isinstance(s, str) and s]
        if cleaned:
            out["stop_sequences"] = cleaned

    top_p = body.get("top_p")
    if isinstance(top_p, (int, float)) and not isinstance(top_p, bool):
        out["top_p"] = max(0.0, min(1.0, float(top_p)))

    top_k = body.get("top_k")
    if isinstance(top_k, int) and not isinstance(top_k, bool) and top_k > 0:
        out["top_k"] = top_k

    # tool_choice rides the same channel. It was previously read only to strip
    # the tools list on `none`; nothing ever told the provider which mode the
    # client asked for, so `any` and a forced tool were both no-ops.
    choice = extract_tool_choice(body)
    if choice:
        out.update(choice)

    # thinking travels verbatim. Anthropic is mid-migration between
    # {"type":"enabled","budget_tokens":N} and {"type":"adaptive"}, and each form
    # is a 400 on models that support the other one. Deciding here would mean
    # guessing which model a third-party endpoint runs, so the field is carried
    # unchanged and each target decides for itself.
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type"):
        out["thinking"] = thinking

    return out

def extract_tool_choice(body: Dict[str, Any]) -> Dict[str, Any]:
    """Translate Anthropic `tool_choice` into the canonical OpenAI spelling.

    OpenAI is the internal shape, so this converts rather than passing through:
    Anthropic says `any` where OpenAI says `required`, and names a tool with
    `{"type": "tool", "name": ...}` where OpenAI nests it under `function`.
    Forwarding the Anthropic spelling is a 400 on every other provider.

    Anthropic values:
      {"type": "auto"}                       -> "auto"
      {"type": "any"}                        -> "required"
      {"type": "none"}                       -> drop tools entirely
      {"type": "tool", "name": "X"}          -> forced X
    """
    tc = body.get("tool_choice")
    if not isinstance(tc, dict):
        return {}

    kind = tc.get("type")
    disable_parallel = bool(tc.get("disable_parallel_tool_use"))
    out: Dict[str, Any] = {}

    if kind == "auto":
        out["tool_choice"] = "auto"
    elif kind == "any":
        out["tool_choice"] = "required"
    elif kind == "none":
        out["tool_choice"] = "none"
    elif kind == "tool":
        name = tc.get("name")
        if isinstance(name, str) and name:
            out["tool_choice"] = {"type": "function", "function": {"name": name}}
    else:
        return {}

    # Anthropic spells it inside tool_choice; OpenAI has a separate flag, and
    # Gemini has neither. Carrying it as a sibling lets each target translate
    # into whatever it does have.
    if disable_parallel:
        out["parallel_tool_calls"] = False
    return out

def apply_tool_choice_to_tools(tool_choice: Dict[str, Any], tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`tool_choice: none` means the model must not see tools at all."""
    if tool_choice.get("tool_choice") == "none":
        return []
    return tools


"""Thinking config builder for Gemini API — direct mapping, no liteLLM.

9router pattern (openai-to-gemini.js:233-248):
  Map reasoning_effort → thinkingConfig.thinkingLevel (not thinkingBudget).
  V3 uses thinkingLevel enum: minimal/low/medium/high.
  V2.5 uses thinkingBudget: token count.
"""

from typing import Any, Dict, Optional


def build_thinking_config(
    thinking_level: Optional[str] = None,
    thinking_budget: Optional[int] = None,
    include_thoughts: bool = True,
    is_v3: bool = True,
) -> Dict[str, Any]:
    """Build thinkingConfig dict for Gemini HTTP request body.

    V3: {"thinking_level": "low", "include_thoughts": true}
    V2.5: {"thinking_budget": 4096, "include_thoughts": true}

    Returns {} if no thinking (use API default).
    """
    if thinking_level is not None:
        level = str(thinking_level).lower().strip()
        if level in ("none", "off", "false"):
            if is_v3:
                return {"thinking_level": "minimal", "include_thoughts": False}
            return {"include_thoughts": False}
        if is_v3:
            return {"thinking_level": level, "include_thoughts": level != "minimal"}
        budget_map = {"low": 1024, "medium": 2048, "high": 4096}
        return {"thinking_budget": budget_map.get(level, 2048), "include_thoughts": True}

    if thinking_budget is not None:
        if is_v3:
            return {"thinking_level": "medium", "include_thoughts": True}
        return {"thinking_budget": thinking_budget, "include_thoughts": include_thoughts}

    if not include_thoughts:
        return {"include_thoughts": False}

    return {}


def get_default_thinking_for_model(model_id: str) -> Dict[str, Any]:
    """Auto-enable thinking with level appropriate for model.

    - V3 flash: low (fast tool calls, enough reasoning)
    - V3 pro: medium
    - V2.5: budget 8192
    - Lite: {} (no thinking support)
    """
    m = model_id.lower()
    if "lite" in m:
        return {}

    is_v3 = "gemini-3" in m and "gemini-2" not in m

    if is_v3:
        if "flash" in m and "pro" not in m:
            return build_thinking_config(thinking_level="low", is_v3=True)
        return build_thinking_config(thinking_level="medium", is_v3=True)

    return {"thinking_budget": 8192, "include_thoughts": True}

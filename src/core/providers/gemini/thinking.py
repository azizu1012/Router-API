"""Thinking config builder for Gemini API — direct mapping, no liteLLM.

9router pattern (openai-to-gemini.js:233-248):
  Map reasoning_effort → thinkingConfig.thinkingLevel (not thinkingBudget).
  V3 uses thinkingLevel enum: minimal/low/medium/high.
  V2.5 uses thinkingBudget: token count.
  Gemma 4 strictly supports toggling thinking ON ("high") or OFF ("minimal").
  Gemma 4 does NOT support thinkingBudget (throws 400) or low/medium (throws 400).
"""

from typing import Any, Dict, Optional


def sanitize_thinking_for_gemma(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Sanitize thinkingConfig for Gemma 4.

    Gemma 4 on Google AI Studio strictly supports ONLY thinkingLevel 'high' (enabled)
    or 'minimal' (disabled). It rejects thinkingBudget and low/medium levels with HTTP 400.
    """
    if not config:
        return {}

    level = config.get("thinking_level")
    budget = config.get("thinking_budget")
    inc = config.get("include_thoughts")

    if level is not None:
        lvl = str(level).lower().strip()
        if lvl in ("none", "off", "false", "minimal"):
            return {"thinking_level": "minimal", "include_thoughts": False}

    if budget is not None and int(budget) == 0:
        return {"thinking_level": "minimal", "include_thoughts": False}

    if inc is False and level is None and budget is None:
        return {"thinking_level": "minimal", "include_thoughts": False}

    # Enabled thinking on Gemma 4: must use thinking_level="high"
    return {
        "thinking_level": "high",
        "include_thoughts": inc if inc is not None else True,
    }


def build_thinking_config(
    thinking_level: Optional[str] = None,
    thinking_budget: Optional[int] = None,
    include_thoughts: bool = True,
    is_v3: bool = True,
    is_gemma: bool = False,
) -> Dict[str, Any]:
    """Build thinkingConfig dict for Gemini / Gemma HTTP request body.

    V3: {"thinking_level": "low", "include_thoughts": true}
    V2.5: {"thinking_budget": 4096, "include_thoughts": true}
    Gemma 4: {"thinking_level": "high", "include_thoughts": true} or minimal

    Returns {} if no thinking (use API default).
    """
    if is_gemma:
        if thinking_level is not None:
            lvl = str(thinking_level).lower().strip()
            if lvl in ("none", "off", "false", "minimal"):
                return {"thinking_level": "minimal", "include_thoughts": False}
            return {"thinking_level": "high", "include_thoughts": lvl != "minimal" and include_thoughts}
        if thinking_budget is not None:
            if int(thinking_budget) == 0:
                return {"thinking_level": "minimal", "include_thoughts": False}
            return {"thinking_level": "high", "include_thoughts": include_thoughts}
        if not include_thoughts:
            return {"thinking_level": "minimal", "include_thoughts": False}
        return {"thinking_level": "high", "include_thoughts": True}

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
    - Gemma 4: high (strictly "high" or "minimal")
    - V2.5: budget 8192
    - Lite: {} (no thinking support)
    """
    m = model_id.lower()
    if "lite" in m:
        return {}

    if "gemma" in m:
        return {"thinking_level": "high", "include_thoughts": True}

    is_v3 = "gemini-3" in m and "gemini-2" not in m

    if is_v3:
        if "flash" in m and "pro" not in m:
            return build_thinking_config(thinking_level="low", is_v3=True)
        return build_thinking_config(thinking_level="medium", is_v3=True)

    return {"thinking_budget": 8192, "include_thoughts": True}


def resolve_thinking_config(
    model_id: str,
    thinking_level: Optional[str] = None,
    thinking_budget: Optional[int] = None,
    include_thoughts: Optional[bool] = None,
) -> Dict[str, Any]:
    """Unified helper to build thinking config dictionary for a model, handling defaults and lite logic."""
    m = model_id.lower()

    if "lite" in m:
        return {}

    is_gemma = "gemma" in m
    is_v3 = "gemini-3" in m and "gemini-2" not in m

    # If any specific thinking parameter is provided, use build_thinking_config
    if thinking_level is not None or thinking_budget is not None or include_thoughts is not None:
        inc = include_thoughts if include_thoughts is not None else True
        return build_thinking_config(
            thinking_level=thinking_level,
            thinking_budget=thinking_budget,
            include_thoughts=inc,
            is_v3=is_v3,
            is_gemma=is_gemma,
        )

    # Otherwise, return default thinking for the model
    return get_default_thinking_for_model(model_id)


from typing import Dict, Any, Optional
from src.core.providers.genai_types import types


def build_thinking_config(
    model_id: str,
    thinking_level: Optional[str] = None,
    thinking_budget: Optional[int] = None,
    include_thoughts: Optional[bool] = None,
) -> Optional[types.ThinkingConfig]:
    """Create ThinkingConfig per actual model_id.

    V3.x uses ``thinking_level`` (minimal/low/medium/high).
    V2.5 uses ``thinking_budget`` (0 = off, -1 = dynamic, or token count).

    Returns ``None`` when no thinking params given — each model
    uses its API default.
    """
    if thinking_level is None and thinking_budget is None:
        return None

    is_v3 = "gemini-3" in model_id
    kwargs: Dict[str, Any] = {}

    if is_v3:
        # V3 uses thinking_level (minimal/low/medium/high)
        if thinking_level is not None:
            kwargs["thinking_level"] = thinking_level
        else:
            kwargs["thinking_level"] = "medium"
    else:
        # V2.5: Map -1 (dynamic/max) or None to the model's max budget:
        # - gemini-2.5-pro: 32768
        # - gemini-2.5-flash / flash-lite: 24576
        m_lower = model_id.lower()
        
        budget = thinking_budget
        if thinking_level is not None and budget is None:
            budget = -1

        if budget == -1 or budget is None:
            if "pro" in m_lower:
                budget = 32768
            else:
                budget = 24576  # Max for gemini-2.5-flash and gemini-2.5-flash-lite
                
        kwargs["thinking_budget"] = budget

    if include_thoughts is not None:
        kwargs["include_thoughts"] = include_thoughts

    return types.ThinkingConfig(**kwargs)

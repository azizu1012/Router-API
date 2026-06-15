from typing import Any, Dict, List

from src.core.config_n_logg import config
from src.logical_HQ_translator import _emergency_truncate_to_limit


async def _pre_compact_and_truncate(
    body: Dict[str, Any], openai_messages: List[Dict[str, Any]],
    openai_tools: List[Dict[str, Any]], model_alias: str
) -> None:
    is_lite = "lite" in str(model_alias).lower()
    limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
    openai_messages[:] = _emergency_truncate_to_limit(openai_messages, limit)

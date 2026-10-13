from typing import Any, Dict, List

from src.core.config_n_logg import config
from src.logical_HQ_translator import _emergency_truncate_to_limit


async def _pre_compact_and_truncate(
    body: Dict[str, Any], openai_messages: List[Dict[str, Any]],
    openai_tools: List[Dict[str, Any]], model_alias: str
) -> None:
    is_lite = "lite" in str(model_alias).lower()
    emergency_limit = (
        config.LITE_EMERGENCY_MAX_INPUT_TOKENS
        if is_lite
        else config.EMERGENCY_MAX_INPUT_TOKENS
    )
    # Keep headroom for the provider's real tokenizer and for a hosted search
    # result appended during a recursive chat turn. The old 200k character
    # estimate could become 225k+ provider tokens before the next request.
    limit = min(emergency_limit, config.COMPACTION_TARGET_LIMIT)
    openai_messages[:] = _emergency_truncate_to_limit(openai_messages, limit)

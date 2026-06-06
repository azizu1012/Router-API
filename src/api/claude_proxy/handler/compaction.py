import asyncio
from typing import Any, Dict, List

import litellm
from src.core.config_n_logg import config
from src.api.claude_proxy.utils import should_compact, _compact_conversation, _emergency_truncate_to_limit

async def _pre_compact_and_truncate(
    body: Dict[str, Any], openai_messages: List[Dict[str, Any]],
    openai_tools: List[Dict[str, Any]], model_alias: str
) -> None:
    try:
        input_tokens = await asyncio.to_thread(litellm.token_counter, model="gemini-1.5-flash", messages=openai_messages)
    except Exception:
        input_tokens = max(1, len(str(openai_messages)) // 4)

    if should_compact(openai_messages, input_tokens):
        openai_messages[:] = await _compact_conversation(body, openai_messages, openai_tools, input_tokens)
        try:
            input_tokens = await asyncio.to_thread(litellm.token_counter, model="gemini-1.5-flash", messages=openai_messages)
        except Exception:
            input_tokens = max(1, len(str(openai_messages)) // 4)

    # Failsafe emergency truncation
    is_lite = "lite" in str(model_alias).lower()
    limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
    openai_messages[:] = _emergency_truncate_to_limit(openai_messages, limit)

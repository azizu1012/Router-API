import asyncio
from typing import Any, Dict, List

import litellm

litellm.suppress_debug_info = True

model_list = litellm.model_list

__all__ = [
    "acompletion",
    "token_counter",
    "register_models",
    "model_list",
]


async def acompletion(**kwargs) -> Any:
    return await litellm.acompletion(**kwargs)


async def token_counter(model: str, messages: List[Dict[str, Any]]) -> int:
    return await asyncio.to_thread(litellm.token_counter, model=model, messages=messages)


def register_models(models: List[str]) -> None:
    for m in models:
        if m and m not in litellm.model_list:
            litellm.model_list.append(m)

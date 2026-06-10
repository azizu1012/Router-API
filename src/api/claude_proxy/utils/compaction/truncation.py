"""Emergency truncation — drop oldest messages when token limit is exceeded.

Keeps all system messages and the most recent chat messages,
ensuring the first retained message has role ``user``.
"""

from typing import Any, Dict, List

from src.core.config_n_logg.logger import logger_proxy as logger


def emergency_truncate_to_limit(messages: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Drop oldest chat messages until total estimated tokens ≤ ``limit``.

    System messages are always preserved.
    """
    def _size(msgs: List[Dict[str, Any]]) -> int:
        s = 0
        for m in msgs:
            c = m.get("content", "")
            if isinstance(c, str):
                s += len(c)
            elif isinstance(c, list):
                s += sum(len(item.get("text", "") or "") for item in c if isinstance(item, dict))
            else:
                s += len(str(c))
        return s // 4

    if _size(messages) <= limit:
        return messages

    logger.warning("[Emergency] Messages exceed limit (%d), truncating...", limit)

    system_msgs = [m for m in messages if m.get("role") == "system"]
    chat_msgs = [m for m in messages if m.get("role") != "system"]

    if not chat_msgs:
        return system_msgs

    sys_size = _size(system_msgs)
    accumulated = sys_size
    split_idx = len(chat_msgs) - 1

    for idx in range(len(chat_msgs) - 1, -1, -1):
        c = chat_msgs[idx].get("content", "")
        if isinstance(c, str):
            msg_size = len(c)
        elif isinstance(c, list):
            msg_size = sum(len(item.get("text", "") or "") for item in c if isinstance(item, dict))
        else:
            msg_size = len(str(c))
        msg_size = msg_size // 4

        if accumulated + msg_size > limit:
            split_idx = idx + 1 if idx < len(chat_msgs) - 1 else len(chat_msgs) - 1
            break
        accumulated += msg_size
    else:
        split_idx = 0

    while split_idx > 0 and chat_msgs[split_idx].get("role") != "user":
        split_idx -= 1

    return system_msgs + chat_msgs[split_idx:]

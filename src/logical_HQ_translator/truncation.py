"""Emergency truncation — drop oldest messages when token limit is exceeded.

Keeps all system messages and the most recent chat messages,
ensuring the first retained message has role ``user``.
"""

from typing import Any, Dict, List

from src.core.config_n_logg.logger import logger_proxy as logger


def emergency_truncate_to_limit(messages: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Prune intermediate chat messages from the middle, preserving 
    System messages, the first User message (original task goal), and 
    the most recent chat history to stay under `limit`.
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

    logger.warning("[Emergency] Messages exceed limit (%d), executing middle-out truncation...", limit)

    system_msgs = [m for m in messages if m.get("role") == "system"]
    chat_msgs = [m for m in messages if m.get("role") != "system"]

    if not chat_msgs:
        return system_msgs

    # Preserve first user message (original task goal) if present
    first_user_msg = None
    start_idx = 0
    if chat_msgs and chat_msgs[0].get("role") == "user":
        first_user_msg = chat_msgs[0]
        start_idx = 1

    sys_size = _size(system_msgs)
    goal_size = _size([first_user_msg]) if first_user_msg else 0
    accumulated = sys_size + goal_size
    split_idx = len(chat_msgs) - 1

    # Loop backward down to start_idx to find where limit is exceeded
    for idx in range(len(chat_msgs) - 1, start_idx - 1, -1):
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
        split_idx = start_idx

    recent_msgs = chat_msgs[split_idx:]

    # Fallback to at least the last message if everything was pruned
    if not recent_msgs and len(chat_msgs) > start_idx:
        recent_msgs = [chat_msgs[-1]]

    # Ensure alternating roles and insert notice
    final_chat = []
    if first_user_msg:
        first_user_msg_copy = dict(first_user_msg)
        notice = "\n\n[... Một số lượt hội thoại trung gian đã được Proxy lược bớt để tối ưu hóa context tránh lỗi 429 TPM ...]\n\n"
        content = first_user_msg_copy.get("content")
        if isinstance(content, str):
            first_user_msg_copy["content"] = content + notice
        elif isinstance(content, list):
            text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
            if text_blocks:
                text_blocks[0]["text"] = text_blocks[0].get("text", "") + notice
            else:
                content.append({"type": "text", "text": notice})

        final_chat.append(first_user_msg_copy)

        if recent_msgs:
            if recent_msgs[0].get("role") == "user":
                dummy_assistant = {
                    "role": "assistant",
                    "content": "[Proxy: Truncated intermediate tool outputs and log history to fit Gemini TPM limits]"
                }
                final_chat.append(dummy_assistant)
            final_chat.extend(recent_msgs)
    else:
        final_chat.extend(recent_msgs)

    return system_msgs + final_chat

"""Emergency truncation — drop oldest messages when token limit is exceeded.

Keeps all system messages and the most recent chat messages,
ensuring the first retained message has role ``user``.
"""

import copy
import os
from typing import Any, Dict, List

from src.core.config_n_logg.logger import logger_proxy as logger


def _is_tool_result_msg(msg: Dict[str, Any]) -> bool:
    """Match the same role spelling that anthropic_wire uses to build tool_result blocks."""
    role = str(msg.get("role") or "")
    return role == "tool" or role.startswith("tool")


def _drop_orphaned_tool_results(
    msgs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Remove tool results whose ``tool_use`` was pruned along with the middle.

    ``split_idx`` is a message boundary, not a tool-exchange boundary, so it can
    land between an assistant's ``tool_calls`` and the ``role: "tool"`` messages
    that answer them. Keeping only the tail then produces a ``tool_result`` with
    no ``tool_use`` behind it.

    The converter already guards this case for orphans present in the *client's*
    request (see ``converter.py``), but this truncation runs after conversion, so
    it creates an orphan the converter never gets a chance to see. Reassembling
    the pair here would exceed the budget this function exists to enforce, so
    the result is dropped instead — which is what the injected notice already
    tells the model happened.

    A dangling ``tool_use`` cannot occur the same way: ``recent_msgs`` is a
    contiguous suffix, so an assistant's tool_calls survives only when the tool
    messages answering it survive too.
    """
    answered = set()
    for m in msgs:
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                if isinstance(tc, dict) and tc.get("id"):
                    answered.add(str(tc["id"]))

    kept = []
    for m in msgs:
        if _is_tool_result_msg(m) and str(m.get("tool_call_id") or "") not in answered:
            logger.warning(
                "[Emergency] dropped a tool_result whose tool_use was truncated (id=%s)",
                m.get("tool_call_id"),
            )
            continue
        kept.append(m)
    return kept


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

    recent_msgs = _drop_orphaned_tool_results(recent_msgs)

    # Ensure alternating roles and insert notice
    final_chat = []
    if first_user_msg:
        notice = "\n\n[... Một số lượt hội thoại trung gian đã được Proxy lược bớt để tối ưu hóa context tránh lỗi 429 TPM ...]\n\n"
        # Copy the blocks, not just the message. A shallow `dict()` copy leaves
        # `content` shared with the caller's list, so writing the notice into the
        # first text block below mutated the caller's message in place. This
        # function runs twice over the same body — once for the opening turn and
        # again for the recursive turn after a hosted search — so the notice
        # compounded, and the caller's history was quietly rewritten.
        first_user_msg_copy = dict(first_user_msg)
        content = first_user_msg.get("content")
        if isinstance(content, str):
            first_user_msg_copy["content"] = content + notice
        elif isinstance(content, list):
            copied = [dict(b) if isinstance(b, dict) else b for b in content]
            first_user_msg_copy["content"] = copied
            text_blocks = [b for b in copied
                           if isinstance(b, dict) and b.get("type") == "text"]
            if text_blocks:
                text_blocks[0]["text"] = text_blocks[0].get("text", "") + notice
            else:
                copied.append({"type": "text", "text": notice})

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


def trim_old_tool_results_softly(
    messages: List[Dict[str, Any]],
    limit: int | None = None,
    max_overage_ratio: float = 0.20,
    max_output_tokens: int = 0,
) -> List[Dict[str, Any]]:
    """Shrink old tool output without deleting the tool-call conversation.

    This is deliberately a small safety valve for agent turns: preserve message
    and tool-call structure, replace only the oldest tool-result bodies, and
    leave requests that are too large for the client to compact itself.
    """
    # This guard follows the per-key TPM budget, not the client-advertised
    # context-window metadata (which may intentionally be lower).
    budget = int(limit or os.getenv("SOFT_TOOL_RESULT_TRIM_LIMIT", "250000")) - max(0, int(max_output_tokens))
    if budget <= 0:
        return messages

    def size(items: List[Dict[str, Any]]) -> int:
        return len(str(items)) // 4

    current = size(messages)
    if current <= budget or current > int(budget * (1.0 + max_overage_ratio)):
        return messages

    trimmed = copy.deepcopy(messages)
    marker = "[older tool output trimmed; client context remains authoritative]"
    for message in trimmed:
        if str(message.get("role") or "") != "tool":
            continue
        content = message.get("content")
        if isinstance(content, str) and len(content) > len(marker):
            message["content"] = marker
        elif isinstance(content, list):
            message["content"] = [{"type": "text", "text": marker}]
        if size(trimmed) <= budget:
            return trimmed
    return trimmed if size(trimmed) <= budget else messages


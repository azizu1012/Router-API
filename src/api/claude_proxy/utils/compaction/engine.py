"""Dynamic compaction engine — summarises conversation history via Gemini Lite.

Splits conversation into history (to compact) and recent (to keep),
calls ``gemini-flash-lite`` to merge the existing ``progress_report.md``
with the latest actions, then replaces the history with the summary.
"""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import litellm

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router

from .gate import _detect_claude_code
from .truncation import emergency_truncate_to_limit
from .workspace import find_workspace_roots
from ..model_resolver import _resolve_model
from ..sse_cache_agent import (
    _estimate_msg_tokens,
    _truncate_huge_message,
    is_sub_agent_body,
)


async def compact_conversation(
    body: Dict[str, Any],
    openai_messages: List[Dict[str, Any]],
    openai_tools: List[Dict[str, Any]],
    input_tokens: int,
    retry_attempt: int = 0,
) -> List[Dict[str, Any]]:
    """Compact conversation history by merging into a progress report.

    1. Splits messages into system + history (to compact) + recent (to keep).
    2. Reads existing ``progress_report.md`` from workspace root.
    3. Calls ``gemini-flash-lite`` to merge history into the report.
    4. Writes updated report to disk.
    5. Returns compacted messages (system + summary block + recent).

    Falls back to simple truncation on any failure.
    """
    is_claude_code = _detect_claude_code(openai_messages)
    target_limit = config.CLAUDE_CODE_COMPACTION_TARGET_LIMIT if is_claude_code else config.COMPACTION_TARGET_LIMIT

    logger.warning(
        "[Compact] Input=%d > threshold. Compacting (claude_code=%s, target=%d).",
        input_tokens, is_claude_code, target_limit,
    )

    try:
        system_msgs = [m for m in openai_messages if m.get("role") == "system"]
        chat_msgs = [m for m in openai_messages if m.get("role") != "system"]
        chat_msgs = [_truncate_huge_message(m) for m in chat_msgs]

        history_msgs, recent_msgs = _split_conversation(system_msgs, chat_msgs, target_limit, is_claude_code)

        if not history_msgs:
            logger.info("[Compact] All messages fit in safe window. No compaction needed.")
            return system_msgs + recent_msgs

        logger.info(
            "[Compact] Keeping %d recent (~%d tok), compacting %d historical.",
            len(recent_msgs), _sum_tokens(recent_msgs), len(history_msgs),
        )

        progress_content, progress_path = await _read_progress_report(openai_messages, history_msgs)

        summary_text = await _merge_via_lite_model(body, history_msgs, progress_content, progress_path)

        if not summary_text:
            logger.warning("[Compact] LLM merge failed. Keeping existing history.")
            return system_msgs + history_msgs + recent_msgs

        if progress_path:
            _write_progress_report(progress_path, summary_text)

        compacted = _build_compacted(system_msgs, summary_text, recent_msgs)
        compacted = _inject_context_warning(compacted, input_tokens, is_claude_code, body)

        final_tokens = sum(_estimate_msg_tokens(m) for m in compacted)
        logger.warning("[Compact] Complete: %d→%d msgs (%d tok)", len(openai_messages), len(compacted), final_tokens)

        return emergency_truncate_to_limit(compacted, config.EMERGENCY_MAX_INPUT_TOKENS)

    except Exception as exc:
        logger.error("[Compact] Engine crashed: %s. Falling back to truncation.", exc, exc_info=True)
        return _fallback_truncation(openai_messages, system_msgs if 'system_msgs' in dir() else [],
                                    chat_msgs if 'chat_msgs' in dir() else [], is_claude_code, input_tokens, body)


# ── Internal helpers ────────────────────────────────────────────


def _split_conversation(
    system_msgs: List[Dict], chat_msgs: List[Dict],
    target_limit: int, is_claude_code: bool,
) -> Tuple[List[Dict], List[Dict]]:
    """Split chat messages into history and recent windows.

    Recent window is ≲ ``target_limit`` tokens and at most 10 user turns.
    Returns ``(history_msgs, recent_msgs)``.
    """
    sys_tokens = _sum_tokens(system_msgs)
    accumulated = sys_tokens
    split_idx = 0
    user_turns = 0

    for idx in range(len(chat_msgs) - 1, -1, -1):
        m = chat_msgs[idx]
        msg_tokens = _estimate_msg_tokens(m)
        if m.get("role") == "user":
            user_turns += 1
        if (accumulated + msg_tokens > target_limit or user_turns > 10) and idx < len(chat_msgs) - 1:
            split_idx = idx + 1
            break
        accumulated += msg_tokens

    while split_idx > 0 and chat_msgs[split_idx].get("role") != "user":
        split_idx -= 1

    return chat_msgs[:split_idx], chat_msgs[split_idx:]


async def _read_progress_report(
    openai_messages: List[Dict], history_msgs: List[Dict],
) -> Tuple[str, Optional[Path]]:
    """Locate and read ``progress_report.md`` from workspace roots."""
    workspace_roots = find_workspace_roots(openai_messages)
    logger.info("[Compact] Workspace roots: %s", [str(r) for r in workspace_roots])

    progress_content = ""
    resolved_path: Optional[Path] = None

    for root in workspace_roots:
        p = root / "progress_report.md"
        try:
            if p.exists() and p.is_file():
                logger.info("[Compact] Found progress report at: %s", p)
                with open(p, "r", encoding="utf-8") as f:
                    progress_content = f.read().strip()
                resolved_path = p
                break
        except Exception as e:
            logger.warning("[Compact] Error reading %s: %s", p, e)

    if not resolved_path and workspace_roots:
        resolved_path = workspace_roots[0] / "progress_report.md"
        logger.info("[Compact] No existing report. Target path: %s", resolved_path)

    return progress_content, resolved_path


def _build_merge_prompt(history_msgs: List[Dict], progress_content: str) -> str:
    """Build the LLM prompt that merges conversation history into a progress report."""
    history_text = _format_history(history_msgs)

    if progress_content:
        return (
            "You are an expert developer and project manager tracking codebase progress.\n"
            "Your task is to update the coding session's progress report (`progress_report.md`) by merging "
            "the existing progress report with the latest actions taken in the chat history.\n\n"
            "### Existing progress_report.md Content:\n"
            "```markdown\n"
            f"{progress_content}\n"
            "```\n\n"
            "### Recent Conversation History to Compact:\n"
            "```text\n"
            f"{history_text}\n"
            "```\n\n"
            "### Guidelines:\n"
            "1. Focus ONLY on concrete updates: which files were modified, what specific changes were made, and what needs to be done next.\n"
            "2. Maintain the report in markdown format. It MUST contain:\n"
            "   - **Completed Tasks & Code Changes**: List files modified, showing functions/areas updated and a 1-line summary.\n"
            "   - **Current Status**: Brief summary of the system state.\n"
            "   - **Next Actions / Todo**: Checklist of remaining tasks.\n"
            "3. Keep details factual, concise, and lose no technical details. Avoid placeholders.\n"
            "4. DO NOT copy large blocks of code. Keep code snippets minimal (1-3 lines max).\n"
            "5. Output ONLY the updated markdown content of the progress report, with no preamble.\n"
        )
    else:
        return (
            "You are an expert developer and project manager tracking codebase progress.\n"
            "Your task is to generate a new progress report (`progress_report.md`) based on the actions taken in the conversation history.\n\n"
            "### Conversation History to Compact:\n"
            "```text\n"
            f"{history_text}\n"
            "```\n\n"
            "### Guidelines:\n"
            "1. Focus ONLY on concrete updates: which files were modified, what specific changes were made, and what needs to be done next.\n"
            "2. Structure the report in markdown format. It MUST contain:\n"
            "   - **Completed Tasks & Code Changes**: List files modified, showing functions/areas updated and a 1-line summary.\n"
            "   - **Current Status**: Brief summary of where the project stands.\n"
            "   - **Next Actions / Todo**: Checklist of remaining tasks.\n"
            "3. Keep details factual, concise, and lose no technical details. Avoid placeholders.\n"
            "4. DO NOT copy large blocks of code. Keep code snippets minimal (1-3 lines max).\n"
            "5. Output ONLY the markdown content of the progress report, with no preamble.\n"
        )


def _format_history(history_msgs: List[Dict]) -> str:
    """Flatten history messages into a compact text representation."""
    lines: List[str] = []
    for m in history_msgs:
        role = m.get("role", "unknown")
        content = m.get("content", "")

        if isinstance(content, list):
            extracted = [str(c.get("text", "")) for c in content if isinstance(c, dict) and c.get("type") == "text"]
            content = " ".join(extracted) if extracted else str(content)

        tc = m.get("tool_calls")
        if tc:
            for t in tc:
                fn = t.get("function", {})
                lines.append(f"[{role} called tool: {fn.get('name', '?')}]")
        elif role == "tool":
            lines.append(f"[tool result for {m.get('name', 'unknown_tool')}]: {str(content)[:1000]}")
        else:
            lines.append(f"[{role}]: {str(content)[:1000]}")

    return "\n".join(lines)


async def _merge_via_lite_model(
    body: Dict[str, Any],
    history_msgs: List[Dict],
    progress_content: str,
    progress_path: Optional[Path],
) -> str:
    """Call ``gemini-flash-lite`` to produce the merged summary text.

    Retries up to 3 times with different keys on transient errors.
    Returns empty string on total failure.
    """
    summary_prompt = _build_merge_prompt(history_msgs, progress_content)

    est_lite_input = len(str(summary_prompt)) // 4
    if est_lite_input > config.LITE_EMERGENCY_MAX_INPUT_TOKENS:
        allowed = config.LITE_EMERGENCY_MAX_INPUT_TOKENS * 3
        summary_prompt = summary_prompt[:allowed] + "\n\n[... TRUNCATED ...]"
        est_lite_input = len(str(summary_prompt)) // 4

    summary_text = ""
    lite_key = None
    lite_mid = None

    for attempt in range(3):
        lite_key = None
        try:
            _, lite_mid, lite_key, lite_litellm, lite_reservation = await _resolve_model(
                body, "gemini-flash-lite", estimated_tokens=est_lite_input + 4096,
                retry_attempt=attempt,
            )
            if not lite_key:
                logger.warning("[Compact] No lite key on attempt %d", attempt)
                continue

            kwargs = {
                "model": lite_litellm,
                "messages": [{"role": "user", "content": summary_prompt}],
                "api_key": lite_key,
                "max_tokens": 4096,
                "temperature": 0.3,
                "stream": True,
                "request_timeout": 30,
            }
            gen = await litellm.acompletion(**kwargs)
            chunks: List[str] = []
            async for chunk in gen:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    chunks.append(chunk.choices[0].delta.content)
            summary_text = "".join(chunks).strip()

            if summary_text:
                break
            logger.warning("[Compact] Lite returned empty on attempt %d", attempt)

        except Exception as e:
            logger.warning("[Compact] Lite attempt %d failed: %s", attempt, e)
            if lite_key:
                from src.core.limits import apply_error_penalty
                router.freeze_key(lite_key, 15, lite_mid, "rate_limit")
                apply_error_penalty(lite_key, "rate_limit", lite_mid)
        finally:
            if lite_key:
                router.release_key(lite_key)

    if not summary_text and progress_content:
        logger.warning("[Compact] LLM merge failed, keeping existing progress report.")
        return progress_content

    return summary_text


def _write_progress_report(path: Path, summary_text: str) -> None:
    """Write the updated progress report to disk."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(summary_text)
        logger.info("[Compact] Updated progress report: %s", path)
    except Exception as e:
        logger.error("[Compact] Failed to write progress report: %s", e)


def _build_compacted(
    system_msgs: List[Dict], summary_text: str, recent_msgs: List[Dict],
) -> List[Dict]:
    """Assemble the compacted message list."""
    compacted = list(system_msgs)
    compacted.append({
        "role": "user",
        "content": (
            "[SYSTEM: Previous conversation history has been compacted to save tokens. "
            "Below is the active progress report of actions taken, modified files, and remaining tasks. "
            "Use this to maintain continuity and avoid repeating already completed work.]\n\n"
            + summary_text
        ),
    })
    compacted.extend(recent_msgs)
    return compacted


def _inject_context_warning(
    compacted: List[Dict], input_tokens: int, is_claude_code: bool, body: Dict[str, Any],
) -> List[Dict]:
    """If context is extremely full, inject a visible warning message."""
    warning_threshold = 178000 if is_claude_code else 170000
    if input_tokens <= warning_threshold:
        return compacted
    if is_sub_agent_body(body):
        return compacted

    warning_text = (
        f"\n\n[⚠️ SYSTEM WARNING: CONTEXT EXTREMELY FULL (>{warning_threshold // 1000}K tokens) ⚠️]\n"
        f"The conversation context is extremely large (over {warning_threshold // 1000}K tokens). "
        "You MUST start your response with a highly visible, bold warning message in the language of the conversation, "
        "reminding the user that context is almost full and urging them to run the '/compact' command.\n"
        f"Example: '**⚠️ CẢNH BÁO: Context đã đầy (>{warning_threshold // 1000}K tokens)! "
        "Vui lòng gõ lệnh `/compact` để làm sạch hội thoại.**'\n"
        "If you need to draw architecture diagrams, use ASCII art."
    )

    # Inject into last system message or first user message
    for msg in compacted:
        if msg.get("role") == "system" and isinstance(msg.get("content"), str):
            msg["content"] += warning_text
            return compacted

    for idx in range(len(compacted) - 1, -1, -1):
        if compacted[idx].get("role") == "user":
            u = compacted[idx]
            if isinstance(u.get("content"), str):
                u["content"] += warning_text
            elif isinstance(u.get("content"), list):
                u["content"].append({"type": "text", "text": warning_text})
            return compacted

    compacted.insert(0, {"role": "system", "content": warning_text})
    return compacted


def _fallback_truncation(
    openai_messages: List[Dict], system_msgs: List[Dict], chat_msgs: List[Dict],
    is_claude_code: bool, input_tokens: int, body: Dict[str, Any],
) -> List[Dict]:
    """Fallback when the compaction engine crashes."""
    emergency_limit = 100000 if is_claude_code else 150000
    sys_tokens = _sum_tokens(system_msgs)
    accumulated = sys_tokens
    split_idx = len(chat_msgs) - 1

    for idx in range(len(chat_msgs) - 1, -1, -1):
        m_tok = _estimate_msg_tokens(chat_msgs[idx])
        if accumulated + m_tok > emergency_limit:
            split_idx = idx + 1 if idx < len(chat_msgs) - 1 else len(chat_msgs) - 1
            break
        accumulated += m_tok
    else:
        split_idx = 0

    while split_idx > 0 and chat_msgs[split_idx].get("role") != "user":
        split_idx -= 1

    compacted = list(system_msgs)
    compacted.append({
        "role": "user",
        "content": "[SYSTEM NOTICE: Dynamic conversation compaction failed due to an upstream error. "
                   "Older conversation history has been truncated to protect context limits.]"
    })
    compacted.extend(chat_msgs[split_idx:])

    compacted = _inject_context_warning(compacted, input_tokens, is_claude_code, body)
    return emergency_truncate_to_limit(compacted, config.EMERGENCY_MAX_INPUT_TOKENS)


def _sum_tokens(msgs: List[Dict]) -> int:
    return sum(_estimate_msg_tokens(m) for m in msgs)

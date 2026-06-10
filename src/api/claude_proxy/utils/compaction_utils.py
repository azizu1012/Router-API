import asyncio
import re
from typing import Any, Dict, List

import litellm

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from .model_resolver import _resolve_model
from .sse_cache_agent import (
    _estimate_msg_tokens,
    _truncate_huge_message,
    is_sub_agent_body,
    _dict_to_sse_events,
)

def should_compact(messages: List[Dict[str, Any]], input_tokens: int, retry_attempt: int = 0) -> bool:
    system_prompt = ""
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content", "")
            if isinstance(content, list):
                content_str = " ".join(str(c) for c in content)
            else:
                content_str = str(content or "")
            system_prompt += content_str
            
    is_claude_code = False
    system_prompt_lower = system_prompt.lower()
    if (
        "you are claude code" in system_prompt_lower 
        or "cc_version=" in system_prompt_lower 
        or "claude-code" in system_prompt_lower
    ):
        is_claude_code = True
        
    threshold = (
        config.CLAUDE_CODE_COMPACTION_THRESHOLD 
        if is_claude_code 
        else config.COMPACTION_TOKEN_THRESHOLD
    )
    if retry_attempt >= 10:
        divisor = max(3, retry_attempt - 7)
        threshold = max(5000, threshold // divisor)
    return input_tokens > threshold

def _emergency_truncate_to_limit(messages: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    def _size(msgs):
        s = 0
        for m in msgs:
            c = m.get("content", "")
            if isinstance(c, str):
                s += len(c)
            elif isinstance(c, list):
                for item in c:
                    if isinstance(item, dict):
                        s += len(item.get("text", "") or "")
                    else:
                        s += len(str(item))
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

def find_workspace_roots(messages: List[Dict[str, Any]]) -> List[Any]:
    import json
    from pathlib import Path
    import os
    paths_found = []
    
    for m in messages:
        # Check tool calls
        tc = m.get("tool_calls")
        if tc:
            for t in tc:
                fn = t.get("function", {})
                args_str = fn.get("arguments", "")
                if args_str:
                    try:
                        args = json.loads(args_str)
                        for val in args.values():
                            if isinstance(val, str) and (":" in val or "/" in val or "\\" in val):
                                paths_found.append(val)
                    except Exception:
                        pass
        # Check content
        content = m.get("content", "")
        if isinstance(content, str) and content:
            matches_win = re.findall(r'[a-zA-Z]:\\[\\\w\s\.\-\(\)_]+', content)
            matches_unix = re.findall(r'/[/\w\s\.\-\(\)_]+', content)
            paths_found.extend(matches_win)
            paths_found.extend(matches_unix)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    txt = block.get("text", "")
                    matches_win = re.findall(r'[a-zA-Z]:\\[\\\w\s\.\-\(\)_]+', txt)
                    matches_unix = re.findall(r'/[/\w\s\.\-\(\)_]+', txt)
                    paths_found.extend(matches_win)
                    paths_found.extend(matches_unix)

    candidate_dirs = set()
    for p_str in paths_found:
        try:
            p_str_clean = p_str.strip().rstrip("\\/")
            if not p_str_clean:
                continue
            p = Path(p_str_clean)
            if p.is_absolute():
                candidate_dirs.add(p.parent if p.is_file() else p)
                for parent in p.parents:
                    candidate_dirs.add(parent)
        except Exception:
            pass

    marked_roots = []
    other_roots = []
    for d in candidate_dirs:
        try:
            if d.exists() and d.is_dir():
                is_root = False
                for marker in [".git", "package.json", "requirements.txt", ".env", "tsconfig.json"]:
                    if (d / marker).exists():
                        is_root = True
                        break
                if is_root:
                    marked_roots.append(d)
                else:
                    other_roots.append(d)
        except Exception:
            pass

    seen = set()
    result = []
    for r in marked_roots + other_roots:
        try:
            resolved = str(r.resolve())
            if resolved not in seen:
                seen.add(resolved)
                result.append(r)
        except Exception:
            pass
            
    try:
        cwd = Path(os.getcwd()).resolve()
        if str(cwd) not in seen:
            result.append(cwd)
    except Exception:
        pass
        
    try:
        proj_root = Path(config.PROJECT_ROOT).resolve()
        if str(proj_root) not in seen:
            result.append(proj_root)
    except Exception:
        pass

    return result

async def _compact_conversation(
    body: Dict[str, Any],
    openai_messages: List[Dict[str, Any]],
    openai_tools: List[Dict[str, Any]],
    input_tokens: int,
    retry_attempt: int = 0,
) -> List[Dict[str, Any]]:
    system_prompt = ""
    for m in openai_messages:
        if m.get("role") == "system":
            content = m.get("content", "")
            if isinstance(content, list):
                content_str = " ".join(str(c) for c in content)
            else:
                content_str = str(content or "")
            system_prompt += content_str
            
    is_claude_code = False
    system_prompt_lower = system_prompt.lower()
    if (
        "you are claude code" in system_prompt_lower 
        or "cc_version=" in system_prompt_lower 
        or "claude-code" in system_prompt_lower
    ):
        is_claude_code = True

    threshold = (
        config.CLAUDE_CODE_COMPACTION_THRESHOLD 
        if is_claude_code 
        else config.COMPACTION_TOKEN_THRESHOLD
    )
    target_limit = (
        config.CLAUDE_CODE_COMPACTION_TARGET_LIMIT 
        if is_claude_code 
        else config.COMPACTION_TARGET_LIMIT
    )
    if retry_attempt >= 10:
        divisor = max(3, retry_attempt - 7)
        threshold = max(5000, threshold // divisor)
        target_limit = max(3000, target_limit // (divisor - 1 if divisor > 3 else 2))

    logger.warning(
        "[Compact] Input tokens=%d exceeds threshold=%d. Initiating dynamic compaction (is_claude_code=%s).",
        input_tokens, threshold, is_claude_code
    )

    try:
        system_msgs = [m for m in openai_messages if m.get("role") == "system"]
        chat_msgs = [m for m in openai_messages if m.get("role") != "system"]

        chat_msgs = [_truncate_huge_message(m) for m in chat_msgs]

        sys_tokens = sum(_estimate_msg_tokens(m) for m in system_msgs)
        accumulated_tokens = sys_tokens
        split_idx = 0
        user_turns_seen = 0
        
        for idx in range(len(chat_msgs) - 1, -1, -1):
            m = chat_msgs[idx]
            msg_tokens = _estimate_msg_tokens(m)
            if m.get("role") == "user":
                user_turns_seen += 1
            if (accumulated_tokens + msg_tokens > target_limit or user_turns_seen > 10) and idx < len(chat_msgs) - 1:
                split_idx = idx + 1
                break
            accumulated_tokens += msg_tokens

        while split_idx > 0 and chat_msgs[split_idx].get("role") != "user":
            split_idx -= 1

        recent_msgs = chat_msgs[split_idx:]
        history_msgs = chat_msgs[:split_idx]

        if not history_msgs:
            logger.info("[Compact] All messages fit in the safe window. No compaction needed after truncation.")
            return system_msgs + recent_msgs

        logger.info(
            "[Compact] Splitting conversation: keeping %d recent messages (~%d tokens), compacting %d historical messages.",
            len(recent_msgs), accumulated_tokens - sys_tokens, len(history_msgs)
        )

        history_text = []
        for m in history_msgs:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if isinstance(content, list):
                extracted = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        extracted.append(str(c.get("text", "")))
                content = " ".join(extracted) if extracted else str(content)
            
            tc = m.get("tool_calls")
            if tc:
                for t in tc:
                    fn = t.get("function", {})
                    history_text.append(f"[{role} called tool: {fn.get('name', '?')}]")
            elif role == "tool":
                tool_name = m.get("name", "unknown_tool")
                truncated = str(content)[:1000]
                history_text.append(f"[tool result for {tool_name}]: {truncated}")
            else:
                truncated = str(content)[:1000]
                history_text.append(f"[{role}]: {truncated}")

        # Try to locate progress_report.md in candidate workspace roots
        import os
        from pathlib import Path
        progress_content = ""
        resolved_progress_path = None
        
        workspace_roots = find_workspace_roots(openai_messages)
        logger.info("[Compact] Candidate workspace roots: %s", [str(r) for r in workspace_roots])
        
        for root in workspace_roots:
            p_path = root / "progress_report.md"
            try:
                if p_path.exists() and p_path.is_file():
                    logger.info("[Compact] Found existing progress report at: %s", p_path)
                    with open(p_path, "r", encoding="utf-8") as f:
                        progress_content = f.read().strip()
                    resolved_progress_path = p_path
                    break
            except Exception as e:
                logger.warning("[Compact] Error reading progress report at %s: %s", p_path, e)
                
        if not resolved_progress_path and workspace_roots:
            resolved_progress_path = workspace_roots[0] / "progress_report.md"
            logger.info("[Compact] No existing progress report found. Targeting default path: %s", resolved_progress_path)

        if progress_content:
            summary_prompt = (
                "You are an expert developer and project manager tracking codebase progress.\n"
                "Your task is to update the coding session's progress report (`progress_report.md`) by merging "
                "the existing progress report with the latest actions taken in the chat history.\n\n"
                "### Existing progress_report.md Content:\n"
                "```markdown\n"
                f"{progress_content}\n"
                "```\n\n"
                "### Recent Conversation History to Compact:\n"
                "```text\n"
                + "\n".join(history_text)
                + "\n```\n\n"
                "### Guidelines:\n"
                "1. Focus ONLY on concrete updates: which files were modified, what specific changes/code structures were added or removed, and what needs to be done next.\n"
                "2. Maintain the report in markdown format. It MUST contain the following sections:\n"
                "   - **Completed Tasks & Code Changes**: List files modified (using absolute/relative paths if known), showing functions/areas updated and a 1-line summary of what was done.\n"
                "   - **Current Status**: Brief summary of the system state.\n"
                "   - **Next Actions / Todo**: Checklist of remaining tasks.\n"
                "3. Keep the details factual, concise, and lose no technical details (e.g. paths, error fixes). Avoid placeholders.\n"
                "4. DO NOT copy large blocks of code. Keep code snippets minimal (1-3 lines max at the completion point).\n"
                "5. Output ONLY the updated markdown content of the progress report, with no preamble or chat meta-talk.\n"
            )
        else:
            summary_prompt = (
                "You are an expert developer and project manager tracking codebase progress.\n"
                "Your task is to generate a new progress report (`progress_report.md`) based on the actions taken in the conversation history.\n\n"
                "### Conversation History to Compact:\n"
                "```text\n"
                + "\n".join(history_text)
                + "\n```\n\n"
                "### Guidelines:\n"
                "1. Focus ONLY on concrete updates: which files were modified, what specific changes/code structures were added or removed, and what needs to be done next.\n"
                "2. Structure the report in markdown format. It MUST contain the following sections:\n"
                "   - **Completed Tasks & Code Changes**: List files modified, showing functions/areas updated and a 1-line summary of what was done.\n"
                "   - **Current Status**: Brief summary of where the project stands right now.\n"
                "   - **Next Actions / Todo**: Checklist of remaining tasks.\n"
                "3. Keep the details factual, concise, and lose no technical details (e.g. paths, error fixes). Avoid placeholders.\n"
                "4. DO NOT copy large blocks of code. Keep code snippets minimal (1-3 lines max at the completion point).\n"
                "5. Output ONLY the markdown content of the progress report, with no preamble or chat meta-talk.\n"
            )

        est_input_lite = len(str(summary_prompt)) // 4
        if est_input_lite > config.LITE_EMERGENCY_MAX_INPUT_TOKENS:
            logger.warning("[Compact] Merge prompt is too large (%d tokens). Truncating...", est_input_lite)
            allowed_chars = config.LITE_EMERGENCY_MAX_INPUT_TOKENS * 3
            summary_prompt = (
                summary_prompt[:allowed_chars] 
                + "\n\n[... TRUNCATED DUE TO EXTREME LENGTH TO FIT LIMIT ...]"
            )
            est_input_lite = len(str(summary_prompt)) // 4

        summary_text = ""
        lite_key = None
        lite_mid = None
        max_compact_attempts = 3
        
        for compact_attempt in range(max_compact_attempts):
            lite_key = None
            try:
                _, lite_mid, lite_key, lite_litellm, lite_reservation = await _resolve_model(
                    body, "gemini-flash-lite", estimated_tokens=est_input_lite + 4096, retry_attempt=compact_attempt
                )
                if not lite_key:
                    logger.warning("[Compact] Could not get lite model key on attempt %d", compact_attempt)
                    continue

                summary_kwargs = {
                    "model": lite_litellm,
                    "messages": [{"role": "user", "content": summary_prompt}],
                    "api_key": lite_key,
                    "max_tokens": 4096,
                    "temperature": 0.3,
                    "stream": True,
                    "request_timeout": 30,
                }
                gen = await litellm.acompletion(**summary_kwargs)
                summary_chunks = []
                async for chunk in gen:
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                         summary_chunks.append(chunk.choices[0].delta.content)
                summary_text = "".join(summary_chunks).strip()

                if summary_text:
                    break
                else:
                    logger.warning("[Compact] Lite model returned empty merge report on attempt %d", compact_attempt)

            except Exception as e:
                logger.warning("[Compact] Attempt %d failed with error: %s", compact_attempt, e)
                if lite_key:
                    from src.core.limits import apply_error_penalty
                    router.freeze_key(lite_key, 15, lite_mid, "rate_limit")
                    apply_error_penalty(lite_key, "rate_limit", lite_mid)
            finally:
                if lite_key:
                    router.release_key(lite_key)
                    lite_key = None

        if not summary_text:
            if progress_content:
                logger.warning("[Compact] LLM merge failed. Falling back to existing progress report content.")
                summary_text = progress_content
            else:
                logger.error("[Compact] Dynamic conversation compaction failed and no existing progress report was found.")
                return system_msgs + history_msgs + recent_msgs

        # Write the updated progress report back to disk
        if resolved_progress_path:
            try:
                resolved_progress_path.parent.mkdir(parents=True, exist_ok=True)
                with open(resolved_progress_path, "w", encoding="utf-8") as f:
                    f.write(summary_text)
                logger.info("[Compact] Successfully updated local progress report file at: %s", resolved_progress_path)
            except Exception as e:
                logger.error("[Compact] Failed to write updated progress report file: %s", e)

        logger.info("[Compact] Compaction successful. Summary size: %d chars.", len(summary_text))

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

        warning_threshold = 178000 if is_claude_code else 170000
        is_sub = is_sub_agent_body(body)
        if input_tokens > warning_threshold and not is_sub:
            warning_text = (
                f"\n\n[⚠️ SYSTEM WARNING: CONTEXT EXTREMELY FULL (>{warning_threshold // 1000}K tokens) ⚠️]\n"
                f"The conversation context is extremely large (over {warning_threshold // 1000}K tokens). This is very close to the limit of Gemini Flash.\n"
                "You MUST start your response with a highly visible, bold warning message in the language of the conversation (e.g. Vietnamese/English),\n"
                "reminding the user that the context is almost full and urging them to run the '/compact' command in the CLI terminal to clear history.\n"
                f"Example warning: '**⚠️ CẢNH BÁO: Context đã đầy (>{warning_threshold // 1000}K tokens)! Vui lòng gõ lệnh `/compact` để làm sạch hội thoại tránh bị quá giới hạn.**'\n"
                "Additionally, since you are running inside Claude Code CLI which does not render markdown diagrams (like Mermaid), "
                "if you need to draw any architecture diagrams, flowcharts, or system diagrams, you MUST draw them strictly using ASCII art."
            )
            if compacted and compacted[0].get("role") == "system":
                last_sys = compacted[0]
                if isinstance(last_sys.get("content"), str):
                    last_sys["content"] += warning_text
            else:
                compacted.insert(0, {"role": "system", "content": warning_text})
            
            for idx in range(len(compacted) - 1, -1, -1):
                if compacted[idx].get("role") == "user":
                    u_msg = compacted[idx]
                    if isinstance(u_msg.get("content"), str):
                        u_msg["content"] += warning_text
                    elif isinstance(u_msg.get("content"), list):
                        u_msg["content"].append({"type": "text", "text": warning_text})
                    break

        final_tokens = sum(_estimate_msg_tokens(m) for m in compacted)
        logger.warning(
            "[Compact] Compaction complete: %d messages -> %d messages (Final estimated tokens: %d)",
            len(openai_messages), len(compacted), final_tokens
        )
        return _emergency_truncate_to_limit(compacted, config.EMERGENCY_MAX_INPUT_TOKENS)

    except Exception as exc:
        logger.error("[Compact] Dynamic compaction function crashed: %s. Falling back to emergency truncation.", exc, exc_info=True)
        emergency_limit = 100000 if is_claude_code else 150000
        sys_tokens = sum(_estimate_msg_tokens(m) for m in system_msgs)
        accumulated = sys_tokens
        split_idx = len(chat_msgs) - 1
        
        for idx in range(len(chat_msgs) - 1, -1, -1):
            m_tok = _estimate_msg_tokens(chat_msgs[idx])
            if accumulated + m_tok > emergency_limit:
                if idx < len(chat_msgs) - 1:
                    split_idx = idx + 1
                else:
                    split_idx = len(chat_msgs) - 1
                break
            accumulated += m_tok
        else:
            split_idx = 0

        while split_idx > 0 and chat_msgs[split_idx].get("role") != "user":
            split_idx -= 1

        truncated_recent = chat_msgs[split_idx:]

        compacted = list(system_msgs)
        compacted.append({
            "role": "user",
            "content": "[SYSTEM NOTICE: Dynamic conversation compaction failed due to an upstream error. "
                       "Older conversation history has been truncated to protect context limits.]"
        })
        compacted.extend(truncated_recent)

        warning_threshold = 178000 if is_claude_code else 170000
        is_sub = is_sub_agent_body(body)
        if input_tokens > warning_threshold and not is_sub:
            warning_text = (
                f"\n\n[⚠️ SYSTEM WARNING: CONTEXT EXTREMELY FULL (>{warning_threshold // 1000}K tokens) ⚠️]\n"
                f"The conversation context is extremely large (over {warning_threshold // 1000}K tokens). This is very close to the limit of Gemini Flash.\n"
                "You MUST start your response with a highly visible, bold warning message in the language of the conversation (e.g. Vietnamese/English),\n"
                "reminding the user that the context is almost full and urging them to run the '/compact' command in the CLI terminal to clear history.\n"
                f"Example warning: '**⚠️ CẢNH BÁO: Context đã đầy (>{warning_threshold // 1000}K tokens)! Vui lòng gõ lệnh `/compact` để làm sạch hội thoại tránh bị quá giới hạn.**'\n"
                "Additionally, since you are running inside Claude Code CLI which does not render markdown diagrams (like Mermaid), "
                "if you need to draw any architecture diagrams, flowcharts, or system diagrams, you MUST draw them strictly using ASCII art."
            )
            if compacted and compacted[0].get("role") == "system":
                last_sys = compacted[0]
                if isinstance(last_sys.get("content"), str):
                    last_sys["content"] += warning_text
            else:
                compacted.insert(0, {"role": "system", "content": warning_text})

            for idx in range(len(compacted) - 1, -1, -1):
                if compacted[idx].get("role") == "user":
                    u_msg = compacted[idx]
                    if isinstance(u_msg.get("content"), str):
                        u_msg["content"] += warning_text
                    elif isinstance(u_msg.get("content"), list):
                        u_msg["content"].append({"type": "text", "text": warning_text})
                    break

        return _emergency_truncate_to_limit(compacted, config.EMERGENCY_MAX_INPUT_TOKENS)

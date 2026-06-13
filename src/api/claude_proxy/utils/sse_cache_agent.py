import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from src.core.config_n_logg.logger import logger_proxy as logger

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_log_path = _PROJECT_ROOT / "logs" / "claude_request.log"

def _sse(event: str, data: Dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")

def _get_simulated_cache_usage(body: Dict[str, Any], input_tokens: int) -> Dict[str, int]:
    if input_tokens < 1024:
        return {}
    messages = body.get("messages", [])
    if len(messages) <= 1:
        return {
            "cache_creation_input_tokens": input_tokens,
            "cache_read_input_tokens": 0,
        }
    last_msg = messages[-1]
    content = last_msg.get("content", "")
    last_msg_text = ""
    if isinstance(content, str):
        last_msg_text = content
    elif isinstance(content, list):
        last_msg_text = "".join([
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ])
    last_msg_tokens = max(1, len(last_msg_text) // 4) + 20
    cache_read = max(0, input_tokens - last_msg_tokens)
    if cache_read < 1024:
        return {
            "cache_creation_input_tokens": input_tokens,
            "cache_read_input_tokens": 0,
        }
    return {
        "cache_creation_input_tokens": last_msg_tokens,
        "cache_read_input_tokens": cache_read,
    }

def _intercept_sub_agent(body: Dict[str, Any]) -> Optional[str]:
    system_instruction = body.get("system", "")
    if isinstance(system_instruction, list):
        system_prompt = "\n".join([str(item.get("text", "")) for item in system_instruction if isinstance(item, dict)])
    else:
        system_prompt = str(system_instruction or "")

    if system_prompt:
        first_user_msg = ""
        for msg in body.get("messages", []):
            if msg.get("role") == "user":
                raw = msg.get("content", "")
                if isinstance(raw, list):
                    raw = " ".join(b.get("text", "") for b in raw if isinstance(b, dict) and b.get("type") == "text")
                first_user_msg = str(raw)[:300].replace('\n', ' ')
                break

        logger.info("[Request] Tools=%d | System=%.200s... | FirstUser=%.300s",
                    len(body.get("tools", [])),
                    system_prompt[:200].replace('\n', ' '),
                    first_user_msg)

        try:
            tool_names = [t.get("function", {}).get("name", "?") for t in (body.get("tools") or [])]
            # Ensure log directory exists dynamically
            _log_path.parent.mkdir(parents=True, exist_ok=True)
            _sep = "=" * 60
            _entry = (
                f"{_sep}\n"
                f"[{datetime.datetime.now().isoformat()}]\n"
                f"Tools ({len(tool_names)}): {', '.join(tool_names)}\n"
                f"Messages: {len(body.get('messages', []))}\n"
                f"Model: {body.get('model', '?')}\n"
                f"FirstUser: {first_user_msg}\n"
                f"── System Prompt ──\n{system_prompt}\n"
                f"── End System Prompt ──\n\n"
            )
            with open(_log_path, "a", encoding="utf-8") as _f:
                _f.write(_entry)
        except Exception:
            pass

        system_prompt_lower = system_prompt.lower()
        if "you are an interactive agent" in system_prompt_lower:
            return None
        if "you are claude code" in system_prompt_lower:
            logger.info("[Sub-Agent Detect] Detected Claude Code sub-agent via non-interactive prompt, overriding to gemini-flash-lite")
            return "gemini-flash-lite"

        sub_agent_keywords = [
            "general-purpose agent",
            "general-purpose assistant",
            "explore agent",
            "file search specialist",
            "exploration task",
            "read-only exploration",
            "plan agent",
            "software architect",
            "implementation plans",
            "claude-code-guide",
            "statusline-setup",
            "specialized agent",
            "subagent",
            "sub-agent",
            "security monitor",
            "you are the claude-code-guide",
            "you are the explore",
            "you are the plan",
            "you are the general-purpose",
            "you are the statusline-setup",
        ]
        if any(kw in system_prompt_lower for kw in sub_agent_keywords):
            logger.info("[Sub-Agent Detect] Detected Claude Code sub-agent via system prompt keyword, overriding to gemini-flash-lite")
            return "gemini-flash-lite"

        if re.search(r"you are (a|an|the)[\s\w\-]*sub.?agent", system_prompt_lower):
            logger.info("[Sub-Agent Detect] Detected sub-agent via 'you are a ... sub-agent' pattern in system prompt, overriding to gemini-flash-lite")
            return "gemini-flash-lite"

        if "[sub-agent]" in system_prompt_lower:
            logger.info("[Sub-Agent Detect] Detected [SUB-AGENT] tag in system prompt, overriding to gemini-flash-lite")
            return "gemini-flash-lite"

        tool_count = len(body.get("tools", []))
        if tool_count in (19, 20):
            logger.info("[Sub-Agent Detect] Detected Claude Code sub-agent via tool count (%d), overriding to gemini-flash-lite", tool_count)
            return "gemini-flash-lite"

    messages = body.get("messages", [])
    if not messages:
        return None

    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip().startswith("[SUB-AGENT]"):
            msg["content"] = content.replace("[SUB-AGENT]", "", 1).strip()
            logger.info("[Sub-Agent Detect] Detected sub-agent via [SUB-AGENT] prefix in user message, overriding to gemini-flash-lite")
            return "gemini-flash-lite"
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    if block.get("text", "").strip().startswith("[SUB-AGENT]"):
                        block["text"] = block["text"].replace("[SUB-AGENT]", "", 1).strip()
                        logger.info("[Sub-Agent Detect] Detected sub-agent via [SUB-AGENT] prefix in user message list block, overriding to gemini-flash-lite")
                        return "gemini-flash-lite"
    return None

def _estimate_msg_tokens(m: Dict[str, Any]) -> int:
    try:
        return max(1, len(json.dumps(m, ensure_ascii=False)) // 2)
    except Exception:
        return max(1, len(str(m)) // 2)

def _truncate_huge_message(msg: Dict[str, Any], max_chars: int = 250000) -> Dict[str, Any]:
    content = msg.get("content")
    if isinstance(content, str) and len(content) > max_chars:
        half = max_chars // 2
        truncated_text = (
            f"{content[:half]}\n\n"
            f"[... TRUNCATED {len(content) - max_chars} CHARS TO FIT GEMINI 250K TPM LIMIT ...]\n\n"
            f"{content[-half:]}"
        )
        new_msg = dict(msg)
        new_msg["content"] = truncated_text
        logger.warning(
            "[Truncate] Message (role=%s) content truncated from %d to %d chars to fit 250K TPM limit.",
            msg.get("role"), len(content), len(truncated_text)
        )
        return new_msg
    return msg

def is_claude_code_body(body: Dict[str, Any]) -> bool:
    if not body:
        return False
    system_instruction = body.get("system", "")
    if isinstance(system_instruction, list):
        system_prompt = "\n".join([str(item.get("text", "")) for item in system_instruction if isinstance(item, dict)])
    else:
        system_prompt = str(system_instruction or "")
    system_prompt_lower = system_prompt.lower()
    return "you are claude code" in system_prompt_lower or "cc_version=" in system_prompt_lower or "claude-code" in system_prompt_lower

def is_sub_agent_body(body: Dict[str, Any]) -> bool:
    if not body:
        return False
    system_instruction = body.get("system", "")
    if isinstance(system_instruction, list):
        system_prompt = "\n".join([str(item.get("text", "")) for item in system_instruction if isinstance(item, dict)])
    else:
        system_prompt = str(system_instruction or "")

    if system_prompt:
        system_prompt_lower = system_prompt.lower()
        if "you are an interactive agent" in system_prompt_lower:
            return False
        if "you are claude code" in system_prompt_lower:
            return False

        sub_agent_keywords = [
            "general-purpose agent",
            "general-purpose assistant",
            "explore agent",
            "file search specialist",
            "exploration task",
            "read-only exploration",
            "plan agent",
            "software architect",
            "implementation plans",
            "claude-code-guide",
            "statusline-setup",
            "specialized agent",
            "subagent",
            "sub-agent",
            "security monitor",
            "you are the claude-code-guide",
            "you are the explore",
            "you are the plan",
            "you are the general-purpose",
            "you are the statusline-setup",
        ]
        if any(kw in system_prompt_lower for kw in sub_agent_keywords):
            return True

        if re.search(r"you are (a|an|the)[\s\w\-]*sub.?agent", system_prompt_lower):
            return True

        if "[sub-agent]" in system_prompt_lower:
            return True

        tool_count = len(body.get("tools", []))
        if tool_count in (19, 20):
            return True

    messages = body.get("messages", [])
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and (content.strip().startswith("[SUB-AGENT]") or "[SUB-AGENT]" in content):
            return True
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_val = block.get("text", "").strip()
                    if text_val.startswith("[SUB-AGENT]") or "[SUB-AGENT]" in text_val:
                        return True
    return False

def _dict_to_sse_events(result: Dict[str, Any]) -> Iterator[bytes]:
    msg = {k: result.get(k) for k in ("id", "type", "role", "model", "content", "stop_reason", "stop_sequence")}
    msg["usage"] = {"input_tokens": result.get("usage", {}).get("input_tokens", 0), "output_tokens": 0}
    yield _sse("message_start", {"type": "message_start", "message": msg})
    for idx, block in enumerate(result.get("content", [])):
        if block.get("type") == "text":
            yield _sse("content_block_start", {"type": "content_block_start", "index": idx, "content_block": {"type": "text", "text": ""}})
            yield _sse("content_block_delta", {"type": "content_block_delta", "index": idx, "delta": {"type": "text_delta", "text": block.get("text", "")}})
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif block.get("type") == "thinking":
            yield _sse("content_block_start", {"type": "content_block_start", "index": idx, "content_block": {"type": "thinking", "thinking": ""}})
            thinking_text = block.get("thinking", "")
            if thinking_text:
                yield _sse("content_block_delta", {"type": "content_block_delta", "index": idx, "delta": {"type": "thinking_delta", "thinking": thinking_text}})
            sig = block.get("signature") or "gmni_" + hashlib.sha256(thinking_text.encode()).hexdigest()[:60]
            yield _sse("content_block_delta", {"type": "content_block_delta", "index": idx, "delta": {"type": "signature_delta", "signature": sig}})
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif block.get("type") == "tool_use":
            yield _sse("content_block_start", {"type": "content_block_start", "index": idx, "content_block": {"type": "tool_use", "id": block.get("id"), "name": block.get("name"), "input": {}}})
            yield _sse("content_block_delta", {"type": "content_block_delta", "index": idx, "delta": {"type": "input_json_delta", "partial_json": json.dumps(block.get("input", {}))}})
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif block.get("type") == "agent_use":
            yield _sse("content_block_start", {"type": "content_block_start", "index": idx, "content_block": {"type": "agent_use", "id": block.get("id"), "agent_type": block.get("agent_type", "general-purpose"), "prompt": block.get("prompt", "")}})
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": idx})
    yield _sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": result.get("stop_reason", "end_turn"), "stop_sequence": None}, "usage": {"output_tokens": result.get("usage", {}).get("output_tokens", 0)}})
    yield _sse("message_stop", {"type": "message_stop"})


def save_resolved_model_for_cwd(system_prompt: Any, model_alias: str, model_id: str) -> None:
    if not system_prompt:
        return
        
    if isinstance(system_prompt, list):
        parts = []
        for item in system_prompt:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        system_prompt_str = "\n".join(parts)
    else:
        system_prompt_str = str(system_prompt)

    m = re.search(r"Primary working directory:\s*([^\r\n]+)", system_prompt_str, re.IGNORECASE)
    cwd = None
    if m:
        cwd = m.group(1).strip()
    else:
        m = re.search(r"working directory:\s*([^\r\n]+)", system_prompt_str, re.IGNORECASE)
        if m:
            cwd = m.group(1).strip()
            
    if not cwd:
        return

    try:
        import time
        mapping_file = _PROJECT_ROOT / "logs" / "session_models.json"
        mapping_file.parent.mkdir(parents=True, exist_ok=True)
        
        models_map = {}
        if mapping_file.exists():
            try:
                with open(mapping_file, "r", encoding="utf-8") as mf:
                    models_map = json.load(mf)
            except Exception:
                pass
        
        models_map[cwd] = {
            "model_alias": model_alias,
            "model_id": model_id,
            "timestamp": int(time.time())
        }
        
        with open(mapping_file, "w", encoding="utf-8") as mf:
            json.dump(models_map, mf, ensure_ascii=False, indent=2)
            
        logger.info("[Statusline Sync] Saved resolved model for cwd %s: %s (id: %s)", cwd, model_alias, model_id)
    except Exception as ex:
        logger.error("[Statusline Sync Error] Failed to save resolved model: %s", ex)

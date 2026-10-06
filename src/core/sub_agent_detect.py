"""Sub-agent request detection.

Two detectors, both pure functions over a raw request body. They live in core/
because the request path reaches them from everywhere: middleware decides
whether to hand back a simulated 200 instead of propagating a 429, and the
proxies decide whether to enable web search.

They used to be reachable only through src.api.opencode_proxy and
src.logical_HQ_translator.sse_cache_agent, which meant auth middleware imported
the proxy layer to ask it a question about a request it had already parsed.
"""
import re
from typing import Any, Dict


def is_sub_agent_body(body: Dict[str, Any]) -> bool:
    if not body:
        return False
    system_instruction = body.get("system", "")
    if isinstance(system_instruction, list):
        system_prompt = "\n".join([str(item.get("text", "")) for item in system_instruction if isinstance(item, dict)])
    else:
        system_prompt = str(system_instruction or "")

    # For OpenAI/OpenCode formats, system prompt can be in the messages list with role "system" or "developer"
    if not system_prompt:
        for msg in body.get("messages", []):
            if msg.get("role") in ("system", "developer"):
                content = msg.get("content", "")
                if isinstance(content, str):
                    system_prompt = content
                elif isinstance(content, list):
                    system_prompt = "\n".join([str(item.get("text", "")) for item in content if isinstance(item, dict)])
                break

    if system_prompt:
        system_prompt_lower = system_prompt.lower()
        if "you are an interactive agent" in system_prompt_lower:
            return False
        if "you are claude code" in system_prompt_lower:
            return True

        sub_agent_keywords = [
            "general-purpose agent",
            "general-purpose assistant",
            "explore agent",
            "file search specialist",
            "exploration task",
            "read-only exploration",
            "claude-code-guide",
            "statusline-setup",
            "specialized agent",
            "subagent",
            "sub-agent",
            "security monitor",
            "you are the claude-code-guide",
            "you are the explore",
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
        if 16 <= tool_count <= 25:
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


def is_sub_agent_request_openai(body: Dict[str, Any]) -> bool:
    """Detect if request is from a sub-agent."""
    if is_sub_agent_body(body):
        return True
    system_prompt = ""
    sys_val = body.get("system", "")
    if isinstance(sys_val, list):
        system_prompt = "\n".join([str(item.get("text", "")) for item in sys_val if isinstance(item, dict)])
    elif isinstance(sys_val, str):
        system_prompt = sys_val
    if not system_prompt:
        return False
    sp_lower = system_prompt.lower()
    if "opencode" not in sp_lower:
        return False
    main_indicators = ["interactive agent", "main agent", "primary agent", "you are the main", "you are the primary", "lead agent"]
    if any(ind in sp_lower for ind in main_indicators):
        return False
    sub_keywords = ["explore", "read file", "search", "find", "glob", "grep", "task agent", "subagent", "sub-agent", "read files", "browse"]
    return any(kw in sp_lower for kw in sub_keywords)

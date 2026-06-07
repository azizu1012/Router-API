import re
from typing import Any, Dict, Optional


def get_system_prompt(body: Dict[str, Any]) -> str:
    for msg in body.get("messages", []):
        if msg.get("role") in ("system", "developer"):
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                return "\n".join(parts)
    return ""


_SUB_AGENT_KEYWORDS = [
    "general-purpose agent", "general-purpose assistant",
    "explore agent", "file search specialist",
    "exploration task", "read-only exploration",
    "plan agent", "software architect",
    "implementation plans", "claude-code-guide",
    "statusline-setup", "specialized agent",
    "subagent", "sub-agent", "security monitor",
    "you are the claude-code-guide", "you are the explore",
    "you are the plan", "you are the general-purpose",
    "you are the statusline-setup",
]


def detect_sub_agent_override(body: Dict[str, Any], account: Optional[Dict[str, Any]] = None, is_opencode: bool = False) -> Optional[str]:
    system_prompt = get_system_prompt(body)
    if not system_prompt:
        return None

    system_prompt_lower = system_prompt.lower()

    # Do not override the main interactive session
    if "you are an interactive agent" in system_prompt_lower:
        return None

    # Determine which model to override subagents to
    target_model = None
    if account:
        target_model = account.get("subagent_model") or account.get("agent_model") or account.get("sub_agent_model")
    if not target_model:
        import os
        target_model = os.getenv("OPENCODE_SUB_AGENT_MODEL") or os.getenv("SUB_AGENT_MODEL")
    if not target_model:
        target_model = "gemini-flash-lite"

    # If explicitly called from the opencode endpoint, any non-interactive request is treated as a sub-agent
    if is_opencode:
        return target_model

    # Check if this request is identified as coming from OpenCode or Claude Code via prompt keywords
    is_coding_agent_session = (
        "you are claude code" in system_prompt_lower 
        or "cc_version=" in system_prompt_lower 
        or "claude-code" in system_prompt_lower
        or "you are opencode" in system_prompt_lower
        or "opencode" in system_prompt_lower
    )

    if not is_coding_agent_session:
        return None

    if "you are claude code" in system_prompt_lower or "you are opencode" in system_prompt_lower:
        return target_model

    if any(kw in system_prompt_lower for kw in _SUB_AGENT_KEYWORDS):
        return target_model

    if re.search(r"you are (a|an|the)[\s\w\-]*sub.?agent", system_prompt_lower):
        return target_model

    if "[sub-agent]" in system_prompt_lower:
        return target_model

    tool_count = len(body.get("tools", []))
    if tool_count in (19, 20):
        return target_model

    return None

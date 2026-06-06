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


def detect_sub_agent_override(body: Dict[str, Any]) -> Optional[str]:
    system_prompt = get_system_prompt(body)
    if not system_prompt:
        return None

    system_prompt_lower = system_prompt.lower()

    if "you are an interactive agent" in system_prompt_lower:
        return None
    if "you are claude code" in system_prompt_lower:
        return "gemini-flash-lite"

    if any(kw in system_prompt_lower for kw in _SUB_AGENT_KEYWORDS):
        return "gemini-flash-lite"

    if re.search(r"you are (a|an|the)[\s\w\-]*sub.?agent", system_prompt_lower):
        return "gemini-flash-lite"

    if "[sub-agent]" in system_prompt_lower:
        return "gemini-flash-lite"

    tool_count = len(body.get("tools", []))
    if tool_count in (19, 20):
        return "gemini-flash-lite"

    return None

import re
from typing import List, Optional

SUB_AGENT_KEYWORDS: List[str] = [
    "subagent", "sub-agent", "sub_agent",
    "file search specialist", "file_search_specialist", "file-search-specialist",
    "code search specialist", "code_search_specialist", "code-search-specialist",
    "search specialist", "search_specialist", "search-specialist",
    "research specialist", "research_specialist", "research-specialist",
    "code review", "code_review", "codereview",
    "debug assistant", "debug_assistant", "debug-assistant",
    "planning", "planner", "plan",
    "task agent", "task_agent", "task-agent",
]

def get_system_prompt(body: dict) -> str:
    messages = body.get("messages", [])
    for m in messages:
        if m.get("role") in ("system", "developer"):
            content = m.get("content", "")
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                return "\n".join(parts)
    return ""

def detect_sub_agent_override(body: dict) -> Optional[str]:
    system_prompt = get_system_prompt(body).lower()
    for kw in SUB_AGENT_KEYWORDS:
        if kw in system_prompt:
            return "gemini-flash-lite"
    return None
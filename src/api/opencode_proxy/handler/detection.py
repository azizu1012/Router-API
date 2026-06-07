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
    "planning", "planner",
    "plan agent", "you are the plan",
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

def detect_sub_agent_override(body: dict, account: Optional[dict] = None, is_opencode: bool = False) -> Optional[str]:
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
        
    for kw in SUB_AGENT_KEYWORDS:
        if kw in system_prompt_lower:
            return target_model
            
    if re.search(r"you are (a|an|the)[\s\w\-]*sub.?agent", system_prompt_lower):
        return target_model

    if "[sub-agent]" in system_prompt_lower:
        return target_model

    tool_count = len(body.get("tools", []))
    if tool_count in (19, 20):
        return target_model

    return None
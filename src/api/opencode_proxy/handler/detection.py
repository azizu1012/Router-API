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
    if is_opencode:
        return None
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
    
    # Check if this request is identified as coming from OpenCode or Claude Code via path or prompt keywords
    is_claude_code = (
        "you are claude code" in system_prompt_lower 
        or "cc_version=" in system_prompt_lower 
        or "claude-code" in system_prompt_lower
    )
    is_opencode_prompt = (
        "you are opencode" in system_prompt_lower
        or "opencode" in system_prompt_lower
    )
    
    if not (is_opencode or is_claude_code or is_opencode_prompt):
        return None
        
    # Keyword check: if a sub-agent keyword is found, it's definitely a sub-agent
    for kw in SUB_AGENT_KEYWORDS:
        if kw in system_prompt_lower:
            return target_model
            
    if re.search(r"you are (a|an|the)[\s\w\-]*sub.?agent", system_prompt_lower):
        return target_model

    if "[sub-agent]" in system_prompt_lower:
        return target_model

    # Check for [SUB-AGENT] prefix in user messages
    messages = body.get("messages", [])
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str) and content.strip().startswith("[SUB-AGENT]"):
                return target_model
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        if block.get("text", "").strip().startswith("[SUB-AGENT]"):
                            return target_model

    # Tool count check (only applicable to Claude Code subagents)
    if is_claude_code:
        tool_count = len(body.get("tools", []))
        if tool_count in (19, 20):
            return target_model

    return None
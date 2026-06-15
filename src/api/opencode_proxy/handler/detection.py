import re
from typing import List, Optional

SUB_AGENT_KEYWORDS: List[str] = [
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
    "sub_agent",
    "security monitor",
    "you are the claude-code-guide",
    "you are the explore",
    "you are the plan",
    "you are the general-purpose",
    "you are the statusline-setup",
    "file_search_specialist", "file-search-specialist",
    "code search specialist", "code_search_specialist", "code-search-specialist",
    "search specialist", "search_specialist", "search-specialist",
    "research specialist", "research_specialist", "research-specialist",
    "code review", "code_review", "codereview",
    "debug assistant", "debug_assistant", "debug-assistant",
    "planning", "planner",
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
    
    # If not OpenCode/Claude Code context, no override
    if not (is_opencode or is_claude_code or is_opencode_prompt):
        return None
        
    # MAIN AGENT DETECTION: If it's the main interactive agent, DO NOT override
    # Main agents typically identify as "interactive", "main", "primary", or "you are an interactive agent"
    main_agent_indicators = [
        "you are an interactive agent",
        "interactive agent",
        "main agent",
        "primary agent",
        "you are the main",
        "you are the primary",
        "lead agent",
    ]
    for indicator in main_agent_indicators:
        if indicator in system_prompt_lower:
            return None  # This is the main agent, keep user's requested model
    
    # Check for explicit sub-agent markers (strongest signal)
    # 1. [SUB-AGENT] prefix in user messages
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
    
    # 2. [SUB-AGENT] or "subagent" in system prompt explicitly
    if "[sub-agent]" in system_prompt_lower or "[subagent]" in system_prompt_lower:
        return target_model
    
    if re.search(r"you are (a|an|the)[\s\w\-]*sub.?agent", system_prompt_lower):
        return target_model
    
    # 3. Tool count check (subagents get limited toolset)
    tool_count = len(body.get("tools", []))
    if 16 <= tool_count <= 25:
        return target_model
    
    # 4. Keyword check: Only apply if we're confident it's a sub-agent task
    # Be more conservative - only match specific specialist roles, not generic terms
    sub_agent_specialist_keywords = [
        "file_search_specialist", "file-search-specialist",
        "code search specialist", "code_search_specialist", "code-search-specialist",
        "search specialist", "search_specialist", "search-specialist",
        "research specialist", "research_specialist", "research-specialist",
        "code review", "code_review", "codereview",
        "debug assistant", "debug_assistant", "debug-assistant",
        "planning", "planner",
        "task agent", "task_agent", "task-agent",
        "security monitor",
        "you are the claude-code-guide",
        "you are the explore",
        "you are the plan",
        "you are the statusline-setup",
        "claude-code-guide",
        "statusline-setup",
        "explore agent",
        "read-only exploration",
        "exploration task",
    ]
    
    # Generic terms like "general-purpose agent", "explore agent", "specialized agent" 
    # are too broad and match MAIN agent prompts. Skip them.
    
    for kw in sub_agent_specialist_keywords:
        if kw in system_prompt_lower:
            return target_model
    
    # If we reach here, it's likely the main agent or ambiguous - don't override
    return None
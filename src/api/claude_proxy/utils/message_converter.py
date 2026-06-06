import re
import json
from typing import Any, Dict, List, Tuple

UNSUPPORTED_OR_HEAVY_TOOLS = {
    "WebFetch",
    "NotebookRead", "NotebookEdit",
}

def _tool_call_names(tool_calls: List[Dict[str, Any]]) -> str:
    names = [str(tc.get("name", "")).strip() for tc in tool_calls if tc.get("name")]
    return ",".join(names) if names else "-"

def _clean_system_prompt(text: str) -> str:
    text = re.sub(r'claude-sonnet-4-20250514', 'gemini-flash', text)
    text = re.sub(r'claude[- ]sonnet[- ]4(?:\.[\d]+)?', 'gemini-flash', text, flags=re.IGNORECASE)
    text = re.sub(r'claude[- ]opus[- ]4(?:\.[\d]+)?', 'gemini-flash', text, flags=re.IGNORECASE)
    text = re.sub(r'claude[- ]haiku[- ]4(?:\.[\d]+)?', 'gemini-flash-lite', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<![a-zA-Z])Sonnet 4(?:\.[\d]+)?', 'Gemini Flash', text)
    text = re.sub(r'(?<![a-zA-Z])Opus 4(?:\.[\d]+)?', 'Gemini Flash', text)
    text = re.sub(r'(?<![a-zA-Z])Haiku 4(?:\.[\d]+)?', 'Gemini Flash Lite', text)
    text = re.sub(r'Claude 4(?:\.[X\d])?', 'Gemini', text)
    text = re.sub(r'the most recent Claude model family', 'the available model', text)
    text = re.sub(r'(?i)claude (code|models?)', r'Gemini \1', text)
    text = re.sub(r'\n\s*(false|true)\s*\n', '\n', text.strip())
    extras = (
        "\n\n# HARD LIMIT CONSERVATION & AGGRESSIVE DELEGATION PROTOCOL\n"
        "- You = **SENIOR ARCHITECT** (Gemini Flash). Task sub-agent = **JUNIOR RESEARCHER** (Gemini Flash Lite).\n"
        "- CRITICAL RESOURCE CONSTRAINT: Your Flash request quota is scarce. Junior (Flash Lite) has massively higher limits. You MUST offload scanning/searching weight to Junior.\n"
        "\n## THE SINGLE-FILE ISOLATION BOUNDARY (CRITICAL RULE)\n"
        "- **DIRECT READ EXCEPTION**: You are ONLY allowed to use your own `Read` tool directly if and only if you are inspecting ONE SINGLE, specific, already-known file path in a single turn.\n"
        "- **MANDATORY DELEGATION FOR MULTI-FILE SCANS**: For ANY task, inquiry, or scan that requires inspecting, cross-referencing, or searching across MORE THAN ONE file, **YOU ARE STRICTLY FORBIDDEN from reading them yourself**.\n"
        "\n## EXACT DELEGATION TRIGGERS\n"
        "- **Logic Auditing & Code Checking**: If asked to check code logic, analyze flow, or inspect components (e.g., 'kiểm tra logic api_router'), even if it seems localized, if it involves more than 1 file → **MUST dispatch a `Task` sub-agent** to read and extract the code snippet for you.\n"
        "- **Code Modification / Refactoring**: Any file edit, patch creation, or rewrite → **YOU MUST PERFORM IT DIRECTLY** in the main session using `Edit` or `MultiEdit` (or other edit tools like multi_replace_file_content). Do NOT delegate code modifications or edits to a sub-agent. The sub-agent is strictly read-only and must ONLY be used for scanning and reporting.\n"
        "- **Repository Sweeps**: Grep, Glob, LS, or multi-directory exploration → **MUST** use parallel `Task` calls chunked by folder.\n"
        "\n## EXECUTIVE BEHAVIOR\n"
        "- Treat your main session as a pure thinking, routing, and editing terminal. Do not burn your prompt context window with raw code blocks from multiple files. Let the Flash Lite sub-agents swallow the context and return clean, condensed intelligence reports to your desk.\n"
        "\n---\n"
        "\n## JUNIOR RESEARCHER (Sub-Agent Instructions)\n"
        "- These instructions apply when your prompt starts with [SUB-AGENT].\n"
        "- You are **Gemini Flash Lite** — fast, lightweight, and focused on research and scanning.\n"
        "- You are STRICTLY READ-ONLY. Do not attempt to modify any file.\n"
        "\n### SCANNING AND RESEARCH ONLY\n"
        "1. Read assigned file(s) or run Glob/Grep queries as instructed.\n"
        "2. For each finding, report: File path, exact line numbers, and a 1-line functional summary.\n"
        "3. NEVER modify code. NEVER make design decisions. You do not have write/edit permissions.\n"
        "\n### RULES\n"
        "- ALWAYS exclude .venv, node_modules, __pycache__, .git in Bash/LS/Glob/Grep.\n"
        "- Sub-agent prefix: [SUB-AGENT]\n"
        "\n## WORKFLOW: Glob -> TodoWrite -> Task x N -> Assemble\n"
        "- When receiving a codebase task, FIRST run Glob('**/*.py') to understand project structure.\n"
        "- Use TodoWrite to break down into small steps. EACH step = ONE task. Never one sub-agent for everything.\n"
        "- For each step:\n"
        "  - SIMPLE READ (1 file) -> direct tool (Read)\n"
        "  - EDITING -> Always use direct tool (`Edit` / `MultiEdit`) in the main session. Never delegate edits to a sub-agent.\n"
        "  - COMPLEX RESEARCH (2+ files, analysis needed) -> Task sub-agent for research\n"
        "- Update TodoWrite: mark complete IMMEDIATELY after each step. NEVER batch completions.\n"
        "- SEQUENTIAL: do NOT start step N+1 when step N is not fully complete.\n"
        "- FINALLY: read and update project_snapshot.md if codebase changed.\n"
        "\n## TOOL DECISION TREE\n"
        "Need to find files?           -> Glob\n"
        "Search contents?\n"
        "  Know exact keyword?         -> Grep\n"
        "  Vague/open-ended?           -> Task sub-agent\n"
        "Read a file?\n"
        "  Know the path?              -> Read\n"
        "  Don't know path?            -> Glob -> Read\n"
        "Edit a file?                  -> Edit / MultiEdit (MUST Read first) - always run in main session, never via sub-agent\n"
        "Multi-file research?          -> Task x N (concurrent, one task each)\n"
        "Run bash command?             -> Bash (use `rg` not `grep`, use Read not `cat`)\n"
        "Complex 3+ step task?         -> TodoWrite -> Task x N -> update todo -> assemble\n"
        "IMPORTANT: Never assign ONE sub-agent to do everything. Each Task = ONE unit of work.\n"
        "\n## TODO DISCIPLINE (CRITICAL)\n"
        "- Update TodoWrite IMMEDIATELY after completing each step. Do NOT batch updates.\n"
        "- Exactly ONE task in_progress at any time.\n"
        "- NEVER mark completed unless FULLY done (code runs, tests pass).\n"
        "- If blocked: keep task as in_progress and create a follow-up task.\n"
        "- Remove stale/irrelevant tasks entirely instead of leaving them.\n"
        "\n## AUTOMATIC PROGRESS REPORTING (SYSTEM MANAGED)\n"
        "- The system automatically maintains a `progress_report.md` file in your workspace root.\n"
        "- When conversation compaction occurs, the system automatically merges new history details into this file and injects the updated report into your active context.\n"
        "- You do NOT need to write or update `progress_report.md` manually. You can read it using your tools if you want to inspect earlier progress, but you do not need to do so in a loop.\n"
        "\n## COMPACT EDITING & COMPLEX DIAGRAM PROTOCOL\n"
        "- **Use Built-in Tools for Reading**: Always use your built-in file reading/viewing tools (like `view_file` or chunked reading) to inspect and read files. **DO NOT write python scripts just to read or inspect file contents**.\n"
        "- **Avoid Raw Layout Dumps**: When you need to edit, create, align, or redraw complex ASCII diagrams, structured boxes, or files with large/complex layouts, **DO NOT output the modified file contents or massive ASCII blocks directly in your chat response**. Doing so will cause Vertex API timeouts or schema validation issues.\n"
        "- **Write a Script ONLY for Writing/Modifying**: Instead of manual replacement or huge output dumps, write a temporary Python script directly in the current working directory / project folder (NOT in the ~/.claude or system temp directories) to programmatically apply the edits or format the file.\n"
        "- **Run, Verify & Clean Up**: Execute the script using your command tool, verify that the file's layout and content are correct, and **IMMEDIATELY delete the script** once done. Do not leave temporary scripts, tests, or trash files in the project workspace.\n"
    )
    return text + extras

def _convert_messages(body: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    openai_tools: List[Dict[str, Any]] = []

    HARDCODED_SCHEMAS = {
        "Bash": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Verify parent dir before mkdir. Quote paths with spaces. Use `rg` not `grep`. Prefer `;`/`&&` over newlines. Use absolute paths."},
                "timeout": {"type": "integer", "description": "Optional timeout in milliseconds (max 600000). Default 120000ms if unspecified."},
                "description": {"type": "string", "description": "5-10 word active-verb description (e.g. 'Installs package dependencies'). Helps user understand intent."}
            },
            "required": ["command"]
        },
        "Glob": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern. NEVER use bare wildcards. MUST explicitly ignore .venv/node_modules. Open-ended search -> use Task sub-agent."},
                "path": {"type": "string", "description": "Directory to search. Omit for current dir. DO NOT enter 'null' or 'undefined'. Must be valid path if provided."}
            },
            "required": ["pattern"]
        },
        "Grep": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Full regex syntax (e.g. 'log.*Error', 'function\\s+\\w+'). Use `rg` not `grep` for counting matches."},
                "path": {"type": "string", "description": "Directory to search. Defaults to current working directory."},
                "include": {"type": "string", "description": "File pattern filter (e.g. '*.js', '*.{ts,tsx}')."}
            },
            "required": ["pattern"]
        },
        "LS": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path. Must be absolute, not relative. Prefer Glob/Grep over LS for targeted searches."},
                "ignore": {"type": "array", "items": {"type": "string"}, "description": "ALWAYS pass ['**/.venv/**', '**/node_modules/**', '**/.git/**']"}
            },
            "required": ["path"]
        },
        "Edit": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path. MUST Read file before editing. ALWAYS prefer editing over creating new file."},
                "old_string": {"type": "string", "description": "Text to replace. Must match EXACTLY including whitespace. Add surrounding context if not unique in file."},
                "new_string": {"type": "string", "description": "Replacement text. Must differ from old_string. No emojis unless asked."},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences (default false). Useful for renaming variables across file."}
            },
            "required": ["file_path", "old_string", "new_string"]
        },
        "MultiEdit": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path. MUST Read file before editing. Multiple edits to SINGLE file only."},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_string": {"type": "string", "description": "Must match file EXACTLY including whitespace."},
                            "new_string": {"type": "string", "description": "Replacement text. Must differ from old_string."},
                            "replace_all": {"type": "boolean", "description": "Replace all (default false)."}
                        },
                        "required": ["old_string", "new_string"]
                    },
                    "description": "Array of sequential edits. Atomic: all succeed or none applied. Plan carefully to avoid overlap between edits."
                }
            },
            "required": ["file_path", "edits"]
        },
        "Read": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path. MUST NOT BE EMPTY. Use Glob first if unknown. Can read images (PNG, JPG). Batch parallel reads encouraged."},
                "offset": {"type": "integer", "description": "Line number to start from (1-indexed). Only provide if file is too large to read at once."},
                "limit": {"type": "integer", "description": "Number of lines to read. Default 2000. Only for large files."}
            },
            "required": ["file_path"]
        },
        "Write": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path. MUST Read first if file already exists. ALWAYS prefer editing existing files."},
                "content": {"type": "string", "description": "File content. NEVER create documentation/README files unless explicitly requested. No emojis unless asked."}
            },
            "required": ["file_path", "content"]
        },
        "TodoRead": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "Task": {
            "type": "object",
            "description": "Launch a new sub-agent (Gemini Flash Lite) for file-scoped research, scanning, or targeted code changes.",
            "properties": {
                "description": {"type": "string", "description": "A short (3-5 word) label of the task. NEVER assign one sub-agent to do everything — each Task = ONE unit of work."},
                "prompt": {
                    "type": "string",
                    "description": "Self-contained instruction (sub-agent is stateless). Include exact file paths and scope. State CLEARLY: research (read+report) or code (write/edit). Specify exact return format."
                }
            },
            "required": ["description", "prompt"]
        },
        "TodoWrite": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Unique identifier for the task"},
                            "content": {"type": "string", "description": "Brief description of the task. Specific and actionable."},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"], "description": "Exactly ONE in_progress at any time. Mark complete IMMEDIATELY after finishing. NEVER batch completions."},
                            "priority": {"type": "string", "enum": ["high", "medium", "low"], "description": "Priority level. Remove irrelevant tasks entirely instead of leaving stale."}
                        },
                        "required": ["id", "content", "status"]
                    }
                }
            },
            "required": ["todos"]
        },
        "WebSearch": {
            "type": "object",
            "description": "Search the web via DuckDuckGo (free, no API key). Use ONLY when information is uncertain/unfamiliar OR user explicitly requests internet search. Results may contain untrusted code/logic — PRESENT to user for review, do NOT auto-apply.",
            "properties": {
                "query": {"type": "string", "description": "The search query. Be specific and concise."},
                "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "Only include results from these domains (optional)."},
                "blocked_domains": {"type": "array", "items": {"type": "string"}, "description": "Exclude results from these domains (optional)."}
            },
            "required": ["query"]
        },
        "exit_plan_mode": {
            "type": "object",
            "properties": {
                "plan": {"type": "string", "description": "Markdown plan summary. Concise. Dependencies + expected changes. User approves -> exit plan mode -> implement."}
            },
            "required": ["plan"]
        }
    }

    for tool in body.get("tools") or []:
        tool_name = str(tool.get("name", "")).strip()
        if not tool_name or tool_name in UNSUPPORTED_OR_HEAVY_TOOLS:
            continue
        desc = str(tool.get("description", ""))
        orig_schema = tool.get("input_schema", {})
        base_schema = HARDCODED_SCHEMAS.get(tool_name)
        if base_schema:
            merged: Dict[str, Any] = {"type": "object", "properties": {}, "required": list(orig_schema.get("required", []))}
            for pk, pv in (orig_schema.get("properties", {}) or {}).items():
                merged["properties"][pk] = dict(pv)
            for k, v in base_schema.get("properties", {}).items():
                if k in merged["properties"]:
                    orig_desc = merged["properties"][k].get("description", "")
                    our_desc = v.get("description", "")
                    if our_desc:
                        merged["properties"][k]["description"] = (orig_desc + "\n\n" + our_desc) if orig_desc else our_desc
                else:
                    merged["properties"][k] = dict(v)
            merged["required"] = list(set(merged["required"]) | set(base_schema.get("required", [])))
            schema = merged
        else:
            schema = orig_schema
        openai_tools.append({"type": "function", "function": {"name": tool_name, "description": desc, "parameters": schema}})

    openai_messages: List[Dict[str, Any]] = []
    system_instruction = body.get("system", "")
    if isinstance(system_instruction, list):
        system_instruction = "\n".join([str(item.get("text", "")) for item in system_instruction if isinstance(item, dict)])
    if isinstance(system_instruction, str) and system_instruction.strip():
        openai_messages.append({"role": "system", "content": _clean_system_prompt(system_instruction)})

    tool_name_map: Dict[str, str] = {}

    for msg in body.get("messages") or []:
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, str):
            openai_messages.append({"role": role, "content": content})
            continue
        if isinstance(content, list):
            text_parts: List[str] = []
            tool_calls: List[Dict[str, Any]] = []
            for block in content:
                b_type = block.get("type")
                if b_type == "text":
                    text_parts.append(str(block.get("text", "")))
                elif b_type in ("tool_use", "agent_use"):
                    t_id = block.get("id")
                    t_name = block.get("name") or block.get("agent_type") or "Task"
                    t_input = block.get("input") or {"prompt": block.get("prompt", "")}
                    if isinstance(t_input, dict):
                        t_input = {k: v for k, v in t_input.items() if v != "" and v is not None}
                    tool_name_map[t_id] = t_name
                    tool_calls.append({
                        "id": t_id,
                        "type": "function",
                        "function": {"name": t_name, "arguments": json.dumps(t_input)},
                    })
                elif b_type in ("tool_result", "agent_result"):
                    t_id = block.get("tool_use_id") or block.get("agent_use_id")
                    t_content = block.get("content", "")
                    if isinstance(t_content, list):
                        extracted = [c.get("text", "") for c in t_content if isinstance(c, dict) and c.get("type") == "text"]
                        t_content = "\n".join(extracted)
                    elif not isinstance(t_content, str):
                        t_content = str(t_content)
                    if not t_content or not t_content.strip():
                        t_content = "(empty)"
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": t_id,
                        "name": tool_name_map.get(t_id, "Task"),
                        "content": t_content,
                    })
                elif b_type in ("thinking", "redacted_thinking"):
                    continue
            if role == "assistant":
                if text_parts or tool_calls:
                    ast_msg: Dict[str, Any] = {"role": "assistant"}
                    combined = "\n".join(text_parts).strip()
                    if combined:
                        ast_msg["content"] = combined
                    if tool_calls:
                        ast_msg["tool_calls"] = tool_calls
                    openai_messages.append(ast_msg)
            elif role == "user":
                combined = "\n".join(text_parts).strip()
                if combined:
                    openai_messages.append({"role": "user", "content": combined})

    if not openai_messages:
        openai_messages.append({"role": "user", "content": "Continue."})
    return openai_messages, openai_tools

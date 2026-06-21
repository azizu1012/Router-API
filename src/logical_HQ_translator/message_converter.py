import re
import json
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.core.config_n_logg import config

class ToolNameCache:
    """
    `ToolNameCache` là một cache LRU (Least Recently Used) để lưu trữ ánh xạ từ ID cuộc gọi công cụ
    (tool call ID) sang tên công cụ (tool name). Điều này rất hữu ích trong quá trình chuyển đổi
    tin nhắn để nhanh chóng tra cứu tên công cụ cho các phản hồi công cụ.

    Cache này có kích thước tối đa cố định (`max_size`) và sử dụng một `threading.Lock`
    để đảm bảo an toàn luồng khi truy cập và sửa đổi cache.

    Attributes:
        max_size (int): Kích thước tối đa của cache. Mặc định là 5000.
        cache (Dict[str, str]): Dictionary lưu trữ các cặp key-value (tool_call_id: tool_name).
        keys_list (List[str]): Danh sách các key theo thứ tự được thêm vào/truy cập để quản lý LRU.
        lock (threading.Lock): Khóa để đồng bộ hóa truy cập cache.
    """
    def __init__(self, max_size=5000):
        self.max_size = max_size
        self.cache = {}
        self.keys_list = []
        self.lock = threading.Lock()

    def set(self, key: str, value: str):
        """
        Thêm hoặc cập nhật một cặp key-value vào cache.
        Nếu cache đạt đến `max_size`, mục cũ nhất sẽ bị loại bỏ.

        Args:
            key (str): ID của cuộc gọi công cụ.
            value (str): Tên của công cụ.
        """
        if not key:
            return
        with self.lock:
            if key in self.cache:
                self.cache[key] = value
                return
            if len(self.cache) >= self.max_size:
                oldest = self.keys_list.pop(0)
                self.cache.pop(oldest, None)
            self.cache[key] = value
            self.keys_list.append(key)

    def get(self, key: str) -> str:
        """
        Lấy tên công cụ từ cache dựa trên ID cuộc gọi công cụ.

        Args:
            key (str): ID của cuộc gọi công cụ.

        Returns:
            str: Tên công cụ nếu tìm thấy, ngược lại là chuỗi rỗng.
        """
        with self.lock:
            return self.cache.get(key) or ""

_GLOBAL_TOOL_NAME_CACHE = ToolNameCache()
"""
Instance toàn cục của `ToolNameCache` được sử dụng để duy trì ánh xạ
tên công cụ trên toàn bộ ứng dụng. Điều này cho phép các phần khác nhau của code
tra cứu tên công cụ một cách nhất quán và hiệu quả.
"""

SCIENCE_TOOLS_TO_STRIP = set(config.TOOLS_TO_STRIP)

UNSUPPORTED_OR_HEAVY_TOOLS = {
    "NotebookRead", "NotebookEdit",
} | SCIENCE_TOOLS_TO_STRIP

# Anthropic tool_use.id pattern: ^[a-zA-Z0-9_-]+$
_TOOL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def sanitize_tool_id(tool_id: str) -> str:
    """Sanitize tool ID to match Anthropic's [a-zA-Z0-9_-] pattern."""
    if not tool_id or not isinstance(tool_id, str):
        return ""
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "", tool_id)
    return sanitized if sanitized else ""


def ensure_tool_ids_in_messages(messages: List[Dict[str, Any]]) -> None:
    """Validate & fix all tool call IDs to match Anthropic pattern (mutates in-place)."""
    for i, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")

        # OpenAI format: role=assistant with tool_calls
        if role == "assistant" and msg.get("tool_calls"):
            for j, tc in enumerate(msg["tool_calls"]):
                tc_id = tc.get("id", "")
                if not tc_id or not _TOOL_ID_PATTERN.match(tc_id):
                    sanitized = sanitize_tool_id(tc_id)
                    fn_name = tc.get("function", {}).get("name", "")
                    tc["id"] = sanitized or f"call_msg{i}_tc{j}_{fn_name}" if fn_name else f"call_msg{i}_tc{j}"
                if tc.get("function", {}).get("arguments") and isinstance(tc["function"]["arguments"], dict):
                    tc["function"]["arguments"] = json.dumps(tc["function"]["arguments"])

        # OpenAI format: role=tool with tool_call_id
        if role == "tool" and msg.get("tool_call_id"):
            tc_id = msg["tool_call_id"]
            if not _TOOL_ID_PATTERN.match(tc_id):
                sanitized = sanitize_tool_id(tc_id)
                msg["tool_call_id"] = sanitized or f"tool_call_{i}"

        # Claude format: content array with tool_use / tool_result
        if isinstance(content, list):
            for k, block in enumerate(content):
                b_type = block.get("type")
                if b_type in ("tool_use", "agent_use"):
                    bid = block.get("id", "")
                    if not _TOOL_ID_PATTERN.match(bid):
                        sanitized = sanitize_tool_id(bid)
                        block["id"] = sanitized or f"tool_msg{i}_block{k}_{block.get('name', 'tool')}"
                if b_type in ("tool_result", "agent_result"):
                    tid = block.get("tool_use_id") or block.get("agent_use_id", "")
                    if tid and not _TOOL_ID_PATTERN.match(tid):
                        sanitized = sanitize_tool_id(tid)
                        new_id = sanitized or f"tool_result_msg{i}_block{k}"
                        if block.get("tool_use_id") is not None:
                            block["tool_use_id"] = new_id
                        if block.get("agent_use_id") is not None:
                            block["agent_use_id"] = new_id


def fix_missing_tool_responses(messages: List[Dict[str, Any]]) -> None:
    """Insert empty tool responses for any tool_call without a matching response (mutates in-place)."""
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant":
            tool_ids = _get_tool_call_ids(msg)
            if not tool_ids:
                i += 1
                continue
            next_msg = messages[i + 1] if i + 1 < len(messages) else None
            responded_ids = _get_tool_response_ids(next_msg)
            missing = [tid for tid in tool_ids if tid not in responded_ids]
            if missing:
                inserted = []
                for tid in missing:
                    inserted.append({"role": "tool", "tool_call_id": tid, "content": ""})
                messages[i + 1:i + 1] = inserted
                i += len(inserted)
        i += 1


def _get_tool_call_ids(msg: Dict[str, Any]) -> List[str]:
    """Extract tool call IDs from assistant message (OpenAI & Claude format)."""
    ids = []
    if msg.get("tool_calls"):
        for tc in msg["tool_calls"]:
            if tc.get("id"):
                ids.append(tc["id"])
    if isinstance(msg.get("content"), list):
        for block in msg["content"]:
            if block.get("type") in ("tool_use", "agent_use") and block.get("id"):
                ids.append(block["id"])
    return ids


def _get_tool_response_ids(msg: Optional[Dict[str, Any]]) -> set:
    """Extract tool response IDs from next message (OpenAI & Claude format)."""
    if not msg:
        return set()
    ids = set()
    if msg.get("role") == "tool" and msg.get("tool_call_id"):
        ids.add(msg["tool_call_id"])
    if isinstance(msg.get("content"), list):
        for block in msg["content"]:
            if block.get("type") in ("tool_result", "agent_result"):
                tid = block.get("tool_use_id") or block.get("agent_use_id")
                if tid:
                    ids.add(tid)
    return ids


def _deep_merge_schemas(dict1: dict, dict2: dict) -> dict:
    res = dict1.copy()
    for k, v in dict2.items():
        if k in res:
            if isinstance(res[k], dict) and isinstance(v, dict):
                res[k] = _deep_merge_schemas(res[k], v)
            elif isinstance(res[k], list) and isinstance(v, list):
                merged_list = list(res[k])
                for item in v:
                    if item not in merged_list:
                        merged_list.append(item)
                res[k] = merged_list
            else:
                res[k] = v
        else:
            res[k] = v
    return res


# JSON Schema keywords NOT supported by google-genai SDK Schema model
# Always remove these before passing to SDK to avoid Pydantic extra_forbidden
UNSUPPORTED_SCHEMA_FIELDS = frozenset({
    "$schema", "$id", "$anchor", "$dynamicRef",
    "definitions", "additionalProperties",
    "propertyNames", "contains", "uniqueItems", "const",
    "if", "then", "else", "not",
    "dependentRequired", "dependentSchemas", "prefixItems",
    "contentMediaType", "contentEncoding",
    "readOnly", "writeOnly", "deprecated",
    "examples",  # plural — SDK chỉ supports singular "example"
    "exclusiveMinimum", "exclusiveMaximum",
    "$comment",
    "allOf",
})
OPENAI_EXTRA_SCHEMA_FIELDS = UNSUPPORTED_SCHEMA_FIELDS


def _convert_const_to_enum(obj: dict) -> None:
    """Convert const to enum (Gemini doesn't support const)."""
    if obj.get("const") is not None and "enum" not in obj:
        obj["enum"] = [obj.pop("const")]


def _convert_enum_values_to_strings(obj: dict) -> None:
    """Gemini requires string enum values + explicit type:string."""
    if "enum" in obj and isinstance(obj["enum"], list):
        obj["enum"] = [str(v) for v in obj["enum"]]
        if "type" not in obj:
            obj["type"] = "string"


def _flatten_type_array(obj: dict) -> None:
    """Flatten e.g. ['string', 'null'] → 'string'."""
    if isinstance(obj.get("type"), list):
        non_null = [t for t in obj["type"] if t != "null"]
        obj["type"] = non_null[0] if non_null else "string"


def _ensure_object_type(obj: dict) -> None:
    """Infer type=object when properties exists (Gemini requirement)."""
    if "properties" in obj and "type" not in obj:
        obj["type"] = "object"


def _clean_required(obj: dict) -> None:
    """Remove required fields not in properties; delete if empty."""
    if "required" in obj and isinstance(obj.get("required"), list) and "properties" in obj:
        valid = [f for f in obj["required"] if f in obj["properties"]]
        if valid:
            obj["required"] = valid
        else:
            del obj["required"]


def _strip_unsupported(obj: dict) -> None:
    """Recursively remove unsupported JSON Schema keywords (mutates in-place)."""
    if not isinstance(obj, dict):
        return
    for k in list(obj.keys()):
        if k in OPENAI_EXTRA_SCHEMA_FIELDS:
            del obj[k]
        elif k.startswith("x-"):
            del obj[k]
        elif isinstance(obj[k], dict):
            _strip_unsupported(obj[k])
        elif isinstance(obj[k], list):
            for item in obj[k]:
                if isinstance(item, dict):
                    _strip_unsupported(item)


def _sanitize_schema_for_gemini(schema: dict) -> dict:
    """Clean JSON Schema for Gemini API compatibility. Returns new dict, does NOT mutate input."""
    if not isinstance(schema, dict):
        return schema

    import copy
    cleaned = copy.deepcopy(schema)

    _convert_const_to_enum(cleaned)
    _convert_enum_values_to_strings(cleaned)
    _flatten_type_array(cleaned)
    _ensure_object_type(cleaned)
    _strip_unsupported(cleaned)
    _clean_required(cleaned)

    return cleaned

def _tool_call_names(tool_calls: List[Dict[str, Any]]) -> str:
    names = [str(tc.get("name", "")).strip() for tc in tool_calls if tc.get("name")]
    return ",".join(names) if names else "-"

def _clean_system_prompt(text: str) -> str:
    text = re.sub(r'claude-sonnet-4-20250514', 'gemini-flash', text)
    text = re.sub(r'claude[- ]sonnet[- ]4(?:[\-\.][\d]+)?', 'gemini-flash', text, flags=re.IGNORECASE)
    text = re.sub(r'claude[- ]opus[- ]4(?:[\-\.][\d]+)?', 'gemini-flash', text, flags=re.IGNORECASE)
    text = re.sub(r'claude[- ]haiku[- ]4(?:[\-\.][\d]+)?', 'gemini-flash-lite', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<![a-zA-Z])Sonnet 4(?:\.[\d]+)?', 'Gemini Flash', text)
    text = re.sub(r'(?<![a-zA-Z])Opus 4(?:\.[\d]+)?', 'Gemini Flash', text)
    text = re.sub(r'(?<![a-zA-Z])Haiku 4(?:\.[\d]+)?', 'Gemini Flash Lite', text)
    text = re.sub(r'Claude 4(?:\.[X\d])?', 'Gemini', text)
    text = re.sub(r'the most recent Claude model family', 'the available model', text)
    text = re.sub(r'(?i)claude (code|models?)', r'Gemini \1', text)
    text = re.sub(r'(?i)(x-)?anthropic', 'google', text)
    text = re.sub(r'(?i)Anthropic\'s', 'Google\'s', text)
    text = re.sub(r'cc_version=[\w.]+;?\s*', '', text)
    text = re.sub(r'cc_entrypoint=\w+;?\s*', '', text)
    text = re.sub(r'cch=\w+;?\s*', '', text)
    text = re.sub(r'(?i)antigravity', 'Gemini', text)

    # Strip scientific/unused tools from system prompt lists
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        is_science_tool_bullet = False
        for s_tool in SCIENCE_TOOLS_TO_STRIP:
            if f"`{s_tool}`" in stripped or (stripped.startswith("-") and s_tool in stripped):
                is_science_tool_bullet = True
                break
        if is_science_tool_bullet:
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    # Clean up empty tool-list headers and footers
    text = re.sub(
        r"The tools you have access to have changed\. You now have access to the following tools:\s*(\n\s*)*You can use these tools to perform tasks in parallel\. Please use them responsibly\.",
        "",
        text
    )

    # Inject agent task delegation guidelines to avoid main-session context bloat
    steering = (
        "\n\n[System Override - Task Delegation & Sub-Agent Orchestration Guidelines]\n"
        "1. For any complex, multi-file, or large-scale tasks (such as auditing code, refactoring multiple modules, "
        "searching directories, or writing reports), you MUST NOT perform all operations directly in this session.\n"
        "2. You MUST decompose the objective into a list of independent sub-tasks.\n"
        "   - If task is small (1-2 steps, few files), use TodoWrite/TodoRead tools inline — do NOT create task.md.\n"
        "   - Only create task.md for large/complex tasks (3+ steps, touching many files).\n"
        "3. You MUST spawn multiple specialized sub-agents in PARALLEL (by returning multiple tool calls to the `Agent` tool "
        "simultaneously in a single assistant turn) to explore different directories or check different modules concurrently. "
        "For example, call the `Agent` tool with tasks for 'src/core', 'src/api', and 'src/backend' in a single response "
        "so they run in parallel, saving time and keeping this main session's context extremely clean.\n"
        "4. Let the sub-agents report their findings back, compile the reports, and tick off completed tasks (use todowrite)."
    )
    return text + steering

def _convert_messages(body: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    from src.logical_HQ_translator.rtk import compress_messages
    compress_messages(body, enabled=True)

    # Sanitize all tool IDs in the request body for Anthropic compatibility
    ensure_tool_ids_in_messages(body.get("messages", []))
    fix_missing_tool_responses(body.get("messages", []))

    openai_tools: List[Dict[str, Any]] = []

    for tool in body.get("tools") or []:
        tool_name = str(tool.get("name", "")).strip()
        if not tool_name or tool_name in UNSUPPORTED_OR_HEAVY_TOOLS:
            continue
        raw_schema = tool.get("input_schema", {})
        if tool_name in ("web_search", "WebSearch"):
            if not raw_schema or not isinstance(raw_schema, dict) or not raw_schema.get("properties"):
                raw_schema = {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query."}
                    },
                    "required": ["query"]
                }
        elif tool_name in ("web_fetch", "WebFetch"):
            if not raw_schema or not isinstance(raw_schema, dict) or not raw_schema.get("properties"):
                raw_schema = {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The URL to fetch."}
                    },
                    "required": ["url"]
                }
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool_name,
                "description": str(tool.get("description", "")),
                "parameters": _sanitize_schema_for_gemini(raw_schema),
            }
        })

    openai_messages: List[Dict[str, Any]] = []
    system_instruction = body.get("system", "")
    if isinstance(system_instruction, list):
        system_instruction = "\n".join([str(item.get("text", "")) for item in system_instruction if isinstance(item, dict)])
    if isinstance(system_instruction, str) and system_instruction.strip():
        import logging
        _log = logging.getLogger("proxy")
        _log.info("[SystemPrompt] raw system instruction (%d chars)", len(system_instruction))
        with open("logs/system_prompt_dump.txt", "a", encoding="utf-8") as spf:
            if any(x in system_instruction.lower() for x in ["anthro", "claude", "skill", "workflow", "agent"]):
                spf.write(f"\n{'='*60}\n[{datetime.now()}]\n{'='*60}\n{system_instruction}\n")
        cleaned = _clean_system_prompt(system_instruction)
        has_wref = any(x in system_instruction.lower() for x in ["web_search", "webfetch", "websearch"])
        if not has_wref:
            cleaned += (
                "\n\n[Search] You have a WebSearch tool that performs real web searches "
                "via DuckDuckGo. When you need current or factual information not in "
                "your training data, ALWAYS call WebSearch in preference to writing "
                "scripts or using curl."
            )
        openai_messages.append({"role": "system", "content": cleaned})

    seen_tool_calls = set()
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
                    _GLOBAL_TOOL_NAME_CACHE.set(t_id, t_name)
                    seen_tool_calls.add(t_id)
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
                    
                    tool_name = tool_name_map.get(t_id) or _GLOBAL_TOOL_NAME_CACHE.get(t_id) or "Task"
                    
                    if t_id not in seen_tool_calls:
                        # Fallback: Convert orphaned tool result to a user message
                        # to avoid schema mismatch and invalid dummy argument validation.
                        openai_messages.append({
                            "role": "user",
                            "content": f"[Tool Result: {tool_name}]\n{t_content}"
                        })
                    else:
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": t_id,
                            "name": tool_name,
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

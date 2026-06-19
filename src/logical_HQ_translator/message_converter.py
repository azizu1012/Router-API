import re
import json
import threading
from datetime import datetime
from typing import Any, Dict, List, Tuple

class ToolNameCache:
    def __init__(self, max_size=5000):
        self.max_size = max_size
        self.cache = {}
        self.keys_list = []
        self.lock = threading.Lock()

    def set(self, key: str, value: str):
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
        with self.lock:
            return self.cache.get(key)

_GLOBAL_TOOL_NAME_CACHE = ToolNameCache()

UNSUPPORTED_OR_HEAVY_TOOLS = {
    "NotebookRead", "NotebookEdit",
}


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
    return text

def _convert_messages(body: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    from src.logical_HQ_translator.rtk import compress_messages
    compress_messages(body, enabled=True)

    openai_tools: List[Dict[str, Any]] = []

    for tool in body.get("tools") or []:
        tool_name = str(tool.get("name", "")).strip()
        if not tool_name or tool_name in UNSUPPORTED_OR_HEAVY_TOOLS:
            continue
        raw_schema = tool.get("input_schema", {})
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

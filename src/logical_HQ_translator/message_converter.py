import re
import json
from typing import Any, Dict, List, Tuple

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


def _sanitize_schema_for_gemini(schema: dict) -> dict:
    if not isinstance(schema, dict):
        return schema
    result = {}
    for k, v in schema.items():
        if k in ("anyOf", "oneOf") and isinstance(v, list):
            first = v[0] if v else {"type": "string"}
            if isinstance(first, dict):
                merged = _sanitize_schema_for_gemini(first)
                result = _deep_merge_schemas(result, merged)
        elif isinstance(v, dict):
            result[k] = _sanitize_schema_for_gemini(v)
        elif isinstance(v, list):
            result[k] = [_sanitize_schema_for_gemini(i) if isinstance(i, dict) else i for i in v]
        else:
            result[k] = v
    return result

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
    return text

def _convert_messages(body: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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

"""Translate canonical request options into GenAI SDK configuration."""

from typing import Any, Dict, List, Optional

from src.core.config_n_logg.logger import logger_system as logger


SAFETY_SETTINGS_OFF = [
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
]


def extract_system_text(system_instruction: Optional[Dict]) -> Optional[str]:
    """Extract plain text from a Gemini systemInstruction dict."""
    if not system_instruction:
        return None
    parts = system_instruction.get("parts", [])
    texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
    return "\n".join(texts) or None


def gemini_tool_config(tool_choice: Any) -> Optional[Dict[str, Any]]:
    """Build Gemini's FunctionCallingConfig from the canonical choice."""
    if tool_choice is None:
        return None

    mode: Optional[str] = None
    names: List[str] = []

    if isinstance(tool_choice, str):
        mode = {"none": "NONE", "auto": "AUTO", "required": "ANY"}.get(tool_choice)
    elif isinstance(tool_choice, dict):
        fn = tool_choice.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            mode = "ANY"
            names.append(str(fn["name"]))
        elif tool_choice.get("name"):
            mode = "ANY"
            names.append(str(tool_choice["name"]))
        else:
            ttype = tool_choice.get("type")
            if ttype in ("auto", "any", "none"):
                mode = {"auto": "AUTO", "any": "ANY", "none": "NONE"}[ttype]
            elif ttype == "required":
                mode = "ANY"
            allowed = tool_choice.get("allowed_tools")
            if isinstance(allowed, list):
                for entry in allowed:
                    if isinstance(entry, str):
                        names.append(entry)
                    elif isinstance(entry, dict) and entry.get("name"):
                        names.append(str(entry["name"]))
                declared = tool_choice.get("mode")
                mode = {"auto": "AUTO", "required": "ANY",
                        "any": "ANY", "none": "NONE"}.get(str(declared), "ANY")

    if not mode:
        return None
    cfg: Dict[str, Any] = {"mode": mode}
    if names and mode == "ANY":
        cfg["allowed_function_names"] = names
    return {"function_calling_config": cfg}


def gemini_response_format(value: Any) -> Optional[Dict[str, Any]]:
    """Map OpenAI response_format to Gemini mime type and response schema."""
    if not isinstance(value, dict):
        return None
    kind = value.get("type")

    if kind == "json_object":
        return {"response_mime_type": "application/json"}

    if kind in ("json_schema", "json"):
        spec = value.get("json_schema")
        if isinstance(spec, dict) and "schema" not in spec:
            spec = {"schema": spec}
        out: Dict[str, Any] = {"response_mime_type": "application/json"}
        if isinstance(spec, dict) and isinstance(spec.get("schema"), dict):
            try:
                from .schema_sanitizer import _sanitize_schema_for_gemini
                out["response_schema"] = _sanitize_schema_for_gemini(spec["schema"])
            except Exception:
                logger.warning("[ResponseFormat] schema could not be sanitized")
        return out

    return None


def build_sdk_config(
    system_instruction: Optional[Dict],
    temperature: Optional[float],
    max_output_tokens: Optional[int],
    tools: Optional[list],
    thinking_config: Optional[Dict],
    generation_config: Optional[Dict] = None,
    tool_choice: Any = None,
    response_format: Any = None,
) -> Dict[str, Any]:
    """Build GenerateContentConfig kwargs for GenAI SDK calls."""
    cfg: Dict[str, Any] = {}
    sys_text = extract_system_text(system_instruction)
    if sys_text:
        cfg["system_instruction"] = sys_text
    if temperature is not None:
        cfg["temperature"] = temperature
    if max_output_tokens is not None:
        cfg["max_output_tokens"] = max_output_tokens
    if tools:
        cfg["tools"] = tools
        tool_config = gemini_tool_config(tool_choice)
        if tool_config:
            cfg["tool_config"] = tool_config
    if thinking_config:
        cfg["thinking_config"] = thinking_config
    response = gemini_response_format(response_format)
    if response:
        cfg.update(response)
    if generation_config:
        for key, value in generation_config.items():
            if key not in cfg:
                cfg[key] = value
    cfg["safety_settings"] = SAFETY_SETTINGS_OFF
    return cfg


__all__ = [
    "SAFETY_SETTINGS_OFF", "extract_system_text", "gemini_tool_config",
    "gemini_response_format", "build_sdk_config",
]


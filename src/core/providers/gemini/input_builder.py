"""Convert canonical chat messages into Gemini-native input structures."""

from typing import Any, Dict, List, Optional


def build_gemini_inputs(
    model_id: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    temperature: Optional[float],
    max_output_tokens: Optional[int],
    reasoning_effort: Optional[str],
    thinking_config: Optional[Dict[str, Any]],
) -> tuple:
    """Convert OpenAI messages to ``(contents, system, tools, thinking)``."""
    from .format import build_gemini_body as build_body
    from .format import convert_messages_to_contents
    from .thinking import build_thinking_config

    resolved_thinking = thinking_config
    if resolved_thinking is None and reasoning_effort:
        is_v3 = "gemini-3" in model_id and "gemini-2" not in model_id
        resolved_thinking = build_thinking_config(
            thinking_level=reasoning_effort, is_v3=is_v3)

    converted = convert_messages_to_contents(messages)
    contents = converted["contents"]
    system_instruction = converted.get("systemInstruction")

    gemini_tools = None
    if tools:
        body = build_body(model_id=model_id, messages=messages, tools=tools)
        gemini_tools = body.get("tools")

    if gemini_tools or any(m.get("tool_calls") for m in messages):
        resolved_thinking = {}

    return contents, system_instruction, gemini_tools, resolved_thinking


__all__ = ["build_gemini_inputs"]




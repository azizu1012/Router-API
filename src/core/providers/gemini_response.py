"""Gemini native response → OpenAI format parser.

9router pattern (gemini-to-openai.js:5-243):
  - content.parts[] → text, reasoning_content, functionCall
  - usageMetadata → usage
  - finishReason → finish_reason

Shared between streaming and non-streaming.
"""

import json
import time
from typing import Any, Dict, List, Optional


def parse_gemini_chunk(chunk: Dict[str, Any], state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse one Gemini response chunk → list of OpenAI delta dicts."""
    response = chunk.get("response", chunk)
    if not response or not response.get("candidates"):
        return []

    candidate = response["candidates"][0]
    content = candidate.get("content", {})
    results: List[Dict[str, Any]] = []

    if not state.get("message_id"):
        state["message_id"] = response.get("responseId", f"msg_{id(chunk)}")
        state["model"] = response.get("modelVersion", "gemini")
        state["function_index"] = 0
        results.append(_make_chunk(state, {"role": "assistant"}, None))

    for part in content.get("parts", []):
        is_thought = part.get("thought") is True
        text = part.get("text", "")
        ts = part.get("thought_signature") or part.get("thoughtSignature")

        if text:
            delta = {}
            if is_thought:
                delta["reasoning_content"] = text
            else:
                delta["content"] = text
            if ts:
                delta["thought_signature"] = ts
            results.append(_make_chunk(state, delta, None))
        elif ts and not part.get("functionCall"):
            delta = {"thought_signature": ts}
            if is_thought:
                delta["reasoning_content"] = ""
            results.append(_make_chunk(state, delta, None))

        fc = part.get("functionCall")
        if fc:
            tool_call_index = state["function_index"]
            state["function_index"] += 1
            tool_call = {
                "id": f"{fc['name']}-{tool_call_index}",
                "index": tool_call_index,
                "type": "function",
                "function": {
                    "name": fc["name"],
                    "arguments": json.dumps(fc.get("args", {})),
                },
            }
            delta = {"tool_calls": [tool_call]}
            if ts:
                delta["thought_signature"] = ts
            results.append(_make_chunk(state, delta, None))

    _extract_usage(response, chunk, state)

    finish_reason = candidate.get("finishReason")
    if finish_reason:
        fr_lower = finish_reason.lower()
        if fr_lower == "stop" and state.get("function_index", 0) > 0:
            fr_lower = "tool_calls"
        final = _make_chunk(state, {}, fr_lower)
        if state.get("usage"):
            final["usage"] = state["usage"]
        results.append(final)

    return results


def parse_gemini_nonstream(response: Dict[str, Any], model_alias: str) -> Dict[str, Any]:
    """Parse Gemini non-stream response → OpenAI response dict."""
    state: Dict[str, Any] = {}
    chunks = parse_gemini_chunk(response, state)

    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    thought_sig_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for chunk in chunks:
        delta = chunk.get("choices", [{}])[0].get("delta", {})
        if delta.get("content"):
            content_parts.append(delta["content"])
        if delta.get("reasoning_content"):
            reasoning_parts.append(delta["reasoning_content"])
        if delta.get("thought_signature"):
            thought_sig_parts.append(delta["thought_signature"])
        if delta.get("tool_calls"):
            tool_calls.extend(delta["tool_calls"])

    message: Dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if thought_sig_parts:
        message["thought_signature"] = "".join(thought_sig_parts)

    finish_reason = "stop"
    for chunk in chunks:
        fr = chunk.get("choices", [{}])[0].get("finish_reason")
        if fr:
            finish_reason = fr
            break

    usage = state.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})

    full_content = "".join(content_parts)
    if usage.get("completion_tokens", 0) == 0 and full_content:
        estimated = max(1, len(full_content) // 4)
        usage = {
            **usage,
            "completion_tokens": estimated,
            "total_tokens": usage.get("prompt_tokens", 0) + estimated,
        }

    return {
        "id": f"chatcmpl-{id(response)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_alias,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage,
    }


def _make_chunk(state: Dict[str, Any], delta: Dict[str, Any], finish_reason: Optional[str]) -> Dict[str, Any]:
    return {
        "id": f"chatcmpl-{state.get('message_id', 'unknown')}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": state.get("model", "gemini"),
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _extract_usage(response: Dict[str, Any], chunk: Dict[str, Any], state: Dict[str, Any]) -> None:
    usage_meta = response.get("usageMetadata") or chunk.get("usageMetadata")
    if not usage_meta:
        return
    cached = usage_meta.get("cachedContentTokenCount", 0) or 0
    prompt_tokens = usage_meta.get("promptTokenCount", 0) or 0
    thoughts_tokens = usage_meta.get("thoughtsTokenCount", 0) or 0
    candidates_tokens = usage_meta.get("candidatesTokenCount", 0) or 0
    total_tokens = usage_meta.get("totalTokenCount", 0) or 0

    if total_tokens > 0 and prompt_tokens > 0:
        actual_completion = total_tokens - prompt_tokens
    else:
        actual_completion = candidates_tokens + thoughts_tokens

    if actual_completion > 0:
        completion_tokens = actual_completion
    else:
        completion_tokens = candidates_tokens + thoughts_tokens

    usage: Dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens if total_tokens > 0 else prompt_tokens + completion_tokens,
    }
    if cached > 0:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    if thoughts_tokens > 0:
        usage["completion_tokens_details"] = {"reasoning_tokens": thoughts_tokens}
    state["usage"] = usage

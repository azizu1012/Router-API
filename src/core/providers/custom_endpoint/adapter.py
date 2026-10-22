"""Custom Endpoint to GenAI Adapter.

Converts between the Gemini native wire shape (google.genai Content / Tool)
and the OpenAI chat shape the custom endpoints speak, in both directions:

  genai_contents_to_openai_messages  history + system -> OpenAI messages,
                                     keeping functionCall / functionResponse
  genai_tools_to_openai_tools        FunctionDeclarations -> OpenAI tools
  openai_tool_calls_to_genai_parts   OpenAI tool_calls -> genai functionCall parts
  openai_result_to_genai_response    OpenAI-style result -> Gemini native dict
  stream_custom_as_genai             custom endpoint SSE -> Gemini native SSE bytes

Part of the library-based modularization.
"""

import json
from typing import Any, AsyncIterator, Dict, List, Optional
from src.core.providers.genai_types import types as gt
from .client import call_custom_nonstream, CustomEndpointStreamGen


def _dump_args(args: Any) -> str:
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(args)


def _parse_args(args: Any) -> Dict[str, Any]:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        text = args.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except ValueError:
            return {"_raw": args}
        return parsed if isinstance(parsed, dict) else {"_raw": args}
    return {}


def genai_contents_to_openai_messages(
    contents: List[gt.Content],
    system_instruction: str = ""
) -> List[Dict[str, Any]]:
    """Convert GenAI contents list and system instruction to OpenAI messages.

    Text parts become content; functionCall parts become assistant tool_calls;
    functionResponse parts become tool-role messages. Call ids are derived
    from the function name (plus a per-message ordinal on repeats) so the call
    and its answer carry the same id on the wire with no out-of-band state.
    """
    openai_messages: List[Dict[str, Any]] = []
    if system_instruction:
        openai_messages.append({"role": "system", "content": system_instruction})
    for c in contents or []:
        role = getattr(c, "role", "") or ""
        msg_role = "assistant" if role == "model" else "user"
        texts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        tool_results: List[Dict[str, Any]] = []
        name_counts: Dict[str, int] = {}
        for p in getattr(c, "parts", None) or []:
            text = getattr(p, "text", None)
            if isinstance(text, str) and text:
                texts.append(text)
            fc = getattr(p, "function_call", None)
            if fc is not None and getattr(fc, "name", None):
                fname = str(fc.name)
                ordinal = name_counts.get(fname, 0)
                name_counts[fname] = ordinal + 1
                if ordinal == 0:
                    call_id = "genai_%s" % fname
                else:
                    call_id = "genai_%s_%d" % (fname, ordinal)
                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": fname,
                        "arguments": _dump_args(getattr(fc, "args", None)),
                    },
                })
            fr = getattr(p, "function_response", None)
            if fr is not None and getattr(fr, "name", None):
                fname = str(fr.name)
                ordinal = name_counts.get(fname, 0)
                name_counts[fname] = ordinal + 1
                if ordinal == 0:
                    call_id = "genai_%s" % fname
                else:
                    call_id = "genai_%s_%d" % (fname, ordinal)
                response = getattr(fr, "response", None) or {}
                if isinstance(response, str):
                    content = response
                else:
                    content = _dump_args(response)
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": content,
                })
        if msg_role == "assistant" and tool_calls:
            openai_messages.append({
                "role": "assistant",
                "content": "\n".join(texts) if texts else None,
                "tool_calls": tool_calls,
            })
        elif tool_results:
            if texts:
                openai_messages.append({"role": msg_role, "content": "\n".join(texts)})
            openai_messages.extend(tool_results)
        else:
            openai_messages.append({"role": msg_role, "content": "\n".join(texts)})
    return openai_messages


def genai_tools_to_openai_tools(tools: List[Any]) -> List[Dict[str, Any]]:
    """Convert a genai Tool list (function declarations) to OpenAI tools."""
    out: List[Dict[str, Any]] = []
    for t in tools or []:
        decls = getattr(t, "function_declarations", None) or []
        for d in decls:
            name = getattr(d, "name", None)
            if not name:
                continue
            fn: Dict[str, Any] = {"name": str(name)}
            desc = getattr(d, "description", None)
            if desc:
                fn["description"] = str(desc)
            schema = getattr(d, "parameters_json_schema", None)
            if schema is None:
                schema = getattr(d, "parameters", None)
            if schema is not None:
                if hasattr(schema, "model_dump"):
                    try:
                        schema = schema.model_dump(exclude_none=True, mode="json")
                    except (TypeError, ValueError):
                        schema = None
                if isinstance(schema, dict):
                    fn["parameters"] = _normalize_schema_types(schema)
            out.append({"type": "function", "function": fn})
    return out


_GENAI_TYPE_FIX = {"OBJECT": "object", "STRING": "string", "NUMBER": "number", "INTEGER": "integer", "BOOLEAN": "boolean", "ARRAY": "array", "NULL": "null"}


def _normalize_schema_types(node: Any) -> Any:
    if isinstance(node, dict):
        fixed: Dict[str, Any] = {}
        for key, value in node.items():
            if key == "type" and isinstance(value, str):
                fixed[key] = _GENAI_TYPE_FIX.get(value, _GENAI_TYPE_FIX.get(value.upper(), value))
            else:
                fixed[key] = _normalize_schema_types(value)
        return fixed
    if isinstance(node, list):
        return [_normalize_schema_types(item) for item in node]
    return node


def openai_tool_calls_to_genai_parts(tool_calls: Any) -> List[Dict[str, Any]]:
    """Convert OpenAI tool_calls (dicts or namespaces) to genai parts."""
    parts: List[Dict[str, Any]] = []
    for tc in tool_calls or []:
        if isinstance(tc, dict):
            fn = tc.get("function", {}) or {}
            name = fn.get("name", "")
            args = fn.get("arguments", "")
        else:
            fn = getattr(tc, "function", None)
            if fn is not None:
                name = getattr(fn, "name", "")
                args = getattr(fn, "arguments", "")
            else:
                name = ""
                args = ""
        if not name:
            continue
        parts.append({"functionCall": {"name": name, "args": _parse_args(args)}})
    return parts


def _openai_finish_to_genai(finish_reason: Any) -> str:
    mapping = {
        "stop": "STOP",
        "length": "MAX_TOKENS",
        "tool_calls": "STOP",
        "content_filter": "SAFETY",
        "function_call": "STOP",
    }
    return mapping.get(str(finish_reason or "").lower(), "STOP")


def openai_result_to_genai_response(
    text: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    tool_calls: Any = None,
    finish_reason: str = "STOP",
) -> Dict[str, Any]:
    """Wrap OpenAI text result and token usage in a Gemini native response dict."""
    parts: List[Dict[str, Any]] = []
    if text:
        parts.append({"text": text})
    parts.extend(openai_tool_calls_to_genai_parts(tool_calls))
    if not parts:
        parts = [{"text": ""}]
    if tool_calls:
        reason = "STOP"
    else:
        reason = _openai_finish_to_genai(finish_reason)
    return {
        "candidates": [
            {
                "content": {
                    "parts": parts,
                    "role": "model"
                },
                "finishReason": reason,
                "index": 0
            }
        ],
        "usageMetadata": {
            "promptTokenCount": input_tokens,
            "candidatesTokenCount": output_tokens,
            "totalTokenCount": input_tokens + output_tokens
        }
    }


async def stream_custom_as_genai(
    api_base: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    temperature: float,
    top_p: float,
    auth_key_prefix: str = "",
    api_format: str = "openai",
    tools: Optional[List[Dict[str, Any]]] = None,
    response_format: Any = None,
    tool_choice: Any = None,
) -> AsyncIterator[bytes]:
    """Stream custom endpoint chunks formatted as Gemini native SSE bytes."""
    gen = CustomEndpointStreamGen(
        api_base=api_base,
        api_key=api_key,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=tools,
        api_format=api_format,
        tool_choice=tool_choice,
        response_format=response_format,
    )

    full_text = ""
    input_tokens = len(str(messages)) // 4
    output_tokens = 0
    tool_frags: Dict[int, Dict[str, str]] = {}
    finish_reason = "stop"

    try:
        async for chunk in gen:
            choices = getattr(chunk, "choices", [])
            if not choices:
                continue
            first = choices[0]
            delta = getattr(first, "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if content:
                full_text += content
                gemini_chunk = {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": content}],
                                "role": "model"
                            },
                            "index": 0
                        }
                    ]
                }
                yield ("data: " + json.dumps(gemini_chunk, ensure_ascii=False) + "\n\n").encode("utf-8")
            for tc in getattr(delta, "tool_calls", None) or []:
                try:
                    idx = int(getattr(tc, "index", 0) or 0)
                except (TypeError, ValueError):
                    idx = 0
                frag = tool_frags.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                tc_id = getattr(tc, "id", None)
                if tc_id:
                    frag["id"] = str(tc_id)
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        frag["name"] = str(fn.name)
                    if getattr(fn, "arguments", None):
                        frag["arguments"] += str(fn.arguments)
            fr = getattr(first, "finish_reason", None)
            if fr:
                finish_reason = str(fr)

            usage = getattr(chunk, "usage", None)
            if usage:
                input_tokens = usage.get("prompt_tokens", input_tokens)
                output_tokens = usage.get("completion_tokens", output_tokens)

        if tool_frags:
            func_parts: List[Dict[str, Any]] = []
            for idx in sorted(tool_frags):
                frag = tool_frags[idx]
                if not frag["name"]:
                    continue
                func_parts.append({
                    "functionCall": {
                        "name": frag["name"],
                        "args": _parse_args(frag["arguments"]),
                    }
                })
            if func_parts:
                call_chunk = {
                    "candidates": [
                        {
                            "content": {
                                "parts": func_parts,
                                "role": "model"
                            },
                            "finishReason": "STOP",
                            "index": 0
                        }
                    ]
                }
                yield ("data: " + json.dumps(call_chunk, ensure_ascii=False) + "\n\n").encode("utf-8")

        if not output_tokens and full_text:
            output_tokens = max(1, len(full_text) // 4)

        final_gemini_chunk = {
            "candidates": [
                {
                    "finishReason": _openai_finish_to_genai(finish_reason),
                    "index": 0
                }
            ],
            "usageMetadata": {
                "promptTokenCount": input_tokens,
                "candidatesTokenCount": output_tokens,
                "totalTokenCount": input_tokens + output_tokens
            }
        }
        yield ("data: " + json.dumps(final_gemini_chunk, ensure_ascii=False) + "\n\n").encode("utf-8")

        from src.core.observability.usage_logger import log_usage
        await log_usage(
            model,
            "custom",
            input_tokens,
            output_tokens,
            auth_key_prefix,
            0,
            0,
        )
    except Exception as e:
        error_payload = {"error": {"message": str(e), "type": "stream_error"}}
        yield ("data: " + json.dumps(error_payload, ensure_ascii=False) + "\n\n").encode("utf-8")

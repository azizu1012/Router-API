"""Custom Endpoint to GenAI Adapter.

This module provides reusable adapters to convert inputs/outputs between
OpenAI-compatible custom endpoints and native Google GenAI SDK protocol.
Part of the library-based modularization.
"""

import json
from typing import Any, AsyncIterator, Dict, List, Optional
from src.core.providers.genai_types import types as gt
from .custom_endpoint_client import call_custom_nonstream, CustomEndpointStreamGen


def genai_contents_to_openai_messages(
    contents: List[gt.Content],
    system_instruction: str = ""
) -> List[Dict[str, Any]]:
    """Convert GenAI contents list & system instruction to OpenAI messages list."""
    openai_messages = []
    if system_instruction:
        openai_messages.append({"role": "system", "content": system_instruction})
    for c in contents:
        role = "assistant" if c.role == "model" else "user"
        text = "".join([getattr(p, "text", "") or "" for p in getattr(c, "parts", []) or []])
        openai_messages.append({"role": role, "content": text})
    return openai_messages


def openai_result_to_genai_response(
    text: str,
    input_tokens: int = 0,
    output_tokens: int = 0
) -> Dict[str, Any]:
    """Wrap OpenAI text result and token usage in a Gemini native response dict."""
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text}],
                    "role": "model"
                },
                "finishReason": "STOP",
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
) -> AsyncIterator[bytes]:
    """Stream OpenAI custom endpoint chunks formatted as Gemini native SSE bytes."""
    gen = CustomEndpointStreamGen(
        api_base=api_base,
        api_key=api_key,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    
    full_text = ""
    input_tokens = len(str(messages)) // 4
    output_tokens = 0

    try:
        async for chunk in gen:
            # chunk is a NativeChunk
            choices = getattr(chunk, "choices", [])
            if not choices:
                continue
            delta = choices[0].delta
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
                yield f"data: {json.dumps(gemini_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
            
            # Extract usage if present in the chunk
            usage = getattr(chunk, "usage", None)
            if usage:
                input_tokens = usage.get("prompt_tokens", input_tokens)
                output_tokens = usage.get("completion_tokens", output_tokens)

        # Yield final metadata chunk
        if not output_tokens and full_text:
            output_tokens = max(1, len(full_text) // 4)
        
        final_gemini_chunk = {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "index": 0
                }
            ],
            "usageMetadata": {
                "promptTokenCount": input_tokens,
                "candidatesTokenCount": output_tokens,
                "totalTokenCount": input_tokens + output_tokens
            }
        }
        yield f"data: {json.dumps(final_gemini_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
        
        # Log usage to telemetry
        from src.core.usage_logger import log_usage
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
        yield f"data: {json.dumps(error_payload, ensure_ascii=False)}\n\n".encode("utf-8")

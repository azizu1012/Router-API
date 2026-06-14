import json as _json
import aiohttp
from typing import Any, AsyncIterator, Dict, List, Optional
from fastapi import HTTPException
from src.core.providers.genai_types import types as gt

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_api
from src.core.providers import api_manager
from src.core.usage_logger import log_usage


async def stream_gemini_native(
    model_alias: str,
    system_instruction: str,
    contents: List[gt.Content],
    max_tokens: int,
    temperature: float,
    top_p: float,
    tools: Optional[List[gt.Tool]],
    image_count: int,
    account: Optional[Dict[str, Any]],
    web_search: bool,
    hybrid_citations: List[Dict[str, Any]],
    auth_key_prefix: str,
    thinking_level: Optional[str] = None,
    thinking_budget: Optional[int] = None,
    include_thoughts: Optional[bool] = None,
) -> AsyncIterator[bytes]:
    try:
        stream_generator = api_manager.call_gemini_stream(
            model_alias=model_alias,
            system_instruction=system_instruction,
            contents=contents,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            tools=tools or None,
            image_count=image_count,
            account=account,
            web_search=web_search,
            thinking_level=thinking_level,
            thinking_budget=thinking_budget,
            include_thoughts=include_thoughts,
        )
        last_chunk_dict = None
        last_chunk_data = None
        actual_model_alias = model_alias
        actual_model_id = model_alias
        actual_api_key = None
        async for chunk_data in stream_generator:
            chunk_dict = chunk_data["response_chunk"]
            actual_model_alias = chunk_data.get("model_alias", model_alias)
            actual_model_id = chunk_data.get("model_id", model_alias)
            actual_api_key = chunk_data.get("api_key")
            last_chunk_data = chunk_data
            if hybrid_citations and chunk_dict.get("candidates") and chunk_dict["candidates"]:
                cand = chunk_dict["candidates"][0]
                if cand.get("finishReason") or chunk_dict.get("usageMetadata"):
                    try:
                        from src.core.providers.search_manager import _format_citations_footer
                        footer = _format_citations_footer(hybrid_citations)
                        if cand.get("content") and cand["content"].get("parts"):
                            first_part = cand["content"]["parts"][0]
                            if "text" in first_part:
                                first_part["text"] = (first_part["text"] or "") + footer
                    except Exception as ge:
                        logger_api.error("Failed to append hybrid citations to stream response: %s", ge)
            last_chunk_dict = chunk_dict
            yield f"data: {_json.dumps(chunk_dict, ensure_ascii=False)}\n\n".encode("utf-8")
        if last_chunk_data:
            last_chunk_dict = last_chunk_data["response_chunk"]
            usage = last_chunk_dict.get("usageMetadata") or last_chunk_dict.get("usage_metadata")
            if usage:
                input_tokens = usage.get("promptTokenCount", 0) or usage.get("prompt_token_count", 0) or 0
                output_tokens = usage.get("candidatesTokenCount", 0) or usage.get("candidates_token_count", 0) or 0
                actual_model_id = last_chunk_data.get("model_id") or actual_model_alias
                await log_usage(
                    actual_model_id,
                    (actual_api_key or "")[-8:] if actual_api_key else "",
                    input_tokens,
                    output_tokens,
                    auth_key_prefix,
                    0,
                    0,
                )
    except RuntimeError as e:
        error_message = str(e)
        logger_api.error("Streaming failed: %s", error_message)
        error_payload = {"error": {"message": error_message, "type": "stream_error"}}
        yield f"data: {_json.dumps(error_payload, ensure_ascii=False)}\n\n".encode("utf-8")
    except HTTPException as he:
        error_payload = {"error": {"message": str(he.detail), "type": "http_error"}}
        yield f"data: {_json.dumps(error_payload, ensure_ascii=False)}\n\n".encode("utf-8")
    except Exception as e:
        logger_api.error("Unhandled exception during streaming: %s", e)
        error_payload = {"error": {"message": str(e), "type": "server_error"}}
        yield f"data: {_json.dumps(error_payload, ensure_ascii=False)}\n\n".encode("utf-8")


async def stream_custom_endpoint_native(
    base_url: str,
    auth_key: str,
    model: str,
    messages: list,
    max_tokens: int,
    temperature: float,
    top_p: float,
    auth_key_prefix: str,
) -> AsyncIterator[bytes]:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {auth_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "stream": True,
    }
    input_tokens = 0
    output_tokens = 0
    full_text = ""
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT_SECONDS)) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    error_payload = {"error": {"message": f"Custom endpoint returned HTTP {resp.status}: {err_text}", "type": "stream_error"}}
                    yield f"data: {_json.dumps(error_payload, ensure_ascii=False)}\n\n".encode("utf-8")
                    return
                async for line in resp.content:
                    line = line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = _json.loads(data_str)
                    except _json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    for ch in choices:
                        delta = ch.get("delta", {})
                        content = delta.get("content")
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
                            yield f"data: {_json.dumps(gemini_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
                    usage = chunk.get("usage")
                    if usage:
                        input_tokens = usage.get("prompt_tokens", 0) or 0
                        output_tokens = usage.get("completion_tokens", 0) or 0
        if not output_tokens and full_text:
            output_tokens = max(1, len(full_text) // 4)
        if not input_tokens:
            input_tokens = len(str(messages)) // 4
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
        yield f"data: {_json.dumps(final_gemini_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
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
        logger_api.error("Stream custom endpoint failed: %s", e)
        error_payload = {"error": {"message": str(e), "type": "stream_error"}}
        yield f"data: {_json.dumps(error_payload, ensure_ascii=False)}\n\n".encode("utf-8")

import base64
import copy
import json
from typing import Any, Dict, AsyncIterator, List, Optional
from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from google.genai import types as gt

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_api
from src.core.router import router
from src.core.providers import api_manager
from src.core.usage_logger import log_usage
from src.core.limits import account_limiter
from src.server.openai_server.auth import _check_auth, _auth_key_prefix
from src.server.openai_server.routes.app_init import app

def _resolve_gemini_auth(
    request: Request,
    authorization: str | None = None,
    x_api_key: str | None = None,
    x_goog_api_key: str | None = None,
) -> str | None:
    # 1. Check query parameter 'key'
    key_param = request.query_params.get("key")
    if key_param:
        return f"Bearer {key_param}"
    # 2. Check x-goog-api-key header
    if x_goog_api_key and x_goog_api_key.strip():
        return f"Bearer {x_goog_api_key.strip()}"
    # 3. Check x-api-key header
    if x_api_key and x_api_key.strip():
        return f"Bearer {x_api_key.strip()}"
    # 4. Check standard Authorization header
    if authorization and authorization.strip():
        return authorization
    return None

def _parse_gemini_contents(raw_contents: list) -> List[gt.Content]:
    contents = []
    for c in raw_contents or []:
        if not isinstance(c, dict):
            continue
        role = c.get("role")
        parts = []
        for p in c.get("parts") or []:
            if not isinstance(p, dict):
                continue
            if "text" in p:
                parts.append(gt.Part.from_text(text=p["text"]))
            elif "inlineData" in p or "inline_data" in p:
                inline = p.get("inlineData") or p.get("inline_data") or {}
                mime_type = inline.get("mimeType") or inline.get("mime_type")
                data_b64 = inline.get("data")
                if mime_type and data_b64:
                    data = base64.b64decode(data_b64)
                    parts.append(gt.Part.from_bytes(data=data, mime_type=mime_type))
            elif "fileData" in p or "file_data" in p:
                file_info = p.get("fileData") or p.get("file_data") or {}
                parts.append(gt.Part(
                    file_data=gt.FileData(
                        file_uri=file_info.get("fileUri") or file_info.get("file_uri"),
                        mime_type=file_info.get("mimeType") or file_info.get("mime_type")
                    )
                ))
            elif "functionCall" in p or "function_call" in p:
                fc = p.get("functionCall") or p.get("function_call") or {}
                name = fc.get("name")
                args = fc.get("args") or {}
                if name:
                    parts.append(gt.Part.from_function_call(name=name, args=args))
            elif "functionResponse" in p or "function_response" in p:
                fr = p.get("functionResponse") or p.get("function_response") or {}
                name = fr.get("name")
                response = fr.get("response") or {}
                if name:
                    parts.append(gt.Part.from_function_response(name=name, response=response))
        contents.append(gt.Content(role=role, parts=parts))
    return contents

def _parse_gemini_tools(raw_tools: list) -> List[gt.Tool]:
    tools = []
    for t in raw_tools or []:
        if not isinstance(t, dict):
            continue
        if "googleSearch" in t or "google_search" in t:
            tools.append(gt.Tool(google_search=gt.GoogleSearch()))
        elif "functionDeclarations" in t or "function_declarations" in t:
            decls = t.get("functionDeclarations") or t.get("function_declarations") or []
            func_decls = []
            for d in decls:
                func_decls.append(gt.FunctionDeclaration(**d))
            tools.append(gt.Tool(function_declarations=func_decls))
    return tools

async def _stream_gemini_native_real(
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

            # Append citations to the last chunk (the one with usageMetadata or finishReason)
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
            yield f"data: {json.dumps(chunk_dict, ensure_ascii=False)}\n\n".encode("utf-8")

        # Log usage after streaming completes (last chunk has usageMetadata)
        if last_chunk_data:
            last_chunk_dict = last_chunk_data["response_chunk"]
            usage = last_chunk_dict.get("usageMetadata") or last_chunk_dict.get("usage_metadata")
            if usage:
                input_tokens = usage.get("promptTokenCount", 0) or usage.get("prompt_token_count", 0) or 0
                output_tokens = usage.get("candidatesTokenCount", 0) or usage.get("candidates_token_count", 0) or 0
                # Use actual model_id for accurate pricing lookup
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
        yield f"data: {json.dumps(error_payload, ensure_ascii=False)}\n\n".encode("utf-8")
    except HTTPException as he:
        error_payload = {"error": {"message": str(he.detail), "type": "http_error"}}
        yield f"data: {json.dumps(error_payload, ensure_ascii=False)}\n\n".encode("utf-8")
    except Exception as e:
        logger_api.error("Unhandled exception during streaming: %s", e)
        error_payload = {"error": {"message": str(e), "type": "server_error"}}
        yield f"data: {json.dumps(error_payload, ensure_ascii=False)}\n\n".encode("utf-8")

async def _handle_gemini_native(
    model_id: str,
    request: Request,
    authorization: str | None,
    x_api_key: str | None,
    x_goog_api_key: str | None,
    stream: bool
):
    auth_val = _resolve_gemini_auth(request, authorization, x_api_key, x_goog_api_key)
    account = _check_auth(auth_val)
    
    body = await request.json()
    model_alias = model_id.split("/")[-1]
    
    # 1. Parse contents & tools
    raw_contents = body.get("contents") or []
    contents = _parse_gemini_contents(raw_contents)
    if not contents:
        contents = [gt.Content(role="user", parts=[gt.Part.from_text(text="Hi")])]
        
    raw_tools = body.get("tools") or []
    tools = _parse_gemini_tools(raw_tools)
    
    # 2. Extract system instruction
    sys_inst = body.get("systemInstruction") or body.get("system_instruction")
    system_instruction = ""
    if sys_inst:
        parts = sys_inst.get("parts") or []
        system_instruction = "".join([str(p.get("text") or "") for p in parts if isinstance(p, dict)]).strip()
        
    # 3. Generation config
    gen_config = body.get("generationConfig") or body.get("generation_config") or {}
    temperature = float(gen_config.get("temperature", 0.7))
    top_p = float(gen_config.get("topP") or gen_config.get("top_p", 0.95))
    max_tokens = int(gen_config.get("maxOutputTokens") or gen_config.get("max_output_tokens") or config.MAX_OUTPUT_TOKENS)
    max_tokens = max(1, min(max_tokens, config.MAX_OUTPUT_TOKENS))
    
    # 4. Count image inputs
    image_count = sum(
        1 for c in contents
        for p in getattr(c, "parts", []) or []
        if getattr(p, "inline_data", None) is not None or getattr(p, "file_data", None) is not None
    )
    
    # 5. Extract prompt text and count/estimate input tokens
    prompt_text = api_manager._flatten_contents_text(contents)
    
    is_lite = "lite" in model_alias.lower()
    limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
    estimated_input_tokens = max(1, len(prompt_text) // 4) + (image_count * 258)
    
    while len(contents) > 2 and estimated_input_tokens > limit:
        contents.pop(0)
        image_count = sum(
            1 for c in contents
            for p in getattr(c, "parts", []) or []
            if getattr(p, "inline_data", None) is not None or getattr(p, "file_data", None) is not None
        )
        prompt_text = api_manager._flatten_contents_text(contents)
        estimated_input_tokens = max(1, len(prompt_text) // 4) + (image_count * 258)
        
    # 6. Apply account rate limit
    pool_type = "lite" if is_lite else "flash"
    from src.core.limits.account_limiter import get_effective_limits_by_pool
    eff_rpm, eff_tpm, eff_rpd = await get_effective_limits_by_pool(account, pool_type)
    
    effective = dict(account)
    effective["rpm"] = eff_rpm
    effective["tpm"] = eff_tpm
    effective["rpd"] = eff_rpd
    
    allowed, reason = await account_limiter.acquire(effective, estimated_input_tokens + max_tokens, pool_type)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"error": {"message": f"Account rate limit exceeded: {reason}", "type": "rate_limit_error"}}
        )
        
    # 7. Check for web search tool
    web_search = False
    has_media = image_count > 0 or api_manager._has_media_or_files(contents)
    auth_key_prefix = _auth_key_prefix(account)

    for tool in tools:
        if getattr(tool, "google_search", None) is not None:
            web_search = True
            
    # Respect explicit client-level disable override
    explicit_disable = False
    for flag in ["web_search", "search", "google_search", "grounding"]:
        if flag in body and body[flag] is False:
            explicit_disable = True
            break
            
    if explicit_disable:
        web_search = False
        tools = [t for t in tools if getattr(t, "google_search", None) is None]
    else:
        if not web_search and account and account.get("web_search_enabled"):
            web_search = True
            
    target_model_id = router.get_model_id(model_alias)
    is_lite = "lite" in model_alias.lower() or "lite" in target_model_id.lower()
    can_native_ground = (
        web_search
        and not has_media
        and is_lite
        and api_manager.model_supports_grounding(target_model_id)
    )
    native_grounding_active = can_native_ground
    
    hybrid_citations = []
    if web_search and not can_native_ground:
        from src.core.providers.search_manager import extract_search_queries, execute_hybrid_search
        
        history_messages = []
        for c in contents:
            role = "assistant" if c.role == "model" else "user"
            text = "".join([getattr(p, "text", "") or "" for p in getattr(c, "parts", []) or []])
            history_messages.append({"role": role, "content": text})
            
        try:
            queries = await extract_search_queries(prompt_text, history_messages, auth_key_prefix, account=account)
        except Exception as qerr:
            logger_api.warning("[Grounding] extract_search_queries failed (%s), skipping grounding.", qerr)
            queries = []
        if queries:
            try:
                search_results, hybrid_citations = await execute_hybrid_search(queries, auth_key_prefix, account=account)
            except Exception as serr:
                logger_api.warning("[Grounding] execute_hybrid_search failed (%s), skipping grounding.", serr)
                search_results, hybrid_citations = "", []
            if search_results:
                from datetime import datetime
                current_time_str = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p")
                context_block = (
                    "\n\n[Search Context from Google Search Grounding]\n"
                    f"Current Time: {current_time_str}\n"
                    "Use the following real-time web search results to answer the user request:\n"
                    f"{search_results}\n"
                    "[End of Search Context]"
                )
                system_instruction = (system_instruction + context_block) if system_instruction else context_block.strip()
                
    # 8. Execute Gemini Call
    if stream:
        return StreamingResponse(_stream_gemini_native_real(
            model_alias=model_alias,
            system_instruction=system_instruction,
            contents=contents,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            tools=tools or None,
            image_count=image_count,
            account=account,
            web_search=native_grounding_active,
            hybrid_citations=hybrid_citations,
            auth_key_prefix=auth_key_prefix,
        ), media_type="text/event-stream")

    gresult = await api_manager.call_gemini(
        model_alias=model_alias,
        system_instruction=system_instruction,
        contents=contents,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        tools=tools or None,
        image_count=image_count,
        account=account,
        web_search=native_grounding_active,
    )
    
    # 9. Format response payload safely (using model_dump_json to serialize bytes to base64 strings)
    response_obj = gresult["response"]
    resp_json = response_obj.model_dump_json(by_alias=True, exclude_none=True)
    resp_dict = json.loads(resp_json)
    
    try:
        candidates = resp_dict.get("candidates") or []
        if candidates and "content" in candidates[0]:
            parts = candidates[0]["content"].get("parts") or []
            if parts and "text" in parts[0]:
                text = parts[0]["text"]
                footer = None
                if hybrid_citations:
                    from src.core.providers.search_manager import _format_citations_footer
                    footer = _format_citations_footer(hybrid_citations)
                else:
                    grounding = candidates[0].get("groundingMetadata")
                    if grounding:
                        chunks = grounding.get("groundingChunks") or []
                        native_citations = []
                        for chunk in chunks:
                            web = chunk.get("web")
                            if web:
                                title = web.get("title") or "Source"
                                uri = web.get("uri")
                                if uri:
                                    native_citations.append({"title": title, "url": uri})
                        if native_citations:
                            from src.core.providers.search_manager import _format_citations_footer
                            footer = _format_citations_footer(native_citations)
                if footer:
                    text += footer
                parts[0]["text"] = text
    except Exception as ge:
        logger_api.error("Failed to append hybrid citations to native response: %s", ge)
        
    # 10. Usage Logging
    auth_key_prefix = _auth_key_prefix(account)
    input_tokens = gresult.get("input_tokens", 0) or 0
    output_tokens = gresult.get("output_tokens", 0) or 0
    # Use actual model_id for accurate pricing lookup
    actual_model_id = gresult.get("model_id") or gresult.get("model_alias") or model_alias
    await log_usage(
        actual_model_id,
        (gresult.get("api_key") or "")[-8:],
        input_tokens,
        output_tokens,
        auth_key_prefix,
        0,
        0,
    )
    
    return JSONResponse(content=resp_dict)

@app.post("/v1beta/models/{model_id:path}:generateContent")
@app.post("/v1alpha/models/{model_id:path}:generateContent")
@app.post("/v1/models/{model_id:path}:generateContent")
async def generate_content(
    model_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_goog_api_key: str | None = Header(default=None),
):
    return await _handle_gemini_native(model_id, request, authorization, x_api_key, x_goog_api_key, stream=False)

@app.post("/v1beta/models/{model_id:path}:streamGenerateContent")
@app.post("/v1alpha/models/{model_id:path}:streamGenerateContent")
@app.post("/v1/models/{model_id:path}:streamGenerateContent")
async def stream_generate_content(
    model_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_goog_api_key: str | None = Header(default=None),
):
    return await _handle_gemini_native(model_id, request, authorization, x_api_key, x_goog_api_key, stream=True)

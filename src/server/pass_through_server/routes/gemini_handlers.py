import json
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from src.core.providers.genai_types import types as gt

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_api
from src.core.router import router
from src.core.providers import api_manager
from src.core.usage_logger import log_usage
from src.core.limits import account_limiter
from src.server.openai_server.auth import _check_auth, _auth_key_prefix
from .gemini_parsers import resolve_gemini_auth, parse_gemini_contents, parse_gemini_tools
from .gemini_streaming import stream_gemini_native, stream_custom_endpoint_native


async def _handle_gemini_native(
    model_id: str,
    request: Request,
    authorization: str | None,
    x_api_key: str | None,
    x_goog_api_key: str | None,
    stream: bool
):
    auth_val = resolve_gemini_auth(request, authorization, x_api_key, x_goog_api_key)
    account = _check_auth(auth_val)
    body = await request.json()
    model_alias = model_id.split("/")[-1]
    raw_contents = body.get("contents") or []
    contents = parse_gemini_contents(raw_contents)
    if not contents:
        contents = [gt.Content(role="user", parts=[gt.Part.from_text(text="Hi")])]
    raw_tools = body.get("tools") or []
    tools = parse_gemini_tools(raw_tools)
    sys_inst = body.get("systemInstruction") or body.get("system_instruction")
    system_instruction = ""
    if sys_inst:
        parts = sys_inst.get("parts") or []
        system_instruction = "".join([str(p.get("text") or "") for p in parts if isinstance(p, dict)]).strip()
    gen_config = body.get("generationConfig") or body.get("generation_config") or {}
    temperature = float(gen_config.get("temperature", 0.7))
    top_p = float(gen_config.get("topP") or gen_config.get("top_p", 0.95))
    max_tokens = int(gen_config.get("maxOutputTokens") or gen_config.get("max_output_tokens") or config.MAX_OUTPUT_TOKENS)
    max_tokens = max(1, min(max_tokens, config.MAX_OUTPUT_TOKENS))
    tcfg = gen_config.get("thinkingConfig") or gen_config.get("thinking_config") or {}
    thinking_level = tcfg.get("thinkingLevel") or tcfg.get("thinking_level")
    thinking_budget = tcfg.get("thinkingBudget")
    if thinking_budget is None:
        thinking_budget = tcfg.get("thinking_budget")
    include_thoughts = tcfg.get("includeThoughts") if tcfg.get("includeThoughts") is not None else tcfg.get("include_thoughts")
    image_count = sum(
        1 for c in contents
        for p in getattr(c, "parts", []) or []
        if getattr(p, "inline_data", None) is not None or getattr(p, "file_data", None) is not None
    )
    prompt_text = api_manager._flatten_contents_text(contents)
    is_lite = "lite" in model_alias.lower()
    
    # Resolve the model's configured context length limit
    requested_model = model_alias
    from src.core.api_config import AVAILABLE_MODELS, MODEL_CONTEXT_LENGTH
    model_alias_resolved = router.resolve_model_alias(requested_model)
    model_cfg = AVAILABLE_MODELS.get(model_alias_resolved)
    limit_tokens = model_cfg.get("context_length", MODEL_CONTEXT_LENGTH) if model_cfg else MODEL_CONTEXT_LENGTH
    
    limit = limit_tokens  # Truncate messages to match the specific model's context length limit
    
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

    # Active Reject: check if the estimated input tokens still exceed the model's context length limit
    if estimated_input_tokens > limit_tokens:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Your request's input tokens ({estimated_input_tokens}) exceeds the model's configured context length limit of {limit_tokens}.",
                    "status": "INVALID_ARGUMENT"
                }
            }
        )
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
    web_search = False
    has_media = image_count > 0 or api_manager._has_media_or_files(contents)
    auth_key_prefix = _auth_key_prefix(account)
    for tool in tools:
        if getattr(tool, "google_search", None) is not None:
            web_search = True
    explicit_disable = False
    for flag in ["web_search", "search", "google_search", "grounding"]:
        if flag in body and body[flag] is False:
            explicit_disable = True
            break
    if explicit_disable:
        web_search = False
        tools = [t for t in tools if getattr(t, "google_search", None) is None]
    else:
        if not web_search and account and (account.get("web_search_enabled") or account.get("search_engine", "auto") != "disabled"):
            web_search = True
    target_model_id = router.get_model_id(model_alias)
    is_lite = "lite" in model_alias.lower() or "lite" in target_model_id.lower()
    can_native_ground = (
        web_search
        and not has_media
        and is_lite
        and ("gemini" in target_model_id.lower())
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
    from src.core.providers.custom_endpoint_manager import _custom_endpoint_manager
    for ep in _custom_endpoint_manager.get_endpoints_for_account(account):
        if not ep.get("enabled", True):
            continue
        model_to_use = model_alias
        pool_assignments = ep.get("pool_assignments", {})
        if model_alias in pool_assignments:
            model_to_use = pool_assignments[model_alias]
        enabled_models = ep.get("enabled_models", [])
        if model_to_use in enabled_models:
            from src.core.providers.custom_endpoint_genai_adapter import (
                genai_contents_to_openai_messages,
                openai_result_to_genai_response,
            )
            openai_messages = genai_contents_to_openai_messages(contents, system_instruction)
            try:
                if stream:
                    return StreamingResponse(
                        stream_custom_endpoint_native(
                            base_url=ep["base_url"],
                            auth_key=ep["auth_key"],
                            model=model_to_use,
                            messages=openai_messages,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            top_p=top_p,
                            auth_key_prefix=auth_key_prefix,
                        ),
                        media_type="text/event-stream"
                    )
                else:
                    result = await _custom_endpoint_manager._call_chat(
                        base_url=ep["base_url"],
                        auth_key=ep["auth_key"],
                        model=model_to_use,
                        messages=openai_messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p,
                        stream=False,
                        timeout=config.REQUEST_TIMEOUT_SECONDS,
                        endpoint_name=ep.get("name", ""),
                    )
                    resp_dict = openai_result_to_genai_response(
                        result["text"],
                        result.get("input_tokens", 0),
                        result.get("output_tokens", 0),
                    )
                    await log_usage(
                        model_to_use,
                        "custom",
                        result.get("input_tokens", 0),
                        result.get("output_tokens", 0),
                        auth_key_prefix,
                        0,
                        0,
                    )
                    return JSONResponse(content=resp_dict)
            except Exception as e:
                logger_api.warning("[AccountEndpoint Pass-through] %s failed (%s), trying next endpoint", model_alias, e)
    if stream:
        return StreamingResponse(stream_gemini_native(
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
            thinking_level=thinking_level,
            thinking_budget=thinking_budget,
            include_thoughts=include_thoughts,
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
        thinking_level=thinking_level,
        thinking_budget=thinking_budget,
        include_thoughts=include_thoughts,
    )
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
    auth_key_prefix = _auth_key_prefix(account)
    input_tokens = gresult.get("input_tokens", 0) or 0
    output_tokens = gresult.get("output_tokens", 0) or 0
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

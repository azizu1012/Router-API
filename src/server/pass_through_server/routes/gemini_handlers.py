import asyncio
import json
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from src.core.providers.genai_types import types as gt

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_api
from src.core.router import router
from src.core.providers import api_manager
from src.core.providers.gemini import GeminiBadRequestError
from src.core.observability.usage_logger import log_usage
from src.core.limits import account_limiter
from src.server.openai_server.auth import _check_auth, _auth_key_prefix
from .gemini_parsers import resolve_gemini_auth, parse_gemini_contents, parse_gemini_tools
from .gemini_error import detail_message as _detail_message
from .gemini_error import gemini_error_body as _gemini_error_body
from .gemini_streaming import stream_gemini_native, stream_custom_endpoint_native


def gemini_error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_gemini_error_body(status_code, message))


def _classify_native_failure(exc: Exception) -> tuple[int, str]:
    """Map an internal failure onto the status Google would have returned.

    Without this, anything raised below reached Starlette unhandled and came
    back as `500 Internal Server Error` with a `text/plain` body — which a
    native Gemini client cannot parse at all, so the only thing the caller saw
    was a decode failure instead of the reason.
    """
    msg = str(exc) or exc.__class__.__name__

    if isinstance(exc, HTTPException):
        return exc.status_code, _detail_message(exc.detail)
    if isinstance(exc, GeminiBadRequestError):
        return 400, msg
    if "no_available_key" in msg or "quota_exhausted" in msg:
        return 429, "All API keys are temporarily unavailable, retry later"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return 504, "Upstream request timed out"
    return 500, msg


async def _handle_gemini_native(
    model_id: str,
    request: Request,
    authorization: str | None,
    x_api_key: str | None,
    x_goog_api_key: str | None,
    stream: bool
):
    try:
        return await _handle_gemini_native_inner(
            model_id, request, authorization, x_api_key, x_goog_api_key, stream)
    except HTTPException:
        raise
    except Exception as exc:
        status, message = _classify_native_failure(exc)
        logger_api.error("[GeminiNative] %s -> HTTP %s: %s",
                         model_id, status, message, exc_info=True)
        return gemini_error_response(status, message)


async def _handle_gemini_native_inner(
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
    from src.core.config.api_config import AVAILABLE_MODELS, MODEL_CONTEXT_LENGTH
    model_alias_resolved = router.resolve_model_alias(requested_model)
    if not model_alias_resolved:
        return gemini_error_response(404, f"Model models/{requested_model} not found.")
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
    if account.get("name") not in {"anonymous", "legacy-env-token"}:
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
    web_search = any(
        isinstance(tool, dict) and ("googleSearch" in tool or "google_search" in tool)
        for tool in raw_tools
    ) or any(body.get(flag) is True for flag in ("web_search", "search", "google_search", "grounding"))
    auth_key_prefix = _auth_key_prefix(account)
    # Whether the caller asked for search, as opposed to the account-level
    # default turning it on below. The distinction decides what happens when
    # the search backend is missing: a request that never asked for search must
    # still be answered, one that did has to be told it cannot be served.
    explicit_disable = False
    for flag in ["web_search", "search", "google_search", "grounding"]:
        if flag in body and body[flag] is False:
            explicit_disable = True
            break
    if explicit_disable:
        web_search = False
        tools = [t for t in tools if getattr(t, "google_search", None) is None]
    hybrid_citations = []
    if web_search and prompt_text.strip():
        from src.tools.search.orchestration.manager import execute_hybrid_search
        search_context, hybrid_citations = await execute_hybrid_search(
            [prompt_text.strip()], auth_key_prefix=auth_key_prefix, account=account
        )
        if search_context:
            system_instruction = f"{system_instruction}\n\n{search_context}".strip()
    from src.core.providers.custom_endpoint.manager import _custom_endpoint_manager
    for ep in _custom_endpoint_manager.get_endpoints_for_account(account):
        if not ep.get("enabled", True):
            continue
        model_to_use = model_alias
        pool_assignments = ep.get("pool_assignments", {})
        if model_alias in pool_assignments:
            model_to_use = pool_assignments[model_alias]
        enabled_models = ep.get("enabled_models", [])
        if model_to_use in enabled_models:
            from src.core.providers.custom_endpoint.adapter import (
                genai_contents_to_openai_messages,
                genai_tools_to_openai_tools,
                openai_result_to_genai_response,
            )
            openai_messages = genai_contents_to_openai_messages(contents, system_instruction)
            openai_tools = genai_tools_to_openai_tools(tools)
            mime = gen_config.get("responseMimeType") or gen_config.get("response_mime_type") or ""
            if isinstance(mime, str) and mime.strip().lower() == "application/json":
                openai_response_format = {"type": "json_object"}
            else:
                openai_response_format = None
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
                            api_format=ep.get("api_format") or "openai",
                            tools=openai_tools or None,
                            response_format=openai_response_format,
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
                        timeout=int(ep.get("timeout") or config.REQUEST_TIMEOUT_SECONDS),
                        endpoint_name=ep.get("name", ""),
                        tools=openai_tools or None,
                        response_format=openai_response_format,
                    )
                    resp_dict = openai_result_to_genai_response(
                        result["text"],
                        result.get("input_tokens", 0),
                        result.get("output_tokens", 0),
                        tool_calls=result.get("tool_calls"),
                        finish_reason=result.get("finish_reason") or "STOP",
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
            web_search=web_search,
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
        web_search=web_search,
        thinking_level=thinking_level,
        thinking_budget=thinking_budget,
        include_thoughts=include_thoughts,
    )
    response_obj = gresult["response"]
    # Strip the SDK's own bookkeeping; `sdk_http_response` is the live httpx
    # response and has no business in a body a client parses.
    from src.core.providers.gemini.response_dump import dump_generation_response
    resp_dict = dump_generation_response(response_obj)
    try:
        candidates = resp_dict.get("candidates") or []
        if candidates and "content" in candidates[0]:
            parts = candidates[0]["content"].get("parts") or []
            if parts and "text" in parts[0]:
                text = parts[0]["text"]
                footer = None
                if hybrid_citations:
                    from src.tools.search.orchestration.manager import _format_citations_footer
                    footer = _format_citations_footer(hybrid_citations)
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


def _extract_embed_config(src: dict) -> dict:
    cfg: dict = {}
    for key in (
        "taskType", "task_type",
        "title",
        "outputDimensionality", "output_dimensionality",
        "mimeType", "mime_type",
        "autoTruncate", "auto_truncate",
    ):
        if src.get(key) is not None:
            cfg[key] = src[key]
    return cfg


def _coerce_embed_content(raw: object) -> list:
    if isinstance(raw, str):
        return [gt.Content(role="user", parts=[gt.Part.from_text(text=raw)])]
    if isinstance(raw, dict):
        return parse_gemini_contents([raw])
    if isinstance(raw, list):
        return parse_gemini_contents(raw)
    return []


async def _acquire_native_account_quota(account: dict, estimated_tokens: int, is_lite: bool = False) -> None:
    if account.get("name") in {"anonymous", "legacy-env-token"}:
        return
    pool_type = "lite" if is_lite else "flash"
    from src.core.limits.account_limiter import get_effective_limits_by_pool
    eff_rpm, eff_tpm, eff_rpd = await get_effective_limits_by_pool(account, pool_type)
    effective = dict(account)
    effective["rpm"] = eff_rpm
    effective["tpm"] = eff_tpm
    effective["rpd"] = eff_rpd
    allowed, reason = await account_limiter.acquire(effective, estimated_tokens, pool_type)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"error": {"message": f"Account rate limit exceeded: {reason}", "type": "rate_limit_error"}},
        )


async def _handle_gemini_embed(
    model_id: str,
    request: Request,
    authorization: str | None,
    x_api_key: str | None,
    x_goog_api_key: str | None,
    batch: bool = False,
):
    try:
        return await _handle_gemini_embed_inner(
            model_id, request, authorization, x_api_key, x_goog_api_key, batch=batch
        )
    except HTTPException:
        raise
    except Exception as exc:
        status, message = _classify_native_failure(exc)
        logger_api.error("[GeminiEmbed] %s -> HTTP %s: %s", model_id, status, message, exc_info=True)
        return gemini_error_response(status, message)


async def _handle_gemini_embed_inner(
    model_id: str,
    request: Request,
    authorization: str | None,
    x_api_key: str | None,
    x_goog_api_key: str | None,
    batch: bool = False,
):
    auth_val = resolve_gemini_auth(request, authorization, x_api_key, x_goog_api_key)
    account = _check_auth(auth_val)
    try:
        body = await request.json()
    except Exception:
        return gemini_error_response(400, "Invalid JSON request body")
    if not isinstance(body, dict):
        return gemini_error_response(400, "Request body must be a JSON object")

    model_alias = router.resolve_model_alias(model_id.split("/")[-1])
    if not model_alias:
        return gemini_error_response(404, f"Model models/{model_id} not found.")
    auth_key_prefix = _auth_key_prefix(account)

    if not batch:
        raw_content = body.get("content") if "content" in body else body.get("contents")
        contents = _coerce_embed_content(raw_content)
        if not contents:
            return gemini_error_response(400, "Field `content` is required.")
        prompt_text = api_manager._flatten_contents_text(contents)
        estimated_tokens = max(1, len(prompt_text) // 4)
        await _acquire_native_account_quota(account, estimated_tokens)

        gresult = await api_manager.call_embed(
            model_alias=model_alias,
            contents=contents,
            embed_config=_extract_embed_config(body) or None,
            account=account,
            estimated_tokens=estimated_tokens,
        )
        embeddings = gresult.get("embeddings") or []
        input_tokens = int(gresult.get("input_tokens") or estimated_tokens)
        actual_model_id = gresult.get("model_id") or gresult.get("model_alias") or model_alias
        await log_usage(
            actual_model_id,
            (gresult.get("api_key") or "")[-8:],
            input_tokens,
            0,
            auth_key_prefix,
            0,
            0,
        )
        values = [float(v) for v in (getattr(embeddings[0], "values", None) or [])] if embeddings else []
        return JSONResponse(content={"embedding": {"values": values}})

    raw_requests = body.get("requests")
    if not isinstance(raw_requests, list) or not raw_requests:
        return gemini_error_response(400, "Field `requests` must be a non-empty list.")

    parsed_items = []
    total_chars = 0
    for idx, item in enumerate(raw_requests):
        if not isinstance(item, dict):
            return gemini_error_response(400, f"requests[{idx}] must be an object.")
        c = _coerce_embed_content(item.get("content") if "content" in item else item.get("contents"))
        if not c:
            return gemini_error_response(400, f"requests[{idx}].content is required.")
        item_model = (item.get("model") or model_alias).split("/")[-1]
        item_alias = router.resolve_model_alias(item_model)
        if not item_alias:
            return gemini_error_response(404, f"Model models/{item_model} not found.")
        total_chars += len(api_manager._flatten_contents_text(c))
        parsed_items.append((item_alias, c, _extract_embed_config(item)))

    estimated_tokens = max(1, total_chars // 4)
    await _acquire_native_account_quota(account, estimated_tokens)

    uniform = all(
        ia == parsed_items[0][0] and ic == parsed_items[0][2]
        for ia, _, ic in parsed_items
    ) and all(len(c) == 1 for _, c, _ in parsed_items)

    out_embeddings = []
    total_input_tokens = 0
    last_model_id = model_alias
    last_key_suffix = ""

    if uniform:
        flat_contents = [c[0] for _, c, _ in parsed_items]
        gresult = await api_manager.call_embed(
            model_alias=parsed_items[0][0],
            contents=flat_contents,
            embed_config=parsed_items[0][2] or None,
            account=account,
            estimated_tokens=estimated_tokens,
        )
        for emb in gresult.get("embeddings") or []:
            out_embeddings.append({"values": [float(v) for v in (getattr(emb, "values", None) or [])]})
        total_input_tokens = int(gresult.get("input_tokens") or estimated_tokens)
        last_model_id = gresult.get("model_id") or gresult.get("model_alias") or model_alias
        last_key_suffix = (gresult.get("api_key") or "")[-8:]
    else:
        for item_alias, item_contents, item_cfg in parsed_items:
            item_est = max(1, len(api_manager._flatten_contents_text(item_contents)) // 4)
            gresult = await api_manager.call_embed(
                model_alias=item_alias,
                contents=item_contents,
                embed_config=item_cfg or None,
                account=account,
                estimated_tokens=item_est,
            )
            embs = gresult.get("embeddings") or []
            values = [float(v) for v in (getattr(embs[0], "values", None) or [])] if embs else []
            out_embeddings.append({"values": values})
            total_input_tokens += int(gresult.get("input_tokens") or item_est)
            last_model_id = gresult.get("model_id") or gresult.get("model_alias") or item_alias
            last_key_suffix = (gresult.get("api_key") or "")[-8:]

    await log_usage(
        last_model_id,
        last_key_suffix,
        total_input_tokens,
        0,
        auth_key_prefix,
        0,
        0,
    )
    return JSONResponse(content={"embeddings": out_embeddings})


async def _handle_gemini_decisions(
    request: Request,
    authorization: str | None,
    x_api_key: str | None,
    x_goog_api_key: str | None,
    api_version: str = "v1beta",
):
    try:
        return await _handle_gemini_decisions_inner(
            request, authorization, x_api_key, x_goog_api_key, api_version=api_version
        )
    except HTTPException:
        raise
    except Exception as exc:
        status, message = _classify_native_failure(exc)
        logger_api.error("[GeminiDecisions] %s -> HTTP %s: %s", api_version, status, message, exc_info=True)
        return gemini_error_response(status, message)


async def _handle_gemini_decisions_inner(
    request: Request,
    authorization: str | None,
    x_api_key: str | None,
    x_goog_api_key: str | None,
    api_version: str = "v1beta",
):
    auth_val = resolve_gemini_auth(request, authorization, x_api_key, x_goog_api_key)
    account = _check_auth(auth_val)
    try:
        body = await request.json()
    except Exception:
        return gemini_error_response(400, "Invalid JSON request body")
    if not isinstance(body, dict) or not body:
        return gemini_error_response(400, "Request body must be a non-empty JSON object")

    raw_model = str(body.get("model") or "gemini-decision-1").strip() or "gemini-decision-1"
    requested_model = raw_model.split("/")[-1]
    model_alias = router.resolve_model_alias(requested_model)
    if not model_alias:
        return gemini_error_response(404, f"Model models/{requested_model} not found.")
    estimated_tokens = max(1, len(json.dumps(body, ensure_ascii=False)) // 4)
    await _acquire_native_account_quota(account, estimated_tokens)

    gresult = await api_manager.call_decisions(
        model_alias=model_alias,
        payload=body,
        api_version=api_version,
        account=account,
        estimated_tokens=estimated_tokens,
    )

    input_tokens = int(gresult.get("input_tokens") or estimated_tokens)
    output_tokens = int(gresult.get("output_tokens") or 0)
    actual_model_id = gresult.get("model_id") or gresult.get("model_alias") or model_alias
    auth_key_prefix = _auth_key_prefix(account)

    await log_usage(
        actual_model_id,
        (gresult.get("api_key") or "")[-8:],
        input_tokens,
        output_tokens,
        auth_key_prefix,
        0,
        0,
    )
    return JSONResponse(content=gresult.get("response") or {})



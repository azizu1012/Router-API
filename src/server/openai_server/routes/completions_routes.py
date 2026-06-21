import uuid
import time
from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_api
from src.core.limits import account_limiter
from src.core.providers import _custom_endpoint_manager
from src.api.claude_proxy import claude_proxy
from src.core.usage_logger import log_usage

from src.server.openai_server.auth import _resolve_auth, _check_auth, _apply_account_limit, _auth_key_prefix, is_sub_agent_request, handle_sub_agent_error
from .app_init import app


def _extract_response_text(result: dict) -> str:
    """Extract text content from an OpenAI-format response dict."""
    choices = result.get("choices", [])
    if not choices:
        return ""
    msg = choices[0].get("message", {})
    return msg.get("content", "") or ""


def _extract_finish_reason(result: dict) -> str:
    choices = result.get("choices", [])
    if not choices:
        return "stop"
    return choices[0].get("finish_reason") or "stop"

@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
):
    auth = _resolve_auth(authorization, x_api_key)
    account = _check_auth(auth)
    body = await request.json()
    if not body.get("messages"):
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "`messages` is required", "type": "invalid_request_error"}},
        )

    try:
        await _apply_account_limit(account, body)
        stream = body.get("stream", False)
        from src.api.opencode_proxy import opencode_proxy
        if stream:
            return StreamingResponse(
                opencode_proxy.stream_chat_completion(body, account=account, is_opencode=False),
                media_type="text/event-stream",
            )
        result = await opencode_proxy.chat_completion(body, account=account, is_opencode=False)
        return result
    except Exception as e:
        logger_api.error("chat_completion failed: %s", e)
        if is_sub_agent_request(body, is_opencode=False):
            logger_api.info("Intercepted sub-agent chat_completion error: %s, returning simulated response", e)
            return handle_sub_agent_error(body, e, format_type="openai")

        msg = str(e)
        if msg.startswith("bad_request"):
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "Tool schema error: required field references undefined property", "type": "invalid_request_error"}},
            )
        if msg.startswith("quota_exhausted") or "rate_limited" in msg.lower():
            return JSONResponse(
                status_code=429,
                content={"error": {"message": "Rate limited, please retry later", "type": "rate_limit_error"}},
            )
        if "no_available_key" in msg:
            return JSONResponse(
                status_code=503,
                content={"error": {"message": "All keys are temporarily frozen, retry later", "type": "overloaded_error"}},
            )
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "Service temporarily unavailable", "type": "api_error"}},
        )


@app.post("/v1/completions")
async def completions(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
):
    auth = _resolve_auth(authorization, x_api_key)
    account = _check_auth(auth)
    body = await request.json()
    prompt = body.get("prompt", "")
    if isinstance(prompt, list):
        prompt = "\n".join(str(item) for item in prompt)
    chat_body = {
        "model": body.get("model"),
        "messages": [{"role": "user", "content": str(prompt)}],
        "temperature": body.get("temperature", 0.7),
        "top_p": body.get("top_p", 0.95),
        "max_tokens": body.get("max_tokens"),
        "stream": body.get("stream", False),
    }
    try:
        await _apply_account_limit(account, chat_body)
        from src.api.opencode_proxy import opencode_proxy
        if chat_body.get("stream"):
            return StreamingResponse(
                opencode_proxy.stream_chat_completion(chat_body, account=account, is_opencode=False),
                media_type="text/event-stream",
            )
        result = await opencode_proxy.chat_completion(chat_body, account=account, is_opencode=False)
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "no_available_key" in msg:
            return JSONResponse(status_code=503, content={"error": {"message": "All keys are temporarily frozen, retry later", "type": "overloaded_error"}})
        return JSONResponse(status_code=503, content={"error": {"message": "Service temporarily unavailable", "type": "api_error"}})

    auth_key_prefix = _auth_key_prefix(account)
    from src.logical_HQ_translator import _get_simulated_cache_usage
    usage = result.get("usage", {}) or {}
    input_tokens = usage.get("prompt_tokens", 0) or 0
    output_tokens = usage.get("completion_tokens", 0) or 0
    cache_usage = _get_simulated_cache_usage(chat_body or {}, input_tokens)
    cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
    cr = cache_usage.get("cache_read_input_tokens", 0) or 0
    await log_usage(
        result.get("model") or body.get("model", "unknown"),
        auth_key_prefix,
        input_tokens,
        output_tokens,
        auth_key_prefix,
        cc,
        cr,
    )

    now = int(time.time())
    return {
        "id": f"cmpl-{uuid.uuid4().hex}",
        "object": "text_completion",
        "created": now,
        "model": result.get("model") or body.get("model", ""),
        "choices": [{"text": _extract_response_text(result), "index": 0, "logprobs": None, "finish_reason": _extract_finish_reason(result)}],
        "usage": usage,
    }


@app.post("/v1/responses")
async def responses(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
):
    auth = _resolve_auth(authorization, x_api_key)
    account = _check_auth(auth)
    body = await request.json()
    input_value = body.get("input", "")
    if isinstance(input_value, list):
        messages = []
        for item in input_value:
            if isinstance(item, dict) and item.get("role"):
                messages.append({"role": item.get("role"), "content": item.get("content", "")})
            else:
                messages.append({"role": "user", "content": str(item)})
    else:
        messages = [{"role": "user", "content": str(input_value)}]

    chat_body = {
        "model": body.get("model"),
        "messages": messages,
        "temperature": body.get("temperature", 0.7),
        "top_p": body.get("top_p", 0.95),
        "max_tokens": body.get("max_output_tokens") or body.get("max_tokens"),
    }
    try:
        await _apply_account_limit(account, chat_body)
        from src.api.opencode_proxy import opencode_proxy
        result = await opencode_proxy.chat_completion(chat_body, account=account, is_opencode=False)
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "no_available_key" in msg:
            return JSONResponse(status_code=503, content={"error": {"message": "All keys are temporarily frozen, retry later", "type": "overloaded_error"}})
        return JSONResponse(status_code=503, content={"error": {"message": "Service temporarily unavailable", "type": "api_error"}})

    auth_key_prefix = _auth_key_prefix(account)
    from src.logical_HQ_translator import _get_simulated_cache_usage
    usage = result.get("usage", {}) or {}
    input_tokens = usage.get("prompt_tokens", 0) or 0
    output_tokens = usage.get("completion_tokens", 0) or 0
    cache_usage = _get_simulated_cache_usage(chat_body or {}, input_tokens)
    cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
    cr = cache_usage.get("cache_read_input_tokens", 0) or 0
    await log_usage(
        result.get("model") or body.get("model", "unknown"),
        auth_key_prefix,
        input_tokens,
        output_tokens,
        auth_key_prefix,
        cc,
        cr,
    )

    text = _extract_response_text(result)
    rid = f"resp_{uuid.uuid4().hex}"
    return {
        "id": rid,
        "object": "response",
        "created_at": int(time.time()),
        "model": result.get("model") or body.get("model", ""),
        "status": "completed",
        "output_text": text,
        "output": [{"id": f"msg_{uuid.uuid4().hex}", "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}],
        "usage": usage,
    }


@app.post("/v1/messages")
@app.post("/messages")
async def anthropic_messages(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
):
    auth = _resolve_auth(authorization, x_api_key)
    account = _check_auth(auth)
    body = await request.json()
    if not body.get("messages"):
        return JSONResponse(
            status_code=400,
            content={"type": "error", "error": {"type": "invalid_request_error", "message": "`messages` is required"}},
        )

    # Inject instruction to completely ban image reading in Claude Code proxy prompt
    image_ban_instruction = (
        "\n[IMPORTANT: Image analysis, multimodal features, and image input are completely disabled in this environment. "
        "Do not request, read, or analyze images. If the user asks you to look at an image, explain that image processing "
        "is disabled in the proxy to conserve tokens.]"
    )
    system_val = body.get("system", "")
    if isinstance(system_val, str):
        if system_val.strip():
            body["system"] = system_val + "\n" + image_ban_instruction
        else:
            body["system"] = image_ban_instruction
    elif isinstance(system_val, list):
        body["system"].append({"type": "text", "text": image_ban_instruction})

    # Forbid image content blocks in Claude proxy messages to prevent heavy token consumption
    for msg in body.get("messages", []):
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "type": "error",
                            "error": {
                                "type": "invalid_request_error",
                                "message": "Image inputs are disabled in Claude Proxy to conserve tokens."
                            }
                        }
                    )

    if not body.get("max_tokens"):
        body["max_tokens"] = config.MAX_OUTPUT_TOKENS

    akp = _auth_key_prefix(account)

    try:
        chat_like = {
            "model": body.get("model", ""),
            "messages": [
                {"role": msg.get("role", "user"), "content": msg.get("content", "")}
                for msg in body.get("messages", []) if isinstance(msg, dict)
            ],
            "max_tokens": body.get("max_tokens"),
            "system": body.get("system", ""),
        }
        await _apply_account_limit(account, chat_like)

        # Estimate input tokens to generate simulated rate-limit headers
        messages_val = body.get("messages", [])
        system_val = body.get("system", "")
        text_content = str(system_val) + str(messages_val)
        input_tokens_est = len(text_content) // 4
        from src.core.api_config import MODEL_CONTEXT_LENGTH
        limit_tokens = MODEL_CONTEXT_LENGTH
        remaining_tokens = max(1000, limit_tokens - input_tokens_est)
        utilization_val = min(0.99, round(input_tokens_est / limit_tokens, 4))
        
        response_headers = {
            "anthropic-version": "2023-06-01",
            "anthropic-ratelimit-requests-limit": "1000",
            "anthropic-ratelimit-requests-remaining": "999",
            "anthropic-ratelimit-tokens-limit": str(limit_tokens),
            "anthropic-ratelimit-tokens-remaining": str(remaining_tokens),
            "anthropic-ratelimit-unified-5h-utilization": f"{utilization_val:.4f}",
            "anthropic-ratelimit-unified-7d-utilization": f"{utilization_val:.4f}",
            "anthropic-ratelimit-unified-status": "allowed",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }

        stream = body.get("stream", False)
        if stream:
            return StreamingResponse(
                claude_proxy.stream_message(body, akp, account=account),
                media_type="text/event-stream",
                headers=response_headers,
            )
        else:
            result = await claude_proxy.create_message(body, akp, account=account)
            return JSONResponse(content=result, headers=response_headers)
    except HTTPException as e:
        if is_sub_agent_request(body, is_opencode=False):
            logger_api.info("Intercepted sub-agent anthropic_messages HTTPException: %s, returning simulated response", e)
            return handle_sub_agent_error(body, e, format_type="anthropic")
        raise
    except Exception as e:
        logger_api.error("anthropic_messages unexpected error: %s", e, exc_info=True)
        if is_sub_agent_request(body, is_opencode=False):
            logger_api.info("Intercepted sub-agent anthropic_messages error: %s, returning simulated response", e)
            return handle_sub_agent_error(body, e, format_type="anthropic")
        
        msg = str(e)
        if "quota_exhausted" in msg or "rate_limit" in msg or "rate_limited" in msg.lower():
            return JSONResponse(
                status_code=429,
                headers=response_headers,
                content={"type": "error", "error": {"type": "rate_limit_error", "message": "Rate limited, please retry later."}},
            )
        if "no_available_key" in msg or "frozen" in msg.lower() or "exhausted" in msg.lower():
            return JSONResponse(
                status_code=503,
                headers=response_headers,
                content={"type": "error", "error": {"type": "overloaded_error", "message": "All keys are temporarily frozen or exhausted, retry later."}},
            )
        return JSONResponse(
            status_code=503,
            headers=response_headers,
            content={"type": "error", "error": {"type": "api_error", "message": "Service temporarily unavailable"}},
        )



@app.post("/api/ping-model")
async def ping_model(request: Request):
    from src.core.providers.gemini_facade import acompletion

    body = await request.json()
    model = body.get("model", "")
    if not model:
        return JSONResponse(status_code=400, content={"ok": False, "error": "model required"})

    eps = _custom_endpoint_manager.list_endpoints()
    target = None
    for ep in eps:
        if model in ep.get("models", []):
            target = ep
            break

    if not target:
        return JSONResponse(status_code=404, content={"ok": False, "error": f"Model '{model}' not found in any endpoint"})

    try:
        resp = await acompletion(
            model=model,
            messages=[{"role": "user", "content": "OK"}],
            api_key=target["auth_key"],
            api_base=target["base_url"],
            max_tokens=5,
            temperature=0,
            stream=False,
            request_timeout=15,
        )
        text = resp.choices[0].message.content if resp.choices else ""
        usage = getattr(resp, "usage", None)
        return {
            "ok": True,
            "model": model,
            "response": (text or "").strip(),
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
        }
    except Exception as e:
        return JSONResponse(status_code=503, content={"ok": False, "error": str(e)[:500]})


@app.post("/v1/messages/count_tokens")
@app.post("/messages/count_tokens")
async def anthropic_count_tokens(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
):
    auth = _resolve_auth(authorization, x_api_key)
    _check_auth(auth)
    body = await request.json()
    messages = [
        {"role": msg.get("role", "user"), "content": msg.get("content", "")}
        for msg in body.get("messages", []) if isinstance(msg, dict)
    ]
    max_tokens = int(body.get("max_tokens") or 0)
    return {"input_tokens": account_limiter.estimate_messages_tokens(messages, max_tokens)}

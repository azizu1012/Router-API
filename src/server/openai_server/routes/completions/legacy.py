"""Legacy OpenAI text-completions route."""

import time
import uuid

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.core.config_n_logg.logger import logger_api
from src.core.observability.usage_logger import log_usage
from src.server.openai_server.auth import (
    _resolve_auth, _check_auth, _apply_account_limit, _auth_key_prefix,
)
from src.server.openai_server.routes.responses.format import (
    _error_type, _extract_response_text, _extract_finish_reason,
)
from ..app_init import app
@app.post("/v1/completions")
async def completions(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
):
    account = getattr(request.state, "auth_account", None)
    if account is None:
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
        "stream": body.get("stream", False),
    }
    # Only forward max_tokens when the caller actually sent one. Writing a None
    # into the body looked like a value to everything downstream.
    if body.get("max_tokens"):
        chat_body["max_tokens"] = body["max_tokens"]
    try:
        requested_model = chat_body.get("model", "")
        from src.core.config.api_config import AVAILABLE_MODELS, MODEL_CONTEXT_LENGTH
        from src.core.router import router
        model_alias = router.resolve_model_alias(requested_model)
        model_cfg = AVAILABLE_MODELS.get(model_alias)
        limit_tokens = model_cfg.get("context_length", MODEL_CONTEXT_LENGTH) if model_cfg else MODEL_CONTEXT_LENGTH

        # Estimate input tokens
        input_tokens_est = len(str(prompt)) // 4

        # Active Reject: check if estimated input tokens exceed model context length limit
        if input_tokens_est > limit_tokens:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": f"Your request's input tokens ({input_tokens_est}) exceeds the model's configured context length limit of {limit_tokens}.",
                        "type": "invalid_request_error",
                    }
                }
            )

        await _apply_account_limit(account, chat_body)
        
        remaining_tokens = max(1000, limit_tokens - input_tokens_est)
        response_headers = {
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-limit-tokens": str(limit_tokens),
            "x-ratelimit-remaining-requests": "999",
            "x-ratelimit-remaining-tokens": str(remaining_tokens),
        }

        from src.api.opencode_proxy import opencode_proxy
        if chat_body.get("stream"):
            return StreamingResponse(
                opencode_proxy.stream_chat_completion(chat_body, account=account, is_opencode=False),
                media_type="text/event-stream",
                headers=response_headers,
            )
        result = await opencode_proxy.chat_completion(chat_body, account=account, is_opencode=False)
    except HTTPException as e:
        detail = e.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=e.status_code, content=detail)
        return JSONResponse(
            status_code=e.status_code,
            content={"error": {"message": str(detail), "type": _error_type(e.status_code)}},
        )
    except Exception as e:
        # Log before mapping to a status. The client only ever sees a generic
        # 503, so without this the cause exists nowhere: the response says
        # "unavailable" and no log line says why. /v1/chat/completions logs its
        # own failures; these two routes did not.
        logger_api.error("[Upstream] request failed: %s", e, exc_info=True)
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
    return JSONResponse(
        content={
            "id": f"cmpl-{uuid.uuid4().hex}",
            "object": "text_completion",
            "created": now,
            "model": result.get("model") or body.get("model", ""),
            "choices": [{"text": _extract_response_text(result), "index": 0, "logprobs": None, "finish_reason": _extract_finish_reason(result)}],
            "usage": usage,
        },
        headers=response_headers,
    )




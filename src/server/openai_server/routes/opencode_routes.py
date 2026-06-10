from fastapi import Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.core.config_n_logg.logger import logger_api
from src.api.opencode_proxy import opencode_proxy

from src.server.openai_server.auth import _resolve_auth, _check_auth, _apply_account_limit
from src.core.limits.account_limiter import get_effective_limits_by_pool
from src.core.router import router
from src.core.config_n_logg import config
from .app_init import app


@app.post("/opencode/v1/chat/completions")
async def opencode_chat_completions(
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
        # Resolve model alias early so sub-agent override is applied for limits and headers
        model_alias = await opencode_proxy._resolve_alias(body, account=account, is_opencode=True)
        body["model"] = model_alias

        await _apply_account_limit(account, body, is_opencode=True)

        # Dynamic rate limit headers based on account and pool configuration
        pool_type = "lite" if (model_alias and ("lite" in model_alias.lower() or "flash-lite" in model_alias.lower())) else "flash"
        eff_rpm, eff_tpm, eff_rpd = await get_effective_limits_by_pool(account, pool_type)
        
        messages_val = body.get("messages", [])
        text_content = str(messages_val)
        input_tokens_est = len(text_content) // 4
        
        limit_tokens = eff_tpm if eff_tpm > 0 else 250000
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
        }

        is_stream = body.get("stream", False)
        if is_stream:
            return StreamingResponse(
                opencode_proxy.stream_chat_completion(body, account=account, is_opencode=True),
                media_type="text/event-stream",
                headers=response_headers,
            )
        else:
            resp_dict = await opencode_proxy.chat_completion(body, account=account, is_opencode=True)
            return JSONResponse(
                content=resp_dict,
                headers=response_headers,
            )

    except Exception as e:
        logger_api.error("opencode_chat_completions failed: %s", e)
        msg = str(e)
        if "quota_exhausted" in msg.lower() or "rate_limited" in msg.lower():
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

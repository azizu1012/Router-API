"""Anthropic Messages and token-counting routes."""

import json
import uuid

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_api
from src.api.claude_proxy import claude_proxy
from src.api.claude_proxy.handler.anthropic_spec import estimate_input_tokens
from src.server.openai_server.auth import (
    _resolve_auth, _check_auth, _apply_account_limit, _auth_key_prefix,
    is_sub_agent_request,
    handle_sub_agent_error,
)
from ..app_init import app
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

    # Resolve model alias if exists
    requested_model = body.get("model", "")
    from src.logical_HQ_translator.model.alias import resolve_model_alias
    account_id = str(account.get("account_id") or "")
    account_key_id = account.get("token_key_id") or account.get("account_key_id")

    resolved_model, alias_info = resolve_model_alias(
        requested_model,
        account_id,
        account_key_id,
    )
    if alias_info:
        body["model"] = resolved_model
        body["_original_model_name"] = requested_model
        body["_alias_info"] = alias_info

    if not body.get("max_tokens"):
        body["max_tokens"] = config.MAX_OUTPUT_TOKENS

    akp = _auth_key_prefix(account)
    response_headers = {
        "anthropic-version": "2023-06-01",
        "request-id": "req_" + uuid.uuid4().hex[:24],
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }

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
        from src.core.config.api_config import MODEL_CONTEXT_LENGTH
        limit_tokens = MODEL_CONTEXT_LENGTH
        remaining_tokens = max(1000, limit_tokens - input_tokens_est)
        utilization_val = min(0.99, round(input_tokens_est / limit_tokens, 4))
        
        response_headers.update({
            "anthropic-ratelimit-requests-limit": "1000",
            "anthropic-ratelimit-requests-remaining": "999",
            "anthropic-ratelimit-tokens-limit": str(limit_tokens),
            "anthropic-ratelimit-tokens-remaining": str(remaining_tokens),
            "anthropic-ratelimit-unified-5h-utilization": f"{utilization_val:.4f}",
            "anthropic-ratelimit-unified-7d-utilization": f"{utilization_val:.4f}",
            "anthropic-ratelimit-unified-status": "allowed",
        })

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
        detail = e.detail
        err = detail.get("error", {}) if isinstance(detail, dict) else {}
        return JSONResponse(
            status_code=e.status_code,
            content={"type": "error", "error": {"type": err.get("type", "api_error"), "message": err.get("message", str(detail))}},
        )
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
        if "custom endpoint" in msg.lower():
            import re
            m = re.search(r"HTTP (\d{3}):\s*(.*)", msg, re.DOTALL)
            status_code = int(m.group(1)) if m else 502
            err_text = m.group(2).strip() if m else msg
            err_msg = err_text
            try:
                err_json = json.loads(err_text)
                if isinstance(err_json, dict) and "error" in err_json:
                    err_msg = err_json["error"].get("message") or str(err_json["error"])
            except Exception:
                pass
            return JSONResponse(
                status_code=status_code,
                headers=response_headers,
                content={
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error" if status_code in (400, 404) else "api_error",
                        "message": f"Custom endpoint error ({status_code}): {err_msg}",
                    },
                },
            )
        return JSONResponse(
            status_code=503,
            headers=response_headers,
            content={"type": "error", "error": {"type": "api_error", "message": "Service temporarily unavailable"}},
        )

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
    # Anthropic count_tokens reports INPUT tokens only — max_tokens (the output
    # budget) must not be folded in. Claude Code renders this as the context bar,
    # so including it inflated the reading by thousands of tokens.
    return {"input_tokens": estimate_input_tokens(body)}






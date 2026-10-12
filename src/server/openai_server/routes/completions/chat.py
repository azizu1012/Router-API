"""OpenAI chat-completions route."""

import json
from typing import Any, Dict

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.core.config_n_logg.logger import logger_api
from src.core.providers import _custom_endpoint_manager
from src.server.openai_server.auth import (
    _resolve_auth, _check_auth, _apply_account_limit, is_sub_agent_request,
    handle_sub_agent_error, _sub_agent_stream_error,
)
from src.server.openai_server.routes.responses.format import (
    _error_type, _spoof_model_name_in_response,
)
from ..app_init import app
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
        requested_model = body.get("model", "")
        original_model_name = requested_model  # Save for response spoofing

        # Resolve model alias if exists
        from src.logical_HQ_translator.model.alias import resolve_model_alias
        account_id = str(account.get("account_id") or "")
        account_key_id = account.get("token_key_id") or account.get("account_key_id")

        resolved_model, alias_info = resolve_model_alias(
            requested_model,
            account_id,
            account_key_id
        )

        # If alias found, update body with resolved model
        if alias_info:
            body["model"] = resolved_model
            # Store original name for response spoofing
            body["_original_model_name"] = original_model_name
            body["_alias_info"] = alias_info

        # Route directly to custom endpoint if specified in alias
        target_ep_name = alias_info.get("target_endpoint") if alias_info else None
        if target_ep_name:
            from src.backend.endpoints.manager import get_endpoint_db
            custom_ep = get_endpoint_db(target_ep_name)
            if custom_ep and custom_ep.get("enabled", True):
                from src.core.providers.custom_endpoint.passthrough import call_custom_endpoint_passthrough
                stream = body.get("stream", False)
                if stream:
                    _, stream_gen = await call_custom_endpoint_passthrough(
                        endpoint=custom_ep,
                        model=resolved_model,
                        messages=body.get("messages", []),
                        stream=True,
                        temperature=body.get("temperature"),
                        max_tokens=body.get("max_tokens"),
                        tools=body.get("tools"),
                        extra_body=body.get("extra_body"),
                        tool_choice=body.get("tool_choice"),
                        response_format=body.get("response_format"),
                    )
                    async def _custom_safe_stream():
                        if stream_gen is not None:
                            async for chunk in stream_gen:
                                c_dict = getattr(chunk, "raw", chunk if isinstance(chunk, dict) else {})
                                if original_model_name and isinstance(c_dict, dict):
                                    c_dict["model"] = original_model_name
                                yield f"data: {json.dumps(c_dict, ensure_ascii=False)}\n\n".encode("utf-8")
                        yield b"data: [DONE]\n\n"
                    return StreamingResponse(_custom_safe_stream(), media_type="text/event-stream")
                else:
                    resp, _ = await call_custom_endpoint_passthrough(
                        endpoint=custom_ep,
                        model=resolved_model,
                        messages=body.get("messages", []),
                        stream=False,
                        temperature=body.get("temperature"),
                        max_tokens=body.get("max_tokens"),
                        tools=body.get("tools"),
                        extra_body=body.get("extra_body"),
                        tool_choice=body.get("tool_choice"),
                        response_format=body.get("response_format"),
                    )
                    def _ns_to_dict(obj: Any) -> Any:
                        if isinstance(obj, dict):
                            return {k: _ns_to_dict(v) for k, v in obj.items()}
                        elif isinstance(obj, list):
                            return [_ns_to_dict(v) for v in obj]
                        elif hasattr(obj, "__dict__"):
                            return {k: _ns_to_dict(v) for k, v in vars(obj).items() if not k.startswith("_")}
                        return obj
                    resp_converted = _ns_to_dict(resp)
                    resp_dict: Dict[str, Any] = resp_converted if isinstance(resp_converted, dict) else {"choices": []}
                    if original_model_name:
                        resp_dict = _spoof_model_name_in_response(resp_dict, original_model_name)
                    return JSONResponse(content=resp_dict)

        from src.core.config.api_config import AVAILABLE_MODELS, MODEL_CONTEXT_LENGTH
        from src.core.router import router
        model_alias = router.resolve_model_alias(body.get("model", ""))
        model_cfg = AVAILABLE_MODELS.get(model_alias)
        limit_tokens = model_cfg.get("context_length", MODEL_CONTEXT_LENGTH) if model_cfg else MODEL_CONTEXT_LENGTH

        # Estimate input tokens
        messages_val = body.get("messages", [])
        system_val = body.get("system", "")
        for msg in messages_val:
            if isinstance(msg, dict) and msg.get("role") in ("system", "developer"):
                system_val = str(system_val) + "\n" + str(msg.get("content", ""))
        text_content = str(system_val) + str(messages_val)
        input_tokens_est = len(text_content) // 4

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

        await _apply_account_limit(account, body)
        stream = body.get("stream", False)
        
        remaining_tokens = max(1000, limit_tokens - input_tokens_est)
        response_headers = {
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-limit-tokens": str(limit_tokens),
            "x-ratelimit-remaining-requests": "999",
            "x-ratelimit-remaining-tokens": str(remaining_tokens),
        }

        from src.api.opencode_proxy import opencode_proxy
        if stream:
            async def _safe_stream():
                try:
                    async for chunk in opencode_proxy.stream_chat_completion(body, account=account, is_opencode=False):
                        # Spoof model name in streaming chunks if needed
                        if body.get("_original_model_name"):
                            try:
                                if chunk.startswith(b"data: "):
                                    data_str = chunk.decode("utf-8").removeprefix("data: ").strip()
                                    if data_str and data_str != "[DONE]":
                                        chunk_data = json.loads(data_str)
                                        if "model" in chunk_data:
                                            chunk_data["model"] = body["_original_model_name"]
                                        chunk = f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n".encode("utf-8")
                            except Exception:
                                pass  # If parsing fails, forward original chunk
                        yield chunk
                except Exception as e:
                    logger_api.warning("[Completion Route] Stream error caught: %s", e)
                    if is_sub_agent_request(body, is_opencode=False):
                        async for chunk in _sub_agent_stream_error(body, model_alias, e):
                            yield chunk
                    else:
                        yield "data: [DONE]\n\n".encode("utf-8")
            return StreamingResponse(
                _safe_stream(),
                media_type="text/event-stream",
                headers=response_headers,
            )
        result = await opencode_proxy.chat_completion(body, account=account, is_opencode=False)

        # Spoof model name in non-streaming response if needed
        if body.get("_original_model_name"):
            result = _spoof_model_name_in_response(result, body["_original_model_name"])

        return JSONResponse(content=result, headers=response_headers)
    except Exception as e:
        logger_api.error("chat_completion failed: %s", e)
        if is_sub_agent_request(body, is_opencode=False):
            logger_api.info("Intercepted sub-agent chat_completion error: %s, returning simulated response", e)
            return handle_sub_agent_error(body, e, format_type="openai")

        # An HTTPException already carries the right status and body. Re-deriving
        # one from str(e) threw the reason away: _apply_account_limit raises 429
        # with "Account rate limit exceeded: tokens per minute limit exceeded",
        # which matches none of the branches below, so a rate-limited client was
        # told "Service temporarily unavailable". 503 tells a client to retry
        # immediately, so that turned one rate limit into a retry storm.
        if isinstance(e, HTTPException):
            detail = e.detail
            if isinstance(detail, dict) and "error" in detail:
                return JSONResponse(status_code=e.status_code, content=detail)
            return JSONResponse(
                status_code=e.status_code,
                content={"error": {"message": str(detail), "type": _error_type(e.status_code)}},
            )

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
                content={
                    "error": {
                        "message": f"Custom endpoint error ({status_code}): {err_msg}",
                        "type": _error_type(status_code),
                    }
                },
            )
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "Service temporarily unavailable", "type": "api_error"}},
        )




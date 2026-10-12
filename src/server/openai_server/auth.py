from typing import Any, AsyncIterator, Dict, Optional
from fastapi import HTTPException

from src.core.limits import account_limiter, token_limiter
from src.core.auth import (
    AuthenticationFailure,
    authenticate,
    auth_key_prefix,
    bearer_token,
    count_openai_images,
    resolve_auth_header,
    enforce_account_limit,
    enforce_token_limits,
    token_limit_message,
    AccountLimitFailure,
    TokenLimitFailure,
)

# Sub-agent detection and SSE framing come from src/core/ on purpose. Both used to
# be imported from src.logical_HQ_translator.streaming.cache_agent (and the opencode
# proxy) inside function bodies, which read like cycle-breaking but were not one:
# no proxy ever imports auth. That made auth depend upward on src/api/ with
# nothing to show for it. Keep these at module level. See docs/bug-logs.md Bug #6.
from src.core.runtime.sse_format import sse_event as _sse
from src.core.runtime.sub_agent_detect import is_sub_agent_body, is_sub_agent_request_openai

from src.backend.keys.accounts import get_key_db


_bearer_token = bearer_token


_resolve_auth = resolve_auth_header


def _check_auth(authorization: str | None) -> Dict[str, Any]:
    try:
        return authenticate(authorization)
    except AuthenticationFailure as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": exc.message, "type": "authentication_error"}},
        ) from exc


_token_limit_message = token_limit_message


async def _enforce_token_limits(account: Dict[str, Any], estimated_tokens: int = 0) -> Any:
    try:
        return await enforce_token_limits(account, estimated_tokens)
    except TokenLimitFailure as exc:
        raise HTTPException(
            status_code=429,
            detail={"error": {"message": exc.message, "type": "rate_limit_error"}},
        ) from exc


_count_openai_images = count_openai_images


async def _apply_account_limit(account: Dict[str, Any], body: Dict[str, Any], is_opencode: bool = False) -> None:
    try:
        await enforce_account_limit(account, body, is_opencode=is_opencode)
    except AccountLimitFailure as exc:
        raise HTTPException(
            status_code=429,
            detail={"error": {"message": exc.message, "type": "rate_limit_error"}},
        ) from exc


_auth_key_prefix = auth_key_prefix


def is_sub_agent_request(body: Dict[str, Any], is_opencode: bool = False) -> bool:
    """Helper to detect sub-agent request using both prompt and structure indicators."""
    if is_opencode:
        return is_sub_agent_request_openai(body)
    else:
        return is_sub_agent_body(body)


def handle_sub_agent_error(body: Dict[str, Any], exc: Exception, format_type: str = "openai") -> Any:
    """Intercept sub-agent error and return a simulated successful HTTP 200 response.

    This prevents the client (Claude Code / OpenCode CLI) from crashing when sub-agents encounter errors.
    """
    from fastapi.responses import JSONResponse, StreamingResponse
    import uuid
    import time
    import json

    error_msg = str(exc)
    # Determine reason classification
    reason = "pool_exhausted"
    if "rate" in error_msg.lower() or "limit" in error_msg.lower() or "quota" in error_msg.lower() or "429" in error_msg:
        reason = "rate_limit"
    elif "503" in error_msg or "unavailable" in error_msg or "overloaded" in error_msg or "frozen" in error_msg:
        reason = "unavailable"
    elif "401" in error_msg or "unauthorized" in error_msg or "api key" in error_msg.lower():
        reason = "invalid_key"

    # Let's generate a friendly simulated error message
    from src.api.claude_proxy.handler.helpers import get_system_status_summary
    model_name = body.get("model") or "gemini-flash-lite"
    warning_text = get_system_status_summary(model_name, reason)

    # ponytail: inform main agent to proceed directly rather than triggering an infinite retry crash loop
    warning_text = (
        f"[Notice: Sub-agent could not complete background query ({reason})]. "
        "Proceed with the current session context directly."
    )

    is_stream = body.get("stream", False)

    if format_type == "openai":
        # OpenAI/OpenCode chat.completion format
        mapped_model = model_name
        if is_stream:
            async def stream_generator():
                cid = f"chatcmpl-{uuid.uuid4().hex}"
                created = int(time.time())

                # Yield role delta
                role_chunk = {
                    "id": cid, "object": "chat.completion.chunk", "created": created, "model": mapped_model,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(role_chunk, ensure_ascii=False)}\n\n".encode("utf-8")

                # Yield text delta
                text_chunk = {
                    "id": cid, "object": "chat.completion.chunk", "created": created, "model": mapped_model,
                    "choices": [{"index": 0, "delta": {"content": warning_text}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(text_chunk, ensure_ascii=False)}\n\n".encode("utf-8")

                # Yield stop delta
                stop_chunk = {
                    "id": cid, "object": "chat.completion.chunk", "created": created, "model": mapped_model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(stop_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"

            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        else:
            return JSONResponse(
                status_code=200,
                content={
                    "id": f"chatcmpl-{uuid.uuid4().hex}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": mapped_model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": warning_text},
                            "finish_reason": "stop"
                        }
                    ],
                    "usage": {
                        "prompt_tokens": len(str(body.get("messages", []))) // 4,
                        "completion_tokens": len(warning_text) // 4,
                        "total_tokens": (len(str(body.get("messages", []))) // 4) + (len(warning_text) // 4)
                    }
                }
            )
    else:
        # Anthropic format
        if is_stream:
            async def stream_generator():
                msg_id = "msg_" + uuid.uuid4().hex

                # 1. message_start
                yield _sse("message_start", {
                    "type": "message_start",
                    "message": {
                        "id": msg_id, "type": "message", "role": "assistant", "model": model_name,
                        "content": [], "stop_reason": None, "stop_sequence": None,
                        "usage": {"input_tokens": len(str(body.get("messages", []))) // 4, "output_tokens": 0},
                    },
                })

                # 2. content_block_start
                yield _sse("content_block_start", {
                    "type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""}
                })

                # 3. content_block_delta
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": warning_text}
                })

                # 4. content_block_stop
                yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})

                # 5. message_delta
                yield _sse("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": len(warning_text) // 4},
                })

                # 6. message_stop
                yield _sse("message_stop", {"type": "message_stop"})

            response_headers = {
                "anthropic-version": "2023-06-01",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
            return StreamingResponse(stream_generator(), media_type="text/event-stream", headers=response_headers)
        else:
            response_headers = {
                "anthropic-version": "2023-06-01",
            }
            return JSONResponse(
                status_code=200,
                headers=response_headers,
                content={
                    "id": "msg_" + uuid.uuid4().hex,
                    "type": "message",
                    "role": "assistant",
                    "model": model_name,
                    "content": [{"type": "text", "text": warning_text}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": len(str(body.get("messages", []))) // 4,
                        "output_tokens": len(warning_text) // 4,
                    },
                }
            )


async def _sub_agent_stream_error(body: dict, model_alias: str, exc: Exception) -> AsyncIterator[bytes]:
    """Yield simulated SSE chunks for sub-agent stream error instead of empty response."""
    import uuid, time, json
    from src.api.opencode_proxy.handler.response import get_client_model_name
    cid = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    mapped_model = get_client_model_name(body.get("model") or model_alias or "gemini-flash-lite")
    error_msg = str(exc)
    reason = "pool_exhausted"
    if "rate" in error_msg.lower() or "limit" in error_msg.lower() or "quota" in error_msg.lower() or "429" in error_msg:
        reason = "rate_limit"
    elif "503" in error_msg or "unavailable" in error_msg or "overloaded" in error_msg or "frozen" in error_msg:
        reason = "unavailable"
    # ponytail: inform main agent to proceed directly rather than triggering an infinite retry crash loop
    warning_text = (
        f"[Notice: Sub-agent could not complete background query ({reason})]. "
        "Proceed with the current session context directly."
    )
    for data in [
        {"id": cid, "object": "chat.completion.chunk", "created": created, "model": mapped_model,
         "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
        {"id": cid, "object": "chat.completion.chunk", "created": created, "model": mapped_model,
         "choices": [{"index": 0, "delta": {"content": warning_text}, "finish_reason": None}]},
        {"id": cid, "object": "chat.completion.chunk", "created": created, "model": mapped_model,
         "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]:
        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"


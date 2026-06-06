from fastapi import Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.core.config_n_logg.logger import logger_api
from src.api.opencode_proxy import opencode_proxy

from src.server.openai_server.auth import _resolve_auth, _check_auth, _apply_account_limit
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
        await _apply_account_limit(account, body)

        if body.get("stream"):
            return StreamingResponse(
                opencode_proxy.stream_chat_completion(body, account=account),
                media_type="text/event-stream",
            )

        result = await opencode_proxy.chat_completion(body, account=account)
        return result

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

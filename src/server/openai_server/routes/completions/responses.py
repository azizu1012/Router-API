"""OpenAI Responses API route."""

import time
import uuid

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.claude_proxy.handler.anthropic_spec import estimate_input_tokens
from src.core.config_n_logg.logger import logger_api
from src.core.observability.usage_logger import log_usage
from src.server.openai_server.auth import (
    _resolve_auth, _check_auth, _apply_account_limit, _auth_key_prefix,
)
from src.server.openai_server.routes.responses.format import (
    _error_type,
    _extract_response_text,
    _extract_response_tool_calls,
    _extract_finish_reason,
    _responses_usage,
    _responses_envelope,
    _output_text_part,
    _output_message,
    _web_search_action,
    _web_search_call_item,
    _url_citations,
    _responses_event,
)
from src.server.openai_server.routes.responses.stream import _responses_sse_stream
from ..app_init import app

__all__ = [
    "responses",
    "_error_type",
    "_extract_response_text",
    "_extract_response_tool_calls",
    "_extract_finish_reason",
    "_responses_usage",
    "_responses_envelope",
    "_output_text_part",
    "_output_message",
    "_web_search_action",
    "_web_search_call_item",
    "_url_citations",
    "_responses_event",
    "_responses_sse_stream",
]
@app.post("/v1/responses")
async def responses(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
):
    auth = _resolve_auth(authorization, x_api_key)
    account = _check_auth(auth)
    body = await request.json()

    from src.api.opencode_proxy.handler.responses_mapping import (
        hosted_tool_names,
        responses_to_chat_body,
    )

    chat_body = responses_to_chat_body(body)
    hosted = hosted_tool_names(body)
    if hosted:
        logger_api.info(
            "[Responses Route] hosted tools=%s -> router-native, engine=%s",
            ",".join(hosted), chat_body.get("search_engine", "duckduckgo"),
        )

    is_stream = bool(chat_body.get("stream"))
    try:
        await _apply_account_limit(account, chat_body)
        from src.api.opencode_proxy import opencode_proxy
        search_trace: list = []
        if is_stream:
            # Searches are driven to completion first so the stream can open with
            # the search item already reported, the order OpenAI uses.
            _msgs, _tools, search_trace = await opencode_proxy.prime_search(
                chat_body, account=account)
            stream_body = dict(chat_body)
            stream_body["messages"] = _msgs
            if _tools:
                stream_body["tools"] = _tools
            # `response.completed` always carries usage in the Responses API,
            # while the chat layer only emits its trailing usage chunk when the
            # client asks. The client here speaks Responses and never sees a chat
            # chunk, so asking for it here changes nothing on the wire the client
            # sees — it only stops the completed event reporting `"usage": null`.
            stream_body["stream_options"] = {**(chat_body.get("stream_options") or {}),
                                            "include_usage": True}
            return StreamingResponse(
                _responses_sse_stream(
                    opencode_proxy.stream_chat_completion(
                        stream_body, account=account, is_opencode=False
                    ),
                    model=str(body.get("model", "")),
                    search_trace=search_trace,
                    input_tokens_hint=estimate_input_tokens(dict(stream_body)),
                ),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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

    text = _extract_response_text(result)
    rid = f"resp_{uuid.uuid4().hex}"
    item_id = f"msg_{uuid.uuid4().hex}"
    trace = result.get("_search_trace") or []
    result.pop("_search_trace", None)

    output = []
    # Hosted search items come before the message, matching OpenAI's order.
    for record in trace:
        call_id = record.get("id") or f"ws_{uuid.uuid4().hex}"
        output.append(_web_search_call_item(call_id, record))
    cites = [c for r in trace for c in (r.get("citations") or [])
             if isinstance(c, dict)]
    if text:
        output.append(_output_message(item_id, "completed", text, cites))
    # A tool turn carries no text, so building the envelope from the text alone
    # returned an empty output and the client was told it had been answered.
    for tc in _extract_response_tool_calls(result):
        if tc.get("name") in ("WebSearch", "web_search", "WebFetch",
                              "web_fetch"):
            continue
        output.append({
            "id": f"fc_{uuid.uuid4().hex}",
            "type": "function_call",
            "status": "completed",
            "call_id": tc.get("id") or f"call_{uuid.uuid4().hex}",
            "name": tc.get("name") or "",
            "arguments": tc.get("arguments") or "",
        })

    # `output_text` is deliberately absent: it is a convenience property the
    # OpenAI SDK computes from `output`, not a field the API sends. Shipping it
    # would be a field the official model rejects under extra="forbid".
    return _responses_envelope(
        rid, result.get("model") or body.get("model", ""), "completed",
        output, int(time.time()), _responses_usage(usage),
    )





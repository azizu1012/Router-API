import json
import uuid
import time
from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_api
from src.core.providers import _custom_endpoint_manager
from src.api.claude_proxy import claude_proxy
from src.api.claude_proxy.handler.anthropic_spec import estimate_input_tokens
from src.core.usage_logger import log_usage

from src.server.openai_server.auth import _resolve_auth, _check_auth, _apply_account_limit, _auth_key_prefix, is_sub_agent_request, handle_sub_agent_error, _sub_agent_stream_error
from .app_init import app


def _error_type(status_code: int) -> str:
    """The OpenAI error type that goes with an HTTP status."""
    return {400: "invalid_request_error",
            401: "authentication_error",
            403: "permission_error",
            404: "invalid_request_error",
            429: "rate_limit_error",
            }.get(status_code, "api_error")


def _extract_response_text(result: dict) -> str:
    """Extract text content from an OpenAI-format response dict."""
    choices = result.get("choices", [])
    if not choices:
        return ""
    msg = choices[0].get("message", {})
    return msg.get("content", "") or ""


def _extract_finish_reason(result: dict) -> str:
    """The upstream reason, mapped onto the OpenAI enum.

    Upstream does not speak OpenAI's vocabulary — Gemini reports things like
    `malformed_function_call` — and the field is a Literal in the SDK, so an
    unmapped value makes the response unparseable rather than merely odd.
    """
    from src.core.providers.finish_reason import normalize_finish_reason

    choices = result.get("choices", [])
    if not choices:
        return "stop"
    has_calls = any(
        isinstance(c, dict) and (c.get("message") or {}).get("tool_calls")
        for c in choices
    )
    return normalize_finish_reason(choices[0].get("finish_reason"),
                                   has_tool_calls=has_calls)


def _extract_response_tool_calls(result: dict) -> list[dict]:
    """The function calls a chat-format response asked for, flattened.

    Reads both nested shapes: `message.tool_calls[].function` is what the proxy
    emits, but a handler may hand back the flattened `function.name` /
    `function.arguments` form, and silently returning nothing for either is how
    a tool turn used to come back empty.
    """
    choices = result.get("choices") or []
    if not choices:
        return []
    msg = choices[0].get("message") or {}

    out: list[dict] = []
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name") or ""
        if not name:
            continue
        args = fn.get("arguments")
        if args is None:
            args = tc.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args) if args else ""
        out.append({"id": tc.get("id"), "name": name, "arguments": args})
    return out


def _responses_event(kind: str, seq: int, **fields) -> bytes:
    """One Responses SSE frame.

    Official frames carry an `event:` line naming the type *and* a `data:` line
    whose JSON repeats it. The Python SDK reads the type out of the JSON, but
    other clients dispatch on the event name, so both are emitted.
    """
    payload = {"type": kind, "sequence_number": seq, **fields}
    body = json.dumps(payload, ensure_ascii=False)
    return f"event: {kind}\ndata: {body}\n\n".encode("utf-8")


def _responses_usage(usage: dict | None) -> dict | None:
    """Chat usage -> Responses usage. The two dialects name the same numbers
    differently: prompt/completion_tokens vs input/output_tokens.

    Both `*_tokens_details` objects are required members of ResponseUsage, so
    they are always emitted; the numbers inside them default to 0.

    No usage at all yields None, not a row of zeros. `Response.usage` is
    optional, and reporting 0/0 for a request that really spent tokens is a
    number a client will believe and bill against; null says "unknown".
    """
    if not usage:
        return None
    inp = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    out = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    total = int(usage.get("total_tokens", 0) or 0) or (inp + out)
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
    reasoning = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0)
    return {
        "input_tokens": inp,
        "input_tokens_details": {"cached_tokens": cached},
        "output_tokens": out,
        "output_tokens_details": {"reasoning_tokens": reasoning},
        "total_tokens": total,
    }


def _responses_envelope(rid: str, model: str, status: str, output: list,
                        created_at: int, usage: dict | None = None) -> dict:
    """The `response` object embedded in created/in_progress/completed.

    `parallel_tool_calls`, `tool_choice` and `tools` are required members of the
    Response object; omitting them makes the official SDK reject the frame.
    """
    return {
        "id": rid,
        "object": "response",
        "created_at": created_at,
        "model": model,
        "status": status,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": usage,
    }


def _output_text_part(text: str, citations: list | None = None) -> dict:
    return {"type": "output_text", "text": text,
            "annotations": _url_citations(text, citations or []),
            "logprobs": []}


def _output_message(item_id: str, status: str, text: str,
                    citations: list | None = None) -> dict:
    return {"id": item_id, "type": "message", "status": status,
            "role": "assistant",
            "content": [_output_text_part(text, citations)]}


def _web_search_action(record: dict) -> dict:
    """The action object inside a `web_search_call` item.

    `ActionSearch` declares exactly `type`, `queries`, `query` and `sources`;
    `sources` entries are `{type: "url", url}`. Anything outside that set fails
    validation under strict=True, so the engine name the router used internally
    has no home here.
    """
    if record.get("type") == "open_page":
        return {"type": "open_page", "url": record.get("url", "")}
    action: dict = {"type": "search"}
    queries = record.get("queries")
    if isinstance(queries, list) and queries:
        action["queries"] = [str(q) for q in queries]
    if record.get("query"):
        action["query"] = record["query"]
    sources = [{"type": "url", "url": c["url"]}
               for c in (record.get("citations") or [])
               if isinstance(c, dict) and c.get("url")]
    if sources:
        action["sources"] = sources
    return action


def _web_search_call_item(call_id: str, record: dict) -> dict:
    """A `web_search_call` output item.

    `status` is `failed` when the search did not run, which is how a client
    tells an empty search apart from one that was attempted. A search that ran
    and matched nothing is `completed`, not `failed`.
    """
    failed = bool(record.get("error")) or not record.get("query")
    return {
        "id": call_id,
        "type": "web_search_call",
        "status": "failed" if failed else "completed",
        "action": _web_search_action(record),
    }


def _url_citations(text: str, citations: list) -> list:
    """Citation annotations for a message that used search results.

    OpenAI attaches `url_citation` annotations to the text part, each spanning a
    character range of it. Without them a client has no way to show where an
    answer came from, which is the whole point of the hosted tool.
    """
    if not text or not citations:
        return []
    out = []
    for c in citations:
        if not isinstance(c, dict) or not c.get("url"):
            continue
        out.append({
            "type": "url_citation",
            "url": c["url"],
            "title": c.get("title") or c["url"],
            # The whole message is attributed to every source: the router does
            # not know which span of the answer a given page supports.
            "start_index": 0,
            "end_index": len(text),
        })
    return out


async def _responses_sse_stream(chunks, model: str = "",
                                search_trace: list | None = None,
                                input_tokens_hint: int = 0):
    """Re-frame chat-completions SSE into Responses API events.

    The chat proxy speaks the chat dialect: `data: {"choices":[{"delta":...}]}`.
    A Responses client parses typed events — `response.created`,
    `response.output_text.delta`, `response.completed` — and ignores anything
    else, so forwarding chat chunks verbatim produced a stream that opened and
    closed with no text in it. The model never failed; the client just had no
    event to print.

    The closing frames used to carry empty text and empty arrays, which the
    official SDK accepts for `output_item.done` but which discard the answer:
    `response.completed.output` is what a Responses client reads as the result,
    so an empty array there means a successful call that returns nothing.

    Tool calls are the same failure one level down. The adapter only ever read
    `delta.content`, so a turn that called a function produced a stream with no
    text at all: `output_text.done` with `text: ""` and `response.completed`
    with `output: []`. Every function call the model made was dropped, and the
    client got a successful response describing nothing it had asked for.
    """
    rid = f"resp_{uuid.uuid4().hex}"
    msg_id = f"msg_{uuid.uuid4().hex}"
    searches = list(search_trace or [])
    created = int(time.time())
    seq = 0
    parts: list[str] = []
    usage = None
    msg_open = False
    msg_index = 0
    # Output indices are handed out as items open, not as they close, so this
    # cannot be derived from `len(output)` — that stays 0 until the end.
    next_index = 0
    tools: dict[int, dict] = {}
    output: list = []

    def envelope(status, u=None):
        return _responses_envelope(rid, model, status, output, created, u)

    yield _responses_event("response.created", seq,
                           response=envelope("in_progress"))
    seq += 1
    yield _responses_event("response.in_progress", seq,
                           response=envelope("in_progress"))

    # A search the router ran for the model is a hosted tool call, not a
    # function call: OpenAI reports these as `web_search_call` items that come
    # before the message, with no client-side tool loop. Emitting a
    # `function_call` here would ask the client to run a tool it does not have.
    for record in searches:
        idx = next_index
        next_index += 1
        call_id = record.get("id") or f"ws_{uuid.uuid4().hex}"
        item = _web_search_call_item(call_id, record)
        yield _responses_event("response.output_item.added", seq,
                              output_index=idx, item=item)
        seq += 1
        yield _responses_event("response.web_search_call.in_progress", seq,
                              item_id=call_id, output_index=idx)
        seq += 1
        yield _responses_event("response.web_search_call.searching", seq,
                              item_id=call_id, output_index=idx)
        seq += 1
        yield _responses_event("response.web_search_call.completed", seq,
                              item_id=call_id, output_index=idx)
        seq += 1
        yield _responses_event("response.output_item.done", seq,
                              output_index=idx, item=item)
        seq += 1
        output.append(item)
    seq += 1

    async for raw in chunks:
        # The chat proxy yields bytes; a literal "[DONE]" ends the stream.
        if isinstance(raw, (bytes, bytearray)):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
        else:
            text = str(raw)
        if not text.startswith("data:"):
            continue
        data = text[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("usage"):
            usage = _responses_usage(payload["usage"])

        for choice in payload.get("choices") or []:
            delta = choice.get("delta") or {}

            content = delta.get("content")
            if isinstance(content, str) and content:
                # The message item is opened on the first text fragment rather
                # than up front: a turn that only calls a function must not emit
                # an empty message item.
                if not msg_open:
                    msg_open = True
                    msg_index = next_index
                    next_index += 1
                    yield _responses_event(
                        "response.output_item.added", seq,
                        output_index=msg_index,
                        item={"id": msg_id, "type": "message",
                              "status": "in_progress", "content": [],
                              "role": "assistant"},
                    )
                    seq += 1
                    yield _responses_event(
                        "response.content_part.added", seq,
                        item_id=msg_id, output_index=msg_index, content_index=0,
                        part=_output_text_part(""),
                    )
                    seq += 1
                parts.append(content)
                yield _responses_event(
                    "response.output_text.delta", seq,
                    item_id=msg_id, output_index=msg_index, content_index=0,
                    delta=content, logprobs=[],
                )
                seq += 1

            for tc in delta.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                name = fn.get("name") or tc.get("name") or ""
                if name in ("WebSearch", "web_search", "WebFetch",
                            "web_fetch"):
                    # The router runs these itself and reports them as hosted
                    # tool calls. A function_call for them would ask the client
                    # to execute a tool the client was never given.
                    continue
                idx = int(tc.get("index", 0) or 0)
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                args = args if isinstance(args, str) else (
                    json.dumps(args) if args else "")
                entry = tools.get(idx)
                if entry is None:
                    fc_id = tc.get("id") or f"fc_{uuid.uuid4().hex}"
                    name = fn.get("name") or ""
                    entry = {"id": f"fc_{uuid.uuid4().hex}",
                             "call_id": fc_id, "name": name,
                             "arguments": "", "index": next_index}
                    next_index += 1
                    tools[idx] = entry
                    yield _responses_event(
                        "response.output_item.added", seq,
                        output_index=entry["index"],
                        item={"id": entry["id"], "type": "function_call",
                              "status": "in_progress",
                              "call_id": fc_id, "name": name, "arguments": ""},
                    )
                    seq += 1
                elif fn.get("name") and not entry["name"]:
                    entry["name"] = fn["name"]
                if not args:
                    continue
                entry["arguments"] += args
                yield _responses_event(
                    "response.function_call_arguments.delta", seq,
                    item_id=entry["id"], output_index=entry["index"],
                    delta=args,
                )
                seq += 1

    text = "".join(parts)
    cites = [c for r in searches for c in (r.get("citations") or [])
             if isinstance(c, dict)]
    if msg_open:
        yield _responses_event(
            "response.output_text.done", seq,
            item_id=msg_id, output_index=msg_index, content_index=0,
            text=text, logprobs=[],
        )
        seq += 1
        yield _responses_event(
            "response.content_part.done", seq,
            item_id=msg_id, output_index=msg_index, content_index=0,
            part=_output_text_part(text, cites),
        )
        seq += 1
        item = _output_message(msg_id, "completed", text, cites)
        yield _responses_event(
            "response.output_item.done", seq,
            output_index=msg_index, item=item,
        )
        seq += 1
        output.append(item)

    for idx in sorted(tools):
        entry = tools[idx]
        yield _responses_event(
            "response.function_call_arguments.done", seq,
            item_id=entry["id"], output_index=entry["index"],
            arguments=entry["arguments"], name=entry["name"],
        )
        seq += 1
        item = {"id": entry["id"], "type": "function_call",
                "status": "completed", "call_id": entry["call_id"],
                "name": entry["name"], "arguments": entry["arguments"]}
        yield _responses_event(
            "response.output_item.done", seq,
            output_index=entry["index"], item=item,
        )
        seq += 1
        output.append(item)

    # The chat layer only sends its trailing usage chunk when the client asks,
    # and sends nothing at all when the upstream stream is cut short.
    # `response.completed` is expected to carry usage either way, so fall back to
    # the same estimate the Anthropic path already reports: an estimate is closer
    # to the truth than null, which leaves a client unable to bill the call.
    final_usage = usage or _responses_usage({
        "prompt_tokens": input_tokens_hint,
        "completion_tokens": max(1, sum(len(p) for p in parts) // 4),
    })
    yield _responses_event(
        "response.completed", seq,
        response=envelope("completed", final_usage),
    )
    yield b"data: [DONE]\n\n"

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
        from src.core.api_config import AVAILABLE_MODELS, MODEL_CONTEXT_LENGTH
        from src.core.router import router
        model_alias = router.resolve_model_alias(requested_model)
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
        "stream": body.get("stream", False),
    }
    # Only forward max_tokens when the caller actually sent one. Writing a None
    # into the body looked like a value to everything downstream.
    if body.get("max_tokens"):
        chat_body["max_tokens"] = body["max_tokens"]
    try:
        requested_model = chat_body.get("model", "")
        from src.core.api_config import AVAILABLE_MODELS, MODEL_CONTEXT_LENGTH
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
        from src.core.api_config import MODEL_CONTEXT_LENGTH
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
        return JSONResponse(
            status_code=503,
            headers=response_headers,
            content={"type": "error", "error": {"type": "api_error", "message": "Service temporarily unavailable"}},
        )



@app.post("/api/ping-model")
async def ping_model(request: Request):
    # Admin only: this spends the operator's custom-endpoint quota by calling it
    # with that endpoint's own auth_key. Any logged-in tier could otherwise use it
    # as a free metered proxy.
    from .auth_session import _require_admin
    _require_admin(request)
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
    # Anthropic count_tokens reports INPUT tokens only — max_tokens (the output
    # budget) must not be folded in. Claude Code renders this as the context bar,
    # so including it inflated the reading by thousands of tokens.
    return {"input_tokens": estimate_input_tokens(body)}

"""OpenCode stream executor — SSE formatting only, no pool logic.

Pool management is entirely delegated to PoolManager.
This module handles:
  - SSE chunk formatting
  - Keepalive pings
  - XML thinking extraction
  - WebSearch tool execution and progress reporting
  - Usage logging after stream completes
"""

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, AsyncIterator, Optional, cast

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger

# Shared with the Claude proxy so both dialects bound a silent stream the same
# way. Layering note: this is an import from src/api/claude_proxy into
# src/api/opencode_proxy — sibling protocol clients, neither above the other —
# so it does not invert a layer. If that ever changes, the constant moves down
# to src/core/runtime instead.
from src.api.claude_proxy.handler.streaming.keepalive import (
    STREAM_STALL_TIMEOUT,
    StreamStalled,
)
from src.core.observability.usage_logger import log_usage
from src.core.providers.finish_reason import normalize_finish_reason
from src.logical_HQ_translator import (
    _get_simulated_cache_usage,
    StreamingTextNormalizer,
    XMLThinkingExtractor,
)
from src.core.orchestration.pool_manager import pool_manager
from .streaming import (
    get_content as _get_content,
    get_reasoning as _get_reasoning,
    get_thought_signature as _get_thought_signature,
    get_tool_calls as _get_tool_calls,
    openai_sse as _openai_sse,
)


async def _run_websearch_nonstream(
    body: Dict[str, Any],
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    model_alias: str,
    account: Optional[Dict[str, Any]],
    auth_key_prefix: str,
) -> tuple:
    """Run first-pass non-stream call to detect WebSearch tool call.
    Returns (text, search_context, new_messages_with_result).
    """
    from .websearch import resolve_search_engine
    from .search import (
        HOSTED_SEARCH_MAX_EVIDENCE_CHARS,
        HOSTED_SEARCH_MAX_RESULTS,
        execute_opencode_search,
    )

    thinking_config: Dict[str, Any] = {}
    max_tokens = min(int(body.get("max_tokens", config.MAX_OUTPUT_TOKENS)), config.MAX_OUTPUT_TOKENS)
    temperature = float(body.get("temperature", 0.7))

    result = await pool_manager.call_nonstream(
        model_alias=model_alias,
        messages=messages,
        tools=tools or None,
        temperature=temperature,
        max_tokens=max_tokens,
        thinking_config=thinking_config,
        account=account,
        extra_body=None,
    )

    resp = result["response"]
    if not hasattr(resp, "choices") or not resp.choices:
        return "", "", messages

    choice = resp.choices[0]
    msg = getattr(choice, "message", None)
    if not msg:
        return "", "", messages

    text = getattr(msg, "content", "") or ""
    raw_tool_calls = getattr(msg, "tool_calls", None) or []
    tool_calls = []
    for tc in raw_tool_calls:
        args = getattr(tc.function, "arguments", None) or ""
        if isinstance(args, dict):
            args = json.dumps(args)
        elif not isinstance(args, str):
            args = str(args)
        tool_calls.append({
            "id": getattr(tc, "id", f"call_{uuid.uuid4().hex}"),
            "name": getattr(tc.function, "name", "") or "",
            "arguments": args,
        })

    web_call = next((tc for tc in tool_calls if tc.get("name") == "WebSearch"), None)
    if not web_call:
        return text, "", messages

    try:
        args = json.loads(web_call["arguments"]) if isinstance(web_call["arguments"], str) else web_call["arguments"]
        query = args.get("query", "")
        se = resolve_search_engine(body, account)
        search_context, combined_citations = await execute_opencode_search(
            [query], model_alias_or_name=model_alias, search_engine=se,
            auth_key_prefix=auth_key_prefix, account=account,
            result_limit=HOSTED_SEARCH_MAX_RESULTS,
            evidence_chars=HOSTED_SEARCH_MAX_EVIDENCE_CHARS,
        )
        if search_context:
            result_lines = [search_context]
            unique_links = []
            for c in combined_citations:
                url = c.get("url")
                if url and url not in search_context:
                    title = c.get("title") or "Source"
                    unique_links.append(f"- [{title}]({url})")
            if unique_links:
                result_lines.append("\n**Sources / Citations:**\n" + "\n".join(unique_links))
            search_context = "\n".join(result_lines)
        else:
            search_context = "No search results found."
    except Exception as e:
        search_context = f"Search error: {e}"
        logger.warning("[OpenCode WebSearch] query failed: %s", e)

    assistant_msg = {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [{"id": web_call["id"], "type": "function", "function": {"name": "WebSearch", "arguments": web_call["arguments"]}}],
    }
    tool_result_msg = {"role": "tool", "tool_call_id": web_call["id"], "content": search_context}
    new_messages = list(messages) + [assistant_msg, tool_result_msg]
    return text, search_context, new_messages


def _arguments_are_valid_json(raw: str) -> bool:
    """Whether an accumulated argument fragment is parseable.

    An empty string is not a failure: it is a tool that takes no parameters.
    """
    if not raw.strip():
        return True
    try:
        json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False
    return True


async def execute_stream(
    body: Dict[str, Any],
    model_alias: str,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    account: Optional[Dict[str, Any]] = None,
    is_opencode: bool = False,
    auth_key_prefix: str = "",
) -> AsyncIterator[bytes]:
    """Main streaming entrypoint for OpenCode proxy.
    
    Delegates pool management to PoolManager.
    Handles SSE formatting, keepalive, thinking extraction, WebSearch progress.
    """
    from .response import get_client_model_name
    from .proxy import _resolve_thinking_config, _client_sampling_params

    # Same canonical client fields the nonstream path forwards: without this a
    # client asking for a forced tool got an automatic one on streams only.
    sampling_params = _client_sampling_params(body)
    include_thoughts = body.get("include_thoughts", True)
    thinking_params = {
        "thinking_level": body.get("thinking_level"),
        "thinking_budget": body.get("thinking_budget"),
        "include_thoughts": include_thoughts,
    }
    # The trailing usage chunk carries `choices: []`. OpenAI sends that only when
    # the client asks for it:
    #   include_usage: "If set, an additional chunk will be streamed before the
    #   data: [DONE] message. The usage field on this chunk shows the token usage
    #   statistics for the entire request, and the choices field will always be an
    #   empty array."
    # Emitting it unasked is a spec violation, and clients that index
    # chunk.choices[0] without a length check raise IndexError on it — which
    # surfaces as "invalid response" / "switch model" rather than anything
    # pointing at the real cause.
    _stream_opts = body.get("stream_options") or {}
    _wants_usage = bool(
        isinstance(_stream_opts, dict) and _stream_opts.get("include_usage") is True
    )
    has_websearch = any(t.get("function", {}).get("name") == "WebSearch" for t in tools)
    requested_model = body.get("model") or model_alias
    model_name = get_client_model_name(requested_model)
    chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
    thinking_config = _resolve_thinking_config(body, model_alias)
    max_tokens = min(int(body.get("max_tokens", config.MAX_OUTPUT_TOKENS)), config.MAX_OUTPUT_TOKENS)
    temperature = float(body.get("temperature", 0.7))

    # ── WebSearch path: non-stream first call to detect tool calls ─────────
    if has_websearch:
        _, _, new_messages = await _run_websearch_nonstream(
            body, messages, tools, model_alias, account, auth_key_prefix
        )

        # Final stream answer without search tool
        tools_no_search = [t for t in tools if t.get("function", {}).get("name") != "WebSearch"]
        async for item in pool_manager.call_stream(
            model_alias=model_alias,
            messages=new_messages,
            tools=tools_no_search or None,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_config=thinking_config,
            account=account,
            thinking_params=thinking_params,
            sampling_params=sampling_params,
        ):
            chunk = item["chunk"]
            delta = chunk.choices[0].delta if chunk.choices else None
            content = _get_content(delta)
            reasoning = _get_reasoning(delta)
            fr = chunk.choices[0].finish_reason if chunk.choices else None

            if reasoning and include_thoughts:
                yield _openai_sse(model_name, reasoning_content=reasoning, chunk_id=chunk_id)
            if content:
                yield _openai_sse(model_name, content=content, chunk_id=chunk_id)
            if fr:
                yield _openai_sse(
                    model_name,
                    finish_reason=normalize_finish_reason(
                        fr, has_tool_calls=bool(_get_tool_calls(delta))),
                    chunk_id=chunk_id)

        yield b"data: [DONE]\n\n"
        return

    # ── Normal stream path ──────────────────────────────────────────────────
    from .proxy import _is_sub_agent_request
    is_sub_agent = _is_sub_agent_request(body)

    normalizer = StreamingTextNormalizer()
    extractor = None if is_sub_agent else XMLThinkingExtractor()
    out_len = 0
    _reasoning_buf: List[str] = []
    _BUF_FLUSH_SIZE = 80
    stream_finish_reason = None
    stream_finished = False
    has_tool_calls = False
    # Tool calls are held back until the stream ends so an unfinished one can be
    # discarded. Forwarding fragments as they arrive means a call that
    # max_tokens cut mid-argument reaches the client labelled as complete, and
    # the client executes it with arguments that do not parse — a failure the
    # stream reports as success. Buffering costs nothing here: tool arguments
    # are only meaningful whole.
    tool_buffers: dict = {}
    thinking_enabled = bool(thinking_config)
    reasoning_received = False

    last_api_key = ""
    last_model_id = ""
    last_input_tokens = 0

    def _has_sentence_boundary(text: str) -> bool:
        for i, ch in enumerate(text):
            if ch in ".!?\n" and (i + 1 >= len(text) or text[i + 1] in " \n"):
                return True
        return False

    async def _flush_reasoning():
        nonlocal _reasoning_buf
        if not _reasoning_buf:
            return
        text = "".join(_reasoning_buf)
        _reasoning_buf = []
        if include_thoughts:
            yield _openai_sse(model_name, reasoning_content=text, chunk_id=chunk_id)

    async def _yield_reasoning(val: str):
        nonlocal out_len
        if not val or not include_thoughts:
            return
        _reasoning_buf.append(val)
        out_len += len(val)
        total = sum(len(s) for s in _reasoning_buf)
        buf_text = "".join(_reasoning_buf)
        if total >= _BUF_FLUSH_SIZE or _has_sentence_boundary(buf_text):
            async for c in _flush_reasoning():
                yield c

    async def _yield_text(val: str):
        nonlocal out_len
        if not val:
            return
        async for c in _flush_reasoning():
            yield c
        norm_text = normalizer.feed(val)
        if norm_text:
            yield _openai_sse(model_name, content=norm_text, chunk_id=chunk_id)
            out_len += len(norm_text)

    async def _process_content(content_val: str, has_reasoning: bool):
        if not content_val:
            return
        ex = cast(XMLThinkingExtractor, extractor)
        if has_reasoning:
            for _et, _ev in ex.feed(content_val):
                if _et == "text" and _ev:
                    async for c in _yield_text(_ev):
                        yield c
        else:
            for _et, _ev in ex.feed(content_val):
                if _et == "thinking" and _ev:
                    if include_thoughts:
                        async for c in _yield_reasoning(_ev):
                            yield c
                elif _et == "text" and _ev:
                    async for c in _yield_text(_ev):
                        yield c

    # Start pool-managed stream using a task for TTFB keepalive
    gen = None
    async def _fetch_first():
        g = pool_manager.call_stream(
            model_alias=model_alias,
            messages=messages,
            tools=tools or None,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_config=thinking_config,
            account=account,
            thinking_params=thinking_params,
            sampling_params=sampling_params,
        )
        try:
            first_item = await g.__anext__()
        except StopAsyncIteration:
            return None, None
        return g, first_item

    fetch_task = asyncio.create_task(_fetch_first())
    t0_wait = asyncio.get_event_loop().time()
    ping_count = 0
    yield b": keepalive-init\n\n"
    while not fetch_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(fetch_task), timeout=2.5)
            break
        except asyncio.TimeoutError:
            ping_count += 1
            if ping_count % 5 == 1:
                logger.info(
                    "[OpenCode Stream Keepalive] Waiting for %s (elapsed=%.1fs)",
                    model_alias, asyncio.get_event_loop().time() - t0_wait
                )
            yield b": keepalive\n\n"
            yield _openai_sse(model_name, chunk_id=chunk_id)

    try:
        gen, first_item = await fetch_task
    except Exception as e:
        logger.error("[OpenCode Stream] Failed to get first chunk: %s", e)
        raise e

    if gen is None or first_item is None:
        # The pool stream ended before producing anything — the client hung up,
        # or the model returned nothing. Emit a terminal chunk with finish_reason
        # so spec-compliant clients don't crash with
        # "Response stream ended without a finish reason."
        logger.info("[OpenCode Stream] %s closed before the first chunk", model_alias)
        yield _openai_sse(model_name, finish_reason="stop", chunk_id=chunk_id)
        yield b"data: [DONE]\n\n"
        return

    ttfb = asyncio.get_event_loop().time() - t0_wait
    logger.info("[OpenCode Stream] model=%s ttfb=%.2fs", model_alias, ttfb)

    async def _iter_with_keepalive():
        """Yield items, or None when the model goes quiet, without double-pulling.

        Same hazard as the Anthropic keepalive: `shield` keeps the in-flight
        __anext__ alive through a timeout, so starting another one on the next
        pass raises "asynchronous generator is already running". The pending
        pull is awaited again instead.

        A quiet model is kept alive; a silent one is not. Without the stall bound
        this loop yielded a keepalive forever while the provider had stopped
        responding, holding the request open and the coroutine on the event loop
        for as long as the client was willing to wait.
        """
        yield first_item
        it = gen.__aiter__()
        pending: asyncio.Future = asyncio.ensure_future(it.__anext__())
        last_item = time.monotonic()
        try:
            while True:
                try:
                    item = await asyncio.wait_for(asyncio.shield(pending), timeout=3.0)
                except asyncio.TimeoutError:
                    now = time.monotonic()
                    if now - last_item >= STREAM_STALL_TIMEOUT:
                        raise StreamStalled(
                            f"provider sent nothing for {now - last_item:.0f}s"
                        ) from None
                    yield None          # keepalive; the pull is still in flight
                    continue
                except StopAsyncIteration:
                    return
                yield item
                last_item = time.monotonic()
                pending = asyncio.ensure_future(it.__anext__())
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            close = getattr(it, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass

    try:
        async for item in _iter_with_keepalive():
            if item is None:
                yield b": keepalive\n\n"
                yield _openai_sse(model_name, chunk_id=chunk_id)
                continue

            last_api_key = item.get("api_key", last_api_key)
            last_model_id = item.get("model_id", last_model_id)
            last_input_tokens = item.get("input_tokens", last_input_tokens)

            chunk = item["chunk"]
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            content = _get_content(delta)
            reasoning = _get_reasoning(delta)
            tool_calls = _get_tool_calls(delta)
            ts = _get_thought_signature(delta)
            fr = chunk.choices[0].finish_reason if chunk.choices else None
            if fr:
                stream_finish_reason = fr
                stream_finished = True

            if reasoning:
                reasoning_received = True
                if is_sub_agent:
                    async for c in _yield_text(reasoning):
                        yield c
                else:
                    async for c in _yield_reasoning(reasoning):
                        yield c
            if content:
                if is_sub_agent or not extractor:
                    async for c in _yield_text(content):
                        yield c
                else:
                    async for c in _process_content(content, bool(reasoning)):
                        yield c
            if tool_calls:
                for tc in tool_calls:
                    idx = tc.get("index", 0)
                    buf = tool_buffers.setdefault(
                        idx, {"id": None, "type": "function", "name": "", "args": ""},
                    )
                    if tc.get("id"):
                        buf["id"] = tc["id"]
                    if tc.get("name"):
                        buf["name"] = tc["name"]
                    raw = tc.get("arguments") or ""
                    if isinstance(raw, dict):
                        raw = json.dumps(raw)
                    # Same three shapes the Claude proxy has to survive: real
                    # fragments concatenate, a `{}` placeholder is replaced by
                    # the body that follows it, and a fragment a provider repeats
                    # alongside finish_reason is not counted twice.
                    if buf["args"] == raw:
                        continue
                    if buf["args"] == "{}":
                        buf["args"] = raw
                    else:
                        buf["args"] += raw

        if extractor:
            async for c in _flush_reasoning():
                yield c
            for _et, _ev in extractor.flush():
                if _et == "thinking" and _ev and include_thoughts:
                    async for c in _yield_reasoning(_ev):
                        yield c
                elif _et == "text" and _ev:
                    async for c in _yield_text(_ev):
                        yield c
            async for c in _flush_reasoning():
                yield c
        flushed = normalizer.flush()
        if flushed:
            async for c in _yield_text(flushed):
                yield c

        incomplete_tool_call = False
        for idx in sorted(tool_buffers):
            buf = tool_buffers[idx]
            if buf["args"] and not _arguments_are_valid_json(buf["args"]):
                logger.warning(
                    "[OpenCode Stream] dropping %s (id=%s): arguments are not valid "
                    "JSON, the provider stopped mid-call", buf["name"], buf["id"])
                incomplete_tool_call = True
                continue
            has_tool_calls = True
            yield _openai_sse(model_name, tool_calls=[{
                "id": buf["id"], "type": buf["type"],
                "index": idx, "name": buf["name"], "arguments": buf["args"],
            }], thought_signature=ts, chunk_id=chunk_id)

        if not stream_finished:
            # An upstream EOF without a provider finish signal is a broken
            # stream, not a successful `stop`. Let the route expose an error so
            # an agent can retry instead of ending its task silently.
            raise RuntimeError("stream_ended_without_finish_reason")

        stop_reason = normalize_finish_reason(stream_finish_reason,
                                          has_tool_calls=has_tool_calls,
                                          incomplete_tool_call=incomplete_tool_call)
        yield _openai_sse(model_name, finish_reason=stop_reason, chunk_id=chunk_id)

        out_tokens = max(1, out_len // 4)
        kp = last_api_key[-8:] if last_api_key else ""
        cache_usage = _get_simulated_cache_usage(body, last_input_tokens)
        cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
        cr = cache_usage.get("cache_read_input_tokens", 0) or 0
        await log_usage(last_model_id, kp, last_input_tokens, out_tokens, auth_key_prefix, cc, cr)

        if _wants_usage:
            usage_chunk = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_name,
                "choices": [],
                "usage": {
                    "prompt_tokens": last_input_tokens,
                    "completion_tokens": out_tokens,
                    "total_tokens": last_input_tokens + out_tokens,
                },
            }
            yield f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
        if thinking_enabled and not reasoning_received:
            logger.info(
                "[OpenCode Stream] thinking enabled but no reasoning_content from SDK model=%s (API may not return thought parts in stream mode)",
                model_alias,
            )

        yield b"data: [DONE]\n\n"

    except asyncio.CancelledError:
        logger.warning("[OpenCode Stream Cancelled] model=%s", model_alias)
        yield _openai_sse(model_name, finish_reason="stop", chunk_id=chunk_id)
        raise
    except Exception as e:
        logger.error("[OpenCode Stream Error] model=%s: %s", model_alias, e, exc_info=True)
        yield _openai_sse(model_name, finish_reason="error", chunk_id=chunk_id)
        raise
    finally:
        # Ensure the underlying stream is closed
        try:
            if gen is not None and hasattr(gen, "aclose"):
                await gen.aclose()
        except Exception as e:
            logger.warning("[OpenCode Stream] Error closing underlying stream: %s", e)


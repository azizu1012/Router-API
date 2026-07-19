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
import uuid
import time
from typing import Any, Dict, List, AsyncIterator, Optional, cast

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.usage_logger import log_usage
from src.logical_HQ_translator import (
    _get_simulated_cache_usage,
    StreamingTextNormalizer,
    XMLThinkingExtractor,
)
from src.core.pool_manager import pool_manager


def _openai_sse(
    model_name: str,
    content: Optional[str] = None,
    tool_calls: Optional[List[dict]] = None,
    finish_reason: Optional[str] = None,
    chunk_id: Optional[str] = None,
    reasoning_content: Optional[str] = None,
    thought_signature: Optional[str] = None,
) -> bytes:
    if not chunk_id:
        chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
    data: Dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
    }
    if content is not None:
        data["choices"][0]["delta"]["content"] = content
    if reasoning_content is not None:
        data["choices"][0]["delta"]["reasoning_content"] = reasoning_content
    if tool_calls:
        data["choices"][0]["delta"]["tool_calls"] = [
            {
                "index": idx,
                "id": tc.get("id") or f"call_{uuid.uuid4().hex}",
                "type": tc.get("type", "function"),
                "function": {"name": tc.get("name", ""), "arguments": tc.get("arguments", "")},
            }
            for idx, tc in enumerate(tool_calls)
        ]
    if thought_signature is not None:
        data["choices"][0]["delta"]["thought_signature"] = thought_signature
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _get_reasoning(delta_obj) -> Optional[str]:
    if not delta_obj:
        return None
    for attr in ("reasoning_content", "thought", "reasoning"):
        v = getattr(delta_obj, attr, None)
        if v:
            return v
    if hasattr(delta_obj, "get"):
        for key in ("reasoning_content", "thought", "reasoning"):
            try:
                v = delta_obj.get(key)
                if v:
                    return v
            except Exception:
                pass
    for attr in ("model_extra", "extra_fields", "additional_kwargs"):
        extra = getattr(delta_obj, attr, None)
        if extra and isinstance(extra, dict):
            for key in ("reasoning_content", "thought", "reasoning"):
                if extra.get(key):
                    return extra[key]
    return None


def _get_content(delta_obj) -> Optional[str]:
    if not delta_obj:
        return None
    v = getattr(delta_obj, "content", None)
    if v:
        return v
    if hasattr(delta_obj, "get"):
        try:
            v = delta_obj.get("content")
            if v:
                return v
        except Exception:
            pass
    for attr in ("model_extra", "extra_fields", "additional_kwargs"):
        extra = getattr(delta_obj, attr, None)
        if extra and isinstance(extra, dict):
            if extra.get("content"):
                return extra["content"]
    return None


def _get_tool_calls(delta_obj) -> Optional[List[dict]]:
    if not delta_obj:
        return None
    tcs = getattr(delta_obj, "tool_calls", None)
    if tcs:
        res = []
        for tc in tcs:
            if isinstance(tc, dict):
                fn = tc.get("function", {})
                fn_name = fn.get("name", "") if isinstance(fn, dict) else ""
                fn_args = fn.get("arguments", "") if isinstance(fn, dict) else ""
                tc_id = tc.get("id")
                tc_type = tc.get("type", "function")
            else:
                tc_id = getattr(tc, "id", None)
                tc_type = getattr(tc, "type", "function")
                fn_name = getattr(tc.function, "name", "") if getattr(tc, "function", None) else ""
                fn_args = getattr(tc.function, "arguments", "") if getattr(tc, "function", None) else ""
            res.append({"id": tc_id, "type": tc_type, "name": fn_name, "arguments": fn_args})
        return res
    return None


def _get_thought_signature(delta_obj) -> Optional[str]:
    if not delta_obj:
        return None
    v = getattr(delta_obj, "thought_signature", None)
    if v:
        return v
    if hasattr(delta_obj, "get"):
        try:
            v = delta_obj.get("thought_signature")
            if v:
                return v
        except Exception:
            pass
    return None


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
    from .search import execute_opencode_search

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
    from .proxy import _resolve_thinking_config

    include_thoughts = body.get("include_thoughts", True)
    thinking_params = {
        "thinking_level": body.get("thinking_level"),
        "thinking_budget": body.get("thinking_budget"),
        "include_thoughts": include_thoughts,
    }
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
                yield _openai_sse(model_name, finish_reason=fr, chunk_id=chunk_id)

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
    has_tool_calls = False
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
        nonlocal out_len, _reasoning_buf
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
    async def _fetch_first():
        gen = pool_manager.call_stream(
            model_alias=model_alias,
            messages=messages,
            tools=tools or None,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_config=thinking_config,
            account=account,
            thinking_params=thinking_params,
        )
        try:
            first_item = await gen.__anext__()
        except StopAsyncIteration:
            raise RuntimeError("Empty stream from pool manager")
        return gen, first_item

    fetch_task = asyncio.create_task(_fetch_first())
    t0_wait = asyncio.get_event_loop().time()
    ping_count = 0
    while not fetch_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(fetch_task), timeout=3.0)
            break
        except asyncio.TimeoutError:
            ping_count += 1
            if ping_count % 5 == 1:
                logger.info(
                    "[OpenCode Stream Keepalive] Waiting for %s (elapsed=%.1fs)",
                    model_alias, asyncio.get_event_loop().time() - t0_wait
                )
            yield _openai_sse(model_name, chunk_id=chunk_id)

    try:
        gen, first_item = await fetch_task
    except Exception as e:
        logger.error("[OpenCode Stream] Failed to get first chunk: %s", e)
        raise e

    ttfb = asyncio.get_event_loop().time() - t0_wait
    logger.info("[OpenCode Stream] model=%s ttfb=%.2fs", model_alias, ttfb)

    async def _iter_with_keepalive():
        yield first_item
        it = gen.__aiter__()
        while True:
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(asyncio.shield(it.__anext__()), timeout=4.0)
                        yield item
                        break
                    except asyncio.TimeoutError:
                        yield None
            except StopAsyncIteration:
                break

    try:
        async for item in _iter_with_keepalive():
            if item is None:
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
                has_tool_calls = True
                yield _openai_sse(model_name, tool_calls=tool_calls, thought_signature=ts, chunk_id=chunk_id)

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

        stop_reason = "tool_calls" if has_tool_calls else (
            "length" if stream_finish_reason and "max" in str(stream_finish_reason).lower() else "stop"
        )
        yield _openai_sse(model_name, finish_reason=stop_reason, chunk_id=chunk_id)

        out_tokens = max(1, out_len // 4)
        kp = last_api_key[-8:] if last_api_key else ""
        cache_usage = _get_simulated_cache_usage(body, last_input_tokens)
        cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
        cr = cache_usage.get("cache_read_input_tokens", 0) or 0
        await log_usage(last_model_id, kp, last_input_tokens, out_tokens, auth_key_prefix, cc, cr)

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
        raise
    except Exception as e:
        logger.error("[OpenCode Stream Error] model=%s: %s", model_alias, e, exc_info=True)
        raise

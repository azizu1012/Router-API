import asyncio
import json
import uuid
import time
from typing import Any, Dict, List, AsyncIterator, Optional

from src.core.providers.litellm_wrapper import acompletion, token_counter
from fastapi import HTTPException
from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.limits import apply_error_penalty
from src.core.usage_logger import log_usage
from src.api.claude_proxy.utils import (
    _resolve_model,
    _get_simulated_cache_usage,
    _retry_delay,
    StreamingTextNormalizer,
    normalize_text,
    XMLThinkingExtractor,
)
from .sse import openai_chunks, error_sse
from .nonstream_executor import _resolve_gemini_with_tools, _resolve_gemini_with_tools_stream


class LiteLLMTransientError(Exception):
    """Lỗi tạm thời từ litellm (rate limit, region quota, v.v.) cần retry pool."""
    def __init__(self, message: str, is_region_quota: bool = False):
        super().__init__(message)
        self.is_region_quota = is_region_quota


def _openai_sse(
    model_name: str,
    content: Optional[str] = None,
    tool_calls: Optional[List[dict]] = None,
    finish_reason: Optional[str] = None,
    chunk_id: Optional[str] = None,
    reasoning_content: Optional[str] = None,
) -> bytes:
    if not chunk_id:
        chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
    data = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason,
            }
        ]
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
                "function": {
                    "name": tc.get("name", ""),
                    "arguments": tc.get("arguments", "")
                }
            }
            for idx, tc in enumerate(tool_calls)
        ]
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


async def _execute_stream(
    proxy_instance: Any,
    kwargs: Dict[str, Any],
    api_key: str,
    model_id: str,
    model_alias: str,
    input_tokens: int,
    pool: Any,
    body: Dict[str, Any],
    auth_key_prefix: str = "",
    account: Optional[Dict[str, Any]] = None,
    is_opencode: bool = False,
) -> AsyncIterator[bytes]:
    tools = kwargs.get("tools") or []
    has_websearch = any(
        tool.get("function", {}).get("name") == "WebSearch"
        for tool in tools
    )
    from .response import get_client_model_name
    requested_model = body.get("model") or model_alias
    model_name = get_client_model_name(requested_model)

    if has_websearch:
        kwargs_ns = {k: v for k, v in kwargs.items() if k != "stream"}

        async def _nonstream_wrapper():
            kp = api_key[-8:] if api_key else ""
            fetch_task = None
            try:
                # Yield initial chunk
                chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
                yield _openai_sse(model_name, content="", chunk_id=chunk_id)

                async def _fetch_websearch():
                    return await _resolve_gemini_with_tools(
                        kwargs_ns, body, proxy_instance, auth_key_prefix=auth_key_prefix, account=account
                    )

                fetch_task = asyncio.create_task(asyncio.to_thread(lambda: None))  # dummy, real work below
                t0_wait = asyncio.get_event_loop().time()
                search_statuses = [
                    (0, "🔍 Searching..."),
                    (3, "📡 Querying DuckDuckGo..."),
                    (6, "📄 Reading results..."),
                    (9, "⚡ Synthesizing data..."),
                    (12, "⏳ Almost done..."),
                ]
                status_sent = 0
                text_buf: List[str] = []
                thinking_buf: List[str] = []
                stream_tool_calls: List[Dict[str, Any]] = []
                stream_fr = "stop"
                stream_thought: str = ""
                reasoning_active = False

                # Start streaming from the stream generator directly
                stream_iter = _resolve_gemini_with_tools_stream(
                    kwargs_ns, body, proxy_instance, auth_key_prefix=auth_key_prefix, account=account
                ).__aiter__()
                stream_done = False
                while not stream_done:
                    try:
                        evt_type, *evt_vals = await asyncio.wait_for(stream_iter.__anext__(), timeout=2.0)
                        elapsed = asyncio.get_event_loop().time() - t0_wait
                        if evt_type == "reasoning":
                            val = evt_vals[0]
                            thinking_buf.append(val)
                            yield _openai_sse(model_name, reasoning_content=val, chunk_id=chunk_id)
                        elif evt_type == "text":
                            val = evt_vals[0]
                            text_buf.append(val)
                            yield _openai_sse(model_name, content=val, chunk_id=chunk_id)
                        elif evt_type == "result":
                            text_buf, stream_tool_calls, stream_fr, stream_thought = evt_vals
                            if isinstance(text_buf, str):
                                text_buf = [text_buf]
                            stream_done = True
                    except asyncio.TimeoutError:
                        elapsed = asyncio.get_event_loop().time() - t0_wait
                        for t, msg in search_statuses:
                            if elapsed >= t and status_sent <= t:
                                status_sent = t + 0.01
                                logger.info("[OpenCode Search Status] %.1fs: %s", elapsed, msg)
                                yield _openai_sse(model_name, content=msg + "\n", chunk_id=chunk_id)
                    except StopAsyncIteration:
                        stream_done = True

                text = "".join(text_buf) if text_buf else ""
                thought_text = "".join(thinking_buf) if thinking_buf else stream_thought
                tool_calls = stream_tool_calls
                finish_reason = stream_fr

                elapsed_total = asyncio.get_event_loop().time() - t0_wait
                logger.info(
                    "[OpenCode ToolResolve Stream] model=%s elapsed=%.2fs text_len=%d tools=%d thought_len=%d",
                    model_alias, elapsed_total, len(text), len(tool_calls), len(thought_text)
                )

                if tool_calls:
                    yield _openai_sse(model_name, tool_calls=tool_calls, chunk_id=chunk_id)

                try:
                    out_tokens = await token_counter(model=kwargs.get("model", "gemini/gemini-1.5-flash"), messages=[{"role": "assistant", "content": text}])
                except Exception:
                    out_tokens = max(1, len(text) // 4) if text else 1
                out_tokens += len(tool_calls) * 50

                stop_reason = "tool_calls" if tool_calls else ("length" if "max" in str(finish_reason).lower() else "stop")
                yield _openai_sse(model_name, finish_reason=stop_reason, chunk_id=chunk_id)

                cache_usage = _get_simulated_cache_usage(body, input_tokens)
                cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
                cr = cache_usage.get("cache_read_input_tokens", 0) or 0
                await log_usage(model_id, kp, input_tokens, out_tokens, auth_key_prefix, cc, cr)
                router.record_success(api_key, model_id)
                if pool:
                    pool.record_success()

                cost = proxy_instance._estimate_cost(input_tokens, out_tokens, model_alias)
                usage_dict = {
                    "prompt_tokens": input_tokens,
                    "completion_tokens": out_tokens,
                    "total_tokens": input_tokens + out_tokens,
                    "cost": cost,
                }
                if cr > 0:
                    usage_dict["prompt_tokens_details"] = {
                        "cached_tokens": cr
                    }
                usage_chunk = {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_name,
                    "choices": [],
                    "usage": usage_dict
                }
                yield f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"

            except asyncio.CancelledError:
                logger.warning(
                    "[OpenCode Stream Cancelled] Client disconnected/cancelled request prematurely for model=%s, key=...%s",
                    model_alias, kp
                )
                if fetch_task and not fetch_task.done():
                    fetch_task.cancel()
                raise
            except Exception as e:
                logger.error(
                    "[OpenCode Stream Exception] Unexpected error for model=%s, key=...%s: %s",
                    model_alias, kp, e, exc_info=True
                )
                if fetch_task and not fetch_task.done():
                    fetch_task.cancel()
                raise
            finally:
                router.release_key(api_key)

        return _nonstream_wrapper()

    kwargs["stream"] = True

    async def _stream_wrapper():
        kp = api_key[-8:] if api_key else ""
        fetch_task = None
        try:
            async def _fetch_stream():
                g = await acompletion(**kwargs)
                if g is None:
                    raise LiteLLMTransientError("litellm.acompletion returned None")
                first = await g.__anext__()
                return g, first

            fetch_task = asyncio.create_task(_fetch_stream())
            t0_wait = asyncio.get_event_loop().time()
            ping_count = 0
            while not fetch_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(fetch_task), timeout=3.0)
                    break
                except asyncio.TimeoutError:
                    elapsed = asyncio.get_event_loop().time() - t0_wait
                    ping_count += 1
                    if ping_count % 5 == 1:
                        logger.info(
                            "[OpenCode Stream Keepalive] Still waiting for %s response (elapsed=%.1fs)",
                            model_alias, elapsed
                        )
                    yield b": keepalive\n\n"

            gen, first_chunk = await fetch_task
            ttfb = asyncio.get_event_loop().time() - t0_wait
            logger.info("[OpenCode Stream] model=%s ttfb=%.2fs", model_alias, ttfb)

            normalizer = StreamingTextNormalizer()
            extractor = XMLThinkingExtractor()
            out_len = 0
            _reasoning_buf: List[str] = []
            _BUF_FLUSH_SIZE = 80

            def _has_sentence_boundary(text: str) -> bool:
                for i, ch in enumerate(text):
                    if ch in '.!?\n' and (i + 1 >= len(text) or text[i + 1] in ' \n'):
                        return True
                return False

            async def _flush_reasoning():
                nonlocal _reasoning_buf
                if not _reasoning_buf:
                    return
                text = "".join(_reasoning_buf)
                _reasoning_buf = []
                yield _openai_sse(model_name, reasoning_content=text)

            async def _yield_reasoning(val: str):
                nonlocal out_len, _reasoning_buf
                if not val:
                    return
                _reasoning_buf.append(val)
                out_len += len(val)
                total = sum(len(s) for s in _reasoning_buf)
                buf_text = "".join(_reasoning_buf)
                if total >= _BUF_FLUSH_SIZE or _has_sentence_boundary(buf_text):
                    async for chunk in _flush_reasoning():
                        yield chunk

            async def _yield_text(val: str):
                nonlocal out_len
                if not val:
                    return
                async for chunk in _flush_reasoning():
                    yield chunk
                norm_text = normalizer.feed(val)
                if norm_text:
                    yield _openai_sse(model_name, content=norm_text)
                    out_len += len(norm_text)

            async def _process_extractor_events(events):
                for ev_type, ev_val in events:
                    if ev_type == "thinking" and ev_val:
                        async for chunk in _yield_reasoning(ev_val):
                            yield chunk
                    elif ev_type == "text" and ev_val:
                        async for chunk in _yield_text(ev_val):
                            yield chunk

            # Helper: process content through extractor, skip thinking if reasoning already from delta
            async def _process_content(content_val: str, has_reasoning: bool):
                if not content_val:
                    return
                if has_reasoning:
                    for _et, _ev in extractor.feed(content_val):
                        if _et == "text" and _ev:
                            async for c in _yield_text(_ev):
                                yield c
                else:
                    async for c in _process_extractor_events(extractor.feed(content_val)):
                        yield c

            # Process first_chunk
            delta_1 = first_chunk.choices[0].delta if first_chunk.choices else None
            content_1 = getattr(delta_1, "content", None) if delta_1 else None
            reasoning_1 = (getattr(delta_1, "reasoning_content", None) or 
                           getattr(delta_1, "thought", None) or 
                           getattr(delta_1, "reasoning", None)) if delta_1 else None
            
            if reasoning_1:
                async for chunk_bytes in _yield_reasoning(reasoning_1):
                    yield chunk_bytes

            if content_1:
                async for chunk_bytes in _process_content(content_1, bool(reasoning_1)):
                    yield chunk_bytes

            # Process subsequent chunks
            async for chunk in gen:
                delta = chunk.choices[0].delta if chunk.choices else None
                content = getattr(delta, "content", None) if delta else None
                reasoning = (getattr(delta, "reasoning_content", None) or 
                             getattr(delta, "thought", None) or 
                             getattr(delta, "reasoning", None)) if delta else None

                if reasoning:
                    async for chunk_bytes in _yield_reasoning(reasoning):
                        yield chunk_bytes

                if content:
                    async for chunk_bytes in _process_content(content, bool(reasoning)):
                        yield chunk_bytes

            # Flush remaining reasoning buffer
            async for chunk_bytes in _flush_reasoning():
                yield chunk_bytes

            # Flush extractor at the end of the stream
            async for chunk_bytes in _process_extractor_events(extractor.flush()):
                yield chunk_bytes

            # Flush any remaining reasoning from extractor flush
            async for chunk_bytes in _flush_reasoning():
                yield chunk_bytes

            # Flush normalizer at the end of the stream
            flushed_content = normalizer.flush()
            if flushed_content:
                async for chunk_bytes in _yield_text(flushed_content):
                    yield chunk_bytes

            out_tokens = max(1, out_len // 4)
            cache_usage = _get_simulated_cache_usage(body, input_tokens)
            cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
            cr = cache_usage.get("cache_read_input_tokens", 0) or 0
            await log_usage(model_id, kp, input_tokens, out_tokens, auth_key_prefix, cc, cr)
            router.record_success(api_key, model_id, input_tokens, out_tokens)
            if pool:
                pool.record_success()

            cost = proxy_instance._estimate_cost(input_tokens, out_tokens, model_alias)
            usage_dict = {
                "prompt_tokens": input_tokens,
                "completion_tokens": out_tokens,
                "total_tokens": input_tokens + out_tokens,
                "cost": cost,
            }
            if cr > 0:
                usage_dict["prompt_tokens_details"] = {
                    "cached_tokens": cr
                }
            usage_chunk = {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_name,
                "choices": [],
                "usage": usage_dict
            }
            yield f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

        except asyncio.CancelledError:
            logger.warning(
                "[OpenCode Stream Cancelled] Client disconnected/cancelled stream prematurely for model=%s, key=...%s",
                model_alias, kp
            )
            if fetch_task and not fetch_task.done():
                fetch_task.cancel()
            raise
        except Exception as e:
            logger.error(
                "[OpenCode Stream Exception] Unexpected stream-level error for model=%s, key=...%s: %s",
                model_alias, kp, e, exc_info=True
            )
            if fetch_task and not fetch_task.done():
                fetch_task.cancel()
            raise
        finally:
            router.release_key(api_key)

    return _stream_wrapper()


async def _stream_with_pool(
    proxy_instance: Any,
    body: Dict[str, Any],
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    pool: Any,
    model_alias: str,
    auth_key_prefix: str = "",
    account: Optional[Dict[str, Any]] = None,
    is_opencode: bool = False,
) -> AsyncIterator[bytes]:
    committed = False
    pool.start()
    while not pool.exhausted:
        actual_alias = pool.current_model
        api_key_val = None
        saved_key = None
        model_id_val = None
        try:
            est_input = len(str(messages)) // 4
            max_output = min(int(body.get("max_tokens", config.MAX_OUTPUT_TOKENS)), config.MAX_OUTPUT_TOKENS)
            estimated_tokens = est_input + max_output
            model_alias_val, model_id_val, api_key_val, litellm_model_val, reservation = await _resolve_model(
                body, actual_alias, account=account, estimated_tokens=estimated_tokens,
                retry_attempt=pool.total_attempts, pool_mode=True
            )
            logger.info(
                "[OpenCode Pool Reserve Stream] Reserved key ...%s for model_alias=%s (model_id=%s) | Attempt: %d (remaining=%ds)",
                api_key_val[-8:] if api_key_val else "N/A", actual_alias, model_id_val, pool.total_attempts + 1, int(pool.remaining_time())
            )

            try:
                try:
                    input_tokens = await token_counter(model=litellm_model_val, messages=messages)
                except Exception:
                    input_tokens = max(1, len(str(messages)) // 4)

                max_output = min(int(body.get("max_tokens", config.MAX_OUTPUT_TOKENS)), config.MAX_OUTPUT_TOKENS)
                has_quota = await router.acquire_quota(input_tokens + max_output, actual_alias)
                if not has_quota:
                    apply_error_penalty(api_key_val, "rate_limit_rpm_tpm", model_id_val)
                    router.freeze_key(api_key_val, 15, model_id_val, "rate_limit")
                    if pool.record_failure(actual_alias, "rate_limit"):
                        if not pool.swap():
                            if pool.exhausted:
                                break
                            wait = min(15.0, pool.remaining_time())
                            for _ in range(int(wait)):
                                yield b": keepalive\n\n"
                                await asyncio.sleep(1)
                            pool.reset_cycle()
                            continue
                    await asyncio.sleep(_retry_delay(pool.total_attempts))
                    continue

                attempt_val = pool.total_attempts
                kwargs = proxy_instance._prepare_litellm_kwargs(
                    litellm_model_val=litellm_model_val,
                    reinforced_messages=messages,
                    api_key_val=api_key_val,
                    max_output=max_output,
                    body=body,
                    openai_tools=tools,
                    reservation=reservation,
                    is_stream=True,
                )

                gen = await _execute_stream(
                    proxy_instance, kwargs, api_key_val, model_id_val, model_alias_val,
                    input_tokens, pool, body, auth_key_prefix, account=account,
                    is_opencode=is_opencode
                )
                saved_key = api_key_val
                api_key_val = None
                async for chunk in gen:
                    has_real_content = False
                    if b'"content":' in chunk and not b'"content": ""' in chunk and not b'"content": null' in chunk:
                        has_real_content = True
                    if b'"tool_calls"' in chunk:
                        has_real_content = True
                    if has_real_content:
                        committed = True
                    yield chunk
                return

            finally:
                if api_key_val:
                    router.release_key(api_key_val)

        except HTTPException as e:
            if committed:
                logger.error("[OpenCode Stream Pool Exception] HTTP exception after stream committed: %s", e)
                yield error_sse({"error": {"type": "api_error", "message": f"Stream error: {e}"}})[0]
                return
            if e.status_code == 503:
                raise
            _err_key = api_key_val or saved_key
            if _err_key:
                router.freeze_key(_err_key, 15, model_id_val, "rate_limit")
                apply_error_penalty(_err_key, "rate_limit", model_id_val)
            router.record_failure("rate_limit")
            if pool.record_failure(actual_alias, "rate_limit"):
                if not pool.swap():
                    if pool.exhausted:
                        break
                    wait = min(15.0, pool.remaining_time())
                    for _ in range(int(wait)):
                        yield b": keepalive\n\n"
                        await asyncio.sleep(1)
                    pool.reset_cycle()
                    continue
            await asyncio.sleep(_retry_delay(pool.total_attempts))

        except asyncio.CancelledError:
            logger.warning(
                "[OpenCode Pool Cancelled] Stream cancelled by client during pool retry loop for alias=%s", actual_alias
            )
            raise
        except Exception as e:
            if committed:
                logger.error("[OpenCode Stream Pool Exception] Exception after stream committed: %s", e)
                yield error_sse({"error": {"type": "api_error", "message": f"Stream error: {e}"}})[0]
                return
            error_text = str(e).lower()

            if "400" in error_text and "failed_precondition" not in error_text and ("invalid_argument" in error_text or "bad_request" in error_text):
                logger.error("[OpenCode Bad Request] Schema/Prompt Error: %s", error_text[:200])
                if api_key_val:
                    router.freeze_key(api_key_val, 2, model_id_val, "bad_request_spam_prevent")
                yield error_sse({"error": {"type": "invalid_request_error", "message": f"LLM rejected payload (HTTP 400): {error_text[:200]}"}})[0]
                return

            if "400" in error_text and "failed_precondition" in error_text:
                logger.error("[OpenCode Failed Precondition] Billing issue with key ...%s: %s", (api_key_val or "N/A")[-4:], error_text[:200])
                if api_key_val:
                    router.freeze_key(api_key_val, 300, model_id_val, "billing_error")
                    apply_error_penalty(api_key_val, "billing_error", model_id_val)
                router.record_failure("billing_error")
                if pool.record_failure(actual_alias, "billing_error"):
                    if not pool.swap():
                        if pool.exhausted:
                            break
                        wait = min(15.0, pool.remaining_time())
                        for _ in range(int(wait)):
                            yield b": keepalive\n\n"
                            await asyncio.sleep(1)
                        pool.reset_cycle()
                        continue
                await asyncio.sleep(_retry_delay(pool.total_attempts))
                continue

            if "499" in error_text or "cancelled" in error_text:
                return

            is_region_quota = "apirequestsperminuteperprojectperregion" in error_text or "api_requests_per_minute_per_project_per_region" in error_text
            if is_region_quota:
                reason = "rate_limit"
            else:
                from src.api.claude_proxy.handler.helpers import _classify_error_reason as _classify_error_reason_static
                reason = _classify_error_reason_static(error_text, api_key_val, model_id_val)

            if reason == "rate_limit":
                router.record_429()

            _err_key = api_key_val or saved_key
            if _err_key:
                router.freeze_key(_err_key, 0, model_id_val, reason)
                if reason not in ("bad_request_spam_prevent", "invalid_key"):
                    apply_error_penalty(_err_key, reason, model_id_val)
            router.record_failure(reason)

            if pool.record_failure(actual_alias, reason):
                if not pool.swap():
                    if pool.exhausted:
                        break
                    wait = min(15.0, pool.remaining_time())
                    for _ in range(int(wait)):
                        yield b": keepalive\n\n"
                        await asyncio.sleep(1)
                    pool.reset_cycle()
                    continue

            if is_region_quota:
                logger.warning("[OpenCode Region Quota] Hit region limit, waiting 25s before retry...")
                await asyncio.sleep(25)
            else:
                await asyncio.sleep(_retry_delay(pool.total_attempts))

    logger.warning("[OpenCode Pool Exhausted] Request timed out after %.1fs.", pool.elapsed)
    err_resp = proxy_instance._error_response(body, model_alias, "pool_exhausted")
    for chunk in error_sse(err_resp):
        yield chunk


async def _stream_standalone(
    proxy_instance: Any,
    body: Dict[str, Any],
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    model_alias: str,
    auth_key_prefix: str = "",
    account: Optional[Dict[str, Any]] = None,
    is_opencode: bool = False,
) -> AsyncIterator[bytes]:
    committed = False
    for attempt in range(config.MAX_RETRIES):
        api_key_val = None
        saved_key = None
        model_id_val = None
        try:
            est_input = len(str(messages)) // 4
            max_output = min(int(body.get("max_tokens", config.MAX_OUTPUT_TOKENS)), config.MAX_OUTPUT_TOKENS)
            estimated_tokens = est_input + max_output
            model_alias_val, model_id_val, api_key_val, litellm_model_val, reservation = await _resolve_model(
                body, model_alias, account=account, estimated_tokens=estimated_tokens,
                retry_attempt=attempt, pool_mode=False
            )

            try:
                try:
                    input_tokens = await token_counter(model=litellm_model_val, messages=messages)
                except Exception:
                    input_tokens = max(1, len(str(messages)) // 4)

                max_output = min(int(body.get("max_tokens", config.MAX_OUTPUT_TOKENS)), config.MAX_OUTPUT_TOKENS)
                has_quota = await router.acquire_quota(input_tokens + max_output, model_alias)
                if not has_quota:
                    apply_error_penalty(api_key_val, "rate_limit_rpm_tpm", model_id_val)
                    router.freeze_key(api_key_val, 15, model_id_val, "rate_limit")
                    await asyncio.sleep(_retry_delay(attempt))
                    continue

                kwargs = proxy_instance._prepare_litellm_kwargs(
                    litellm_model_val=litellm_model_val,
                    reinforced_messages=messages,
                    api_key_val=api_key_val,
                    max_output=max_output,
                    body=body,
                    openai_tools=tools,
                    reservation=reservation,
                    is_stream=True,
                )
                logger.info("[Test Standalone Kwargs] %s", {k: v for k, v in kwargs.items() if k != 'api_key'})

                gen = await _execute_stream(
                    proxy_instance, kwargs, api_key_val, model_id_val, model_alias_val,
                    input_tokens, None, body, auth_key_prefix, account=account,
                    is_opencode=is_opencode
                )
                saved_key = api_key_val
                api_key_val = None
                async for chunk in gen:
                    has_real_content = False
                    if b'"content":' in chunk and not b'"content": ""' in chunk and not b'"content": null' in chunk:
                        has_real_content = True
                    if b'"tool_calls"' in chunk:
                        has_real_content = True
                    if has_real_content:
                        committed = True
                    yield chunk
                return

            finally:
                if api_key_val:
                    router.release_key(api_key_val)

        except HTTPException as e:
            if committed:
                yield error_sse({"error": {"type": "api_error", "message": f"Stream error: {e}"}})[0]
                return
            if e.status_code == 503:
                raise
            _err_key = api_key_val or saved_key
            if _err_key:
                router.freeze_key(_err_key, 15, model_id_val, "rate_limit")
                apply_error_penalty(_err_key, "rate_limit", model_id_val)
            router.record_failure("rate_limit")
            await asyncio.sleep(_retry_delay(attempt))

        except asyncio.CancelledError:
            raise
        except Exception as e:
            if committed:
                yield error_sse({"error": {"type": "api_error", "message": f"Stream error: {e}"}})[0]
                return
            error_text = str(e).lower()

            if "400" in error_text and "failed_precondition" not in error_text and ("invalid_argument" in error_text or "bad_request" in error_text):
                if api_key_val:
                    router.freeze_key(api_key_val, 2, model_id_val, "bad_request_spam_prevent")
                yield error_sse({"error": {"type": "invalid_request_error", "message": f"LLM rejected payload (HTTP 400): {error_text[:200]}"}})[0]
                return

            if "400" in error_text and "failed_precondition" in error_text:
                if api_key_val:
                    router.freeze_key(api_key_val, 300, model_id_val, "billing_error")
                    apply_error_penalty(api_key_val, "billing_error", model_id_val)
                router.record_failure("billing_error")
                await asyncio.sleep(_retry_delay(attempt))
                continue

            if "499" in error_text or "cancelled" in error_text:
                return

            is_region_quota = "apirequestsperminuteperprojectperregion" in error_text or "api_requests_per_minute_per_project_per_region" in error_text
            if is_region_quota:
                reason = "rate_limit"
            else:
                from src.api.claude_proxy.handler.helpers import _classify_error_reason as _classify_error_reason_static
                reason = _classify_error_reason_static(error_text, api_key_val, model_id_val)

            if reason == "rate_limit":
                router.record_429()

            _err_key = api_key_val or saved_key
            if _err_key:
                router.freeze_key(_err_key, 0, model_id_val, reason)
                if reason not in ("bad_request_spam_prevent", "invalid_key"):
                    apply_error_penalty(_err_key, reason, model_id_val)
            router.record_failure(reason)

            if is_region_quota:
                logger.warning("[OpenCode Region Quota] Hit region limit, waiting 25s before retry...")
                await asyncio.sleep(25)
            else:
                await asyncio.sleep(_retry_delay(attempt))

    err_resp = proxy_instance._error_response(body, model_alias, "exhausted")
    for chunk in error_sse(err_resp):
        yield chunk

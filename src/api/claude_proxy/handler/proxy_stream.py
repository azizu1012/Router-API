"""Claude stream proxy — format converter only.

Pool/key/custom management is centralized in PoolManager.
"""

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional, AsyncIterator

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.pool_manager import pool_manager
from src.core.usage_logger import log_usage
from src.logical_HQ_translator import (
    _convert_messages,
    _dict_to_sse_events,
    _sse,
    StreamingTextNormalizer,
    XMLThinkingExtractor,
)
from .anthropic_spec import (
    apply_tool_choice_to_tools,
    build_error_event,
    compute_usage,
    estimate_input_tokens,
    extract_sampling_params,
    extract_tool_choice,
    map_stop_reason,
    thinking_signature,
    server_search_limits,
    run_server_tool,
    _server_tool_record,
    _server_tool_use_block,
    _web_search_result_block,
    _search_citations,
)
from .compaction import _pre_compact_and_truncate
from .helpers import get_system_status_summary

# How often to emit a `ping` while waiting on the model, so proxies and clients
# do not time out during long Gemini thinking phases.
KEEPALIVE_INTERVAL = 4.0

# Sentinel yielded by the keepalive wrapper; distinct from any real pool item.
_KEEPALIVE = object()

# How long one pull may take before the wrapper considers the model quiet.
_POLL_TIMEOUT = 1.0


async def _iter_with_keepalive(
    stream: Any, keepalive_interval: float
) -> AsyncIterator[Any]:
    """Yield pool items, emitting a sentinel whenever the model goes quiet.

    Module-level rather than a closure inside _stream_message_impl so it can be
    tested on its own — this is the seam where the stream used to die.

    The subtle part is the timeout. `shield` exists so an in-flight pull survives
    a timeout, which makes the obvious `except: continue` the worst possible next
    line: it starts a *second* `__anext__` on a generator that is still running,
    and asyncio raises

        RuntimeError: anext(): asynchronous generator is already running

    which killed the whole stream. A thinking turn takes longer than one second,
    so the first slow model response triggered it — exactly the case this
    wrapper exists to handle. HTTP stayed 200 and the client got a well-formed
    error event, so nothing upstream noticed.

    So the pending pull is held in `pending` and awaited again on the next pass.
    A ping costs latency; two concurrent pulls on one generator cost the response.
    """
    aiter_ = stream.__aiter__()
    pending: asyncio.Future = asyncio.ensure_future(aiter_.__anext__())
    last_emit = time.monotonic()
    while True:
        try:
            item = await asyncio.wait_for(
                asyncio.shield(pending), timeout=_POLL_TIMEOUT
            )
        except asyncio.TimeoutError:
            if time.monotonic() - last_emit >= keepalive_interval:
                last_emit = time.monotonic()
                yield _KEEPALIVE
            continue
        except StopAsyncIteration:
            return
        yield item
        last_emit = time.monotonic()
        # Only now is the previous pull finished, so the next one is safe.
        pending = asyncio.ensure_future(aiter_.__anext__())


class ClaudeProxyStreamMixin:
    """
    `ClaudeProxyStreamMixin` cung cấp logic để xử lý các yêu cầu hoàn thành chat streaming
    cho API Claude. Mixin này tập trung vào việc chuyển đổi định dạng, chèn công cụ WebSearch
    và xử lý các luồng phản hồi streaming từ `PoolManager`.

    Nó ủy quyền việc gọi API streaming thực tế đến `PoolManager` và sau đó định dạng lại
    các chunk phản hồi từ `PoolManager` thành định dạng SSE (Server-Sent Events) mong muốn
    của client Claude streaming.

    **Các chức năng chính bao gồm:**
    - Chuyển đổi định dạng tin nhắn từ OpenCode sang Claude và ngược lại.
    - Chèn công cụ WebSearch nếu được yêu cầu và không phải là yêu cầu từ sub-agent.
    - Xử lý các yêu cầu "thinking" và nén ngữ cảnh (context compaction).
    - Streaming phản hồi từ mô hình, bao gồm cả việc trích xuất suy nghĩ (thoughts) từ phản hồi XML.
    - Xử lý các cuộc gọi công cụ bị chặn (intercepted tool calls) như WebSearch hoặc WebFetch trong một vòng lặp đệ quy.
    - Tạo các sự kiện SSE cho `message_start`, `content_block_start`, `content_block_delta`,
      `content_block_stop`, `message_delta` và `message_stop`.
    """

    async def stream_message(
        self, body: Dict[str, Any], auth_key_prefix: str = "", account: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[bytes]:
        """
        Xử lý yêu cầu hoàn thành chat streaming cho API Claude.

        Phương thức này thực hiện các bước sau:
        1. Chuyển đổi định dạng tin nhắn từ OpenCode sang định dạng nội bộ của Claude.
        2. Kiểm tra và chèn công cụ WebSearch nếu tìm kiếm web được kích hoạt và yêu cầu không phải từ sub-agent.
        3. Giải quyết bí danh mô hình và thực hiện nén ngữ cảnh (context compaction) nếu cần.
        4. Thiết lập cấu hình "thinking" và các tham số khác cho cuộc gọi API.
        5. Gọi phương thức nội bộ `_stream_message_impl` để xử lý logic streaming chính,
           bao gồm vòng lặp đệ quy cho các cuộc gọi công cụ bị chặn.
        6. Bắt và ghi lại bất kỳ ngoại lệ nào xảy ra trong quá trình streaming.

        Args:
            body (Dict[str, Any]): Body của yêu cầu API gốc.
            auth_key_prefix (str, optional): Tiền tố khóa xác thực. Mặc định là "".
            account (Optional[Dict[str, Any]], optional): Thông tin tài khoản người dùng. Mặc định là None.

        Yields:
            AsyncIterator[bytes]: Một iterator bất đồng bộ của các khối phản hồi streaming theo định dạng SSE.
        """
        openai_messages, openai_tools = _convert_messages(body)

        # Same line as the non-stream path. A stream writes different log lines
        # than a non-stream, so instrumenting only one of them makes "no log
        # entry" mean nothing -- which is exactly the case worth logging.
        logger.info(
            "[Claude Stream] requested model=%r msgs=%d tools=%d",
            body.get("model"), len(body.get("messages") or []),
            len(body.get("tools") or []),
        )

        from src.api.opencode_proxy.handler.websearch import should_enable_web_search
        from src.api.opencode_proxy.handler.proxy import _WEBSEARCH_TOOL_DEF, _resolve_thinking_config, _extract_thinking_params
        from src.core.sub_agent_detect import is_sub_agent_body
        if not is_sub_agent_body(body) and should_enable_web_search(body, account) and not any(
            t.get("function", {}).get("name") in ("WebSearch", "web_search") for t in openai_tools
        ):
            openai_tools.append(_WEBSEARCH_TOOL_DEF)
            logger.info("[WebSearch] Injected WebSearch tool for Claude stream")
            
        from src.core.compaction_detect import strip_tools_for_compaction
        strip_tools_for_compaction(body, openai_messages, openai_tools, is_claude_or_opencode=True)

        model_alias = router.resolve_model_alias(body.get("model", "")) or config.DEFAULT_MODEL_ALIAS
        await _pre_compact_and_truncate(body, openai_messages, openai_tools, model_alias)

        try:
            max_tokens = max(1, min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS))
            temperature = float(body.get("temperature", 0.7))
            thinking_config = _resolve_thinking_config(body, model_alias)
            thinking_params = _extract_thinking_params(body)

            # tool_choice: "none" removes tools entirely; anything else is passed down.
            tool_choice = extract_tool_choice(body)
            if tool_choice:
                openai_tools = apply_tool_choice_to_tools(tool_choice, openai_tools)

            sampling_params = extract_sampling_params(body)

            msg_id = "msg_" + uuid.uuid4().hex[:24]

            async for chunk in self._stream_message_impl(
                body=body,
                openai_messages=openai_messages,
                openai_tools=openai_tools,
                model_alias=model_alias,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking_config=thinking_config,
                thinking_params=thinking_params,
                account=account,
                auth_key_prefix=auth_key_prefix,
                msg_id=msg_id,
                recursion_depth=0,
                start_block_index=0,
                sampling_params=sampling_params,
            ):
                yield chunk
        except Exception as e:
            logger.error("[Claude Stream] stream_message failed: %s", e, exc_info=True)
            raise e

    def _buffer_tool_calls(self, delta, tool_buffers: Dict[int, Dict[str, Any]]) -> None:
        """Accumulate streamed tool calls into tool_buffers, keyed by index.

        Providers stream a tool call as one part carrying the name and id, then one
        part per argument fragment carrying neither. Both shapes arrive in the wild —
        some endpoints return plain dicts, some return objects — so both are handled
        here rather than normalised upstream, which would mean rewriting what the pool
        yields mid-stream.

        Keyed by index because the arguments come after the name: the second fragment
        for index 0 has to land in the buffer the first fragment created.
        """
        tool_calls_val = getattr(delta, "tool_calls", None)
        if tool_calls_val is None and hasattr(delta, "get"):
            tool_calls_val = delta.get("tool_calls")
        if not tool_calls_val:
            return
        for tc in tool_calls_val:
            if isinstance(tc, dict):
                tc_idx = tc.get("index", 0)
                fn = tc.get("function", {})
                fn_name = fn.get("name", "") if isinstance(fn, dict) else (getattr(fn, "name", "") if hasattr(fn, "name") else "")
                if tc_idx not in tool_buffers:
                    tc_id = tc.get("id", f"toolu_{fn_name}_{uuid.uuid4().hex[:12]}" if fn_name else f"toolu_{uuid.uuid4().hex}")
                    fn_args = fn.get("arguments") if isinstance(fn, dict) else (getattr(fn, "arguments", None) if hasattr(fn, "arguments") else None)
                    tool_buffers[tc_idx] = {"id": tc_id, "name": fn_name, "args": ""}
                    if fn_args:
                        if isinstance(fn_args, dict):
                            args_str = json.dumps(fn_args)
                        elif not isinstance(fn_args, str):
                            args_str = str(fn_args)
                        else:
                            args_str = fn_args
                        tool_buffers[tc_idx]["args"] += args_str
                else:
                    if fn_name:
                        tool_buffers[tc_idx]["name"] = fn_name
                    fn_args = fn.get("arguments") if isinstance(fn, dict) else (getattr(fn, "arguments", None) if hasattr(fn, "arguments") else None)
                    if fn_args:
                        if isinstance(fn_args, dict):
                            args_str = json.dumps(fn_args)
                        elif not isinstance(fn_args, str):
                            args_str = str(fn_args)
                        else:
                            args_str = fn_args
                        tool_buffers[tc_idx]["args"] += args_str
            else:
                tc_idx = getattr(tc, "index", 0)
                fn = getattr(tc, "function", {}) if hasattr(tc, "function") else {}
                fn_name = getattr(fn, "name", "") if hasattr(fn, "name") else ""
                if tc_idx not in tool_buffers:
                    tc_id = getattr(tc, "id", f"toolu_{fn_name}_{uuid.uuid4().hex[:12]}" if fn_name else f"toolu_{uuid.uuid4().hex}")
                    fn_args = getattr(fn, "arguments", None) if hasattr(fn, "arguments") else None
                    tool_buffers[tc_idx] = {"id": tc_id, "name": fn_name, "args": ""}
                    if fn_args:
                        if isinstance(fn_args, dict):
                            args_str = json.dumps(fn_args)
                        elif not isinstance(fn_args, str):
                            args_str = str(fn_args)
                        else:
                            args_str = fn_args
                        tool_buffers[tc_idx]["args"] += args_str
                else:
                    if fn_name:
                        tool_buffers[tc_idx]["name"] = fn_name
                    fn_args = getattr(fn, "arguments", None) if hasattr(fn, "arguments") else None
                    if fn_args:
                        if isinstance(fn_args, dict):
                            args_str = json.dumps(fn_args)
                        elif not isinstance(fn_args, str):
                            args_str = str(fn_args)
                        else:
                            args_str = fn_args
                        tool_buffers[tc_idx]["args"] += args_str

    async def _stream_message_impl(
        self,
        body: Dict[str, Any],
        openai_messages: List[Dict[str, Any]],
        openai_tools: Optional[List[Dict[str, Any]]],
        model_alias: str,
        temperature: float,
        max_tokens: int,
        thinking_config: Optional[Dict[str, Any]],
        thinking_params: Dict[str, Any],
        account: Optional[Dict[str, Any]],
        auth_key_prefix: str,
        msg_id: str,
        recursion_depth: int,
        start_block_index: int,
        sampling_params: Optional[Dict[str, Any]] = None,
        emit_message_start: bool = True,
        search_limits: Optional[Dict[str, Any]] = None,
        server_tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[bytes]:
        # Shared across the recursion so `max_uses` counts every search in the
        # turn, not one per level, and so the final usage reports the total.
        if search_limits is None:
            search_limits = server_search_limits(body)
        if server_tool_calls is None:
            server_tool_calls = []
        try:
            text_started = False
            thinking_started = False
            thinking_stopped = False
            include_thoughts = thinking_params.get("include_thoughts", True)
            text_index = start_block_index
            thinking_index = start_block_index
            output_chars = 0
            input_tokens = 0
            finish_reason = "end_turn"
            tool_buffers: Dict[int, Dict[str, Any]] = {}
            accumulated_text = []
            accumulated_thought = []
            accumulated_thought_signature = []
            extractor = XMLThinkingExtractor()
            normalizer = StreamingTextNormalizer()
            used_api_key = ""
            used_model_id = ""

            started = False

            stop_sequences = (sampling_params or {}).get("stop_sequences")

            # NEW: Check for custom endpoint passthrough BEFORE pool logic
            from src.core.providers.custom_endpoint_passthrough import (
                resolve_custom_endpoint_for_request,
                call_custom_endpoint_passthrough,
            )

            # Only resolve custom endpoint on first call (not recursion)
            custom_ep = None
            if recursion_depth == 0:
                custom_ep = await resolve_custom_endpoint_for_request(
                    account=account,
                    account_key_id=account.get("token_key_id") or account.get("key_id") if account else None,
                    requested_model=body.get("model", ""),
                    alias_info=body.get("_alias_info"),
                )

            if custom_ep:
                # Custom endpoint: pure passthrough stream, no pool, no retry
                logger.info("[Claude Stream] Using custom endpoint passthrough: %s", custom_ep.get("name"))
                try:
                    tool_choice = extract_tool_choice(body)

                    _, stream_gen = await call_custom_endpoint_passthrough(
                        endpoint=custom_ep,
                        model=body.get("model", ""),
                        messages=openai_messages,
                        stream=True,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=openai_tools or None,
                        extra_body=sampling_params,
                        tool_choice=tool_choice,
                    )

                    # Wrap custom endpoint stream to match pool_manager format
                    async def custom_stream_wrapper():
                        if stream_gen is not None:
                            async for chunk in stream_gen:
                                # Pool manager yields dicts with {chunk, api_key, model_id}
                                yield {
                                    "chunk": chunk,
                                    "api_key": custom_ep["auth_key"],
                                    "model_id": body.get("model", ""),
                                    "input_tokens": 0,  # Custom endpoints don't report this
                                }

                    stream = custom_stream_wrapper()
                except RuntimeError as e:
                    # Custom endpoint errors are NOT retried - return error event immediately
                    logger.error("[Claude Stream] Custom endpoint error: %s", e)
                    error_event = build_error_event(e, "api_error")
                    yield _sse("error", error_event)
                    return
            else:
                # Standard pool logic for Gemini keys
                stream = pool_manager.call_stream(
                    model_alias=model_alias,
                    messages=openai_messages,
                    tools=openai_tools or None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    thinking_config=thinking_config,
                    account=account,
                    thinking_params=thinking_params,
                    sampling_params=sampling_params,
                )

            # message_start is emitted before the pool is even pulled, so it is
            # unconditionally the first event. Anthropic documents the order as
            # message_start -> content blocks -> message_delta -> message_stop
            # with ping "dispersed throughout", and its own SDK accumulator
            # raises `Unexpected event order, got <event> before
            # "message_start"` for anything else arriving first. The SDK hides
            # that by filtering on the SSE event name, so a client reading
            # `type` off the payload is the one that breaks.
            #
            # It cannot live inside the loop below: the keepalive fires whenever
            # the model goes quiet, which includes before its first chunk.
            #
            # Recursion re-enters this method to run a tool result, and that
            # continuation is part of the same message — a second
            # message_start would restart the client's event state mid-stream.
            if emit_message_start:
                client_input_tokens = estimate_input_tokens(body)
                client_usage = compute_usage(body, client_input_tokens, 0)
                yield _sse("message_start", {
                    "type": "message_start",
                    "message": {
                        "id": msg_id,
                        "type": "message",
                        "role": "assistant",
                        "model": body.get("_original_model_name") or body.get("model") or model_alias,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": client_usage,
                    },
                })
                yield _sse("ping", {"type": "ping"})

            # Wrap the pool iterator so a `ping` is emitted whenever the model is
            # slow, not just once at the start. Without this, long Gemini thinking
            # phases leave the connection silent and intermediaries drop it.
            async for item in _iter_with_keepalive(stream, KEEPALIVE_INTERVAL):
                if item is _KEEPALIVE:
                    # Payload matches the docs exactly: {"type": "ping"}. The
                    # retry/reason fields this used to carry are not in the spec,
                    # and a client that switches on `type` would be reading them
                    # as part of an event it does not recognise.
                    yield _sse("ping", {"type": "ping"})
                    continue

                if not isinstance(item, dict):
                    # Defensive: PoolManager yields dicts, but a malformed item must
                    # not be read with .get() and blow up mid-stream.
                    logger.warning("[Claude Stream] unexpected stream item type=%s, skipping",
                                   type(item).__name__)
                    continue

                input_tokens = item.get("input_tokens", input_tokens)
                used_api_key = item.get("api_key", used_api_key)
                used_model_id = item.get("model_id", used_model_id)
                if not started:
                    started = True
                    if recursion_depth == 0:
                        # Warn before the model starts when the context is near the
                        # TPM ceiling, so the user can /compact instead of hitting 429.
                        from src.core.sub_agent_detect import is_sub_agent_body
                        from src.logical_HQ_translator.sse_cache_agent import is_claude_code_body
                        if input_tokens > (178000 if is_claude_code_body(body) else 170000) \
                                and not is_sub_agent_body(body):
                            ctx_warn = (
                                "\n⚠️  [ROUTER-API WARNING] Context is extremely large (%.1fk tokens). "
                                "Please run '/compact' in your terminal immediately to avoid 250k TPM rate limits! ⚠️\n"
                                "⚠️  [CẢNH BÁO] Context hiện tại cực kỳ lớn (%.1fk tokens). "
                                "Vui lòng chạy lệnh '/compact' ngay lập tức để tránh bị lỗi giới hạn 250k TPM! ⚠️\n\n"
                            ) % (input_tokens / 1000.0, input_tokens / 1000.0)
                            text_index = start_block_index
                            text_started = True
                            yield _sse("content_block_start", {
                                "type": "content_block_start",
                                "index": text_index,
                                "content_block": {"type": "text", "text": ""},
                            })
                            yield _sse("content_block_delta", {
                                "type": "content_block_delta",
                                "index": text_index,
                                "delta": {"type": "text_delta", "text": ctx_warn},
                            })
                            accumulated_text.append(ctx_warn)
                            output_chars += len(ctx_warn)

                chunk = item["chunk"]
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                fr = chunk.choices[0].finish_reason
                content = getattr(delta, "content", None)
                reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "thought", None)
                tsig = getattr(delta, "thought_signature", None) or (delta.get("thought_signature") if hasattr(delta, "get") else None)
                if tsig:
                    accumulated_thought_signature.append(tsig)

                if reasoning and include_thoughts:
                    accumulated_thought.append(reasoning)
                    if not thinking_started:
                        thinking_started = True
                        thinking_index = start_block_index + 1 if text_started else start_block_index
                        yield _sse("content_block_start", {
                            "type": "content_block_start",
                            "index": thinking_index,
                            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                        })
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": thinking_index,
                        "delta": {"type": "thinking_delta", "thinking": reasoning},
                    })
                    output_chars += len(reasoning)

                if content:
                    events = extractor.feed(content)
                    for ev_type, ev_val in events:
                        if ev_type in ("start_thinking", "thinking", "end_thinking") and not include_thoughts:
                            continue
                        if ev_type == "start_thinking":
                            if not thinking_started:
                                thinking_started = True
                                thinking_index = start_block_index + 1 if text_started else start_block_index
                                yield _sse("content_block_start", {
                                    "type": "content_block_start",
                                    "index": thinking_index,
                                    "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                                })
                        elif ev_type == "thinking":
                            accumulated_thought.append(ev_val)
                            if not thinking_started:
                                thinking_started = True
                                thinking_index = start_block_index + 1 if text_started else start_block_index
                                yield _sse("content_block_start", {
                                    "type": "content_block_start",
                                    "index": thinking_index,
                                    "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                                })
                            if not thinking_stopped:
                                yield _sse("content_block_delta", {
                                    "type": "content_block_delta",
                                    "index": thinking_index,
                                    "delta": {"type": "thinking_delta", "thinking": ev_val},
                                })
                            output_chars += len(ev_val)
                        elif ev_type == "end_thinking":
                            if thinking_started and not thinking_stopped:
                                thinking_stopped = True
                                yield _sse("content_block_delta", {
                                    "type": "content_block_delta",
                                    "index": thinking_index,
                                    "delta": {"type": "signature_delta", "signature": thinking_signature("".join(accumulated_thought))},
                                })
                                yield _sse("content_block_stop", {"type": "content_block_stop", "index": thinking_index})
                        elif ev_type == "text":
                            accumulated_text.append(ev_val)
                            if not text_started:
                                text_started = True
                                text_index = start_block_index + 1 if thinking_started else start_block_index
                                if thinking_started and not thinking_stopped:
                                    thinking_stopped = True
                                    yield _sse("content_block_delta", {
                                        "type": "content_block_delta",
                                        "index": thinking_index,
                                        "delta": {"type": "signature_delta", "signature": thinking_signature("".join(accumulated_thought))},
                                    })
                                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": thinking_index})
                                yield _sse("content_block_start", {
                                    "type": "content_block_start",
                                    "index": text_index,
                                    "content_block": {"type": "text", "text": ""},
                                })
                            # Normalize LaTeX/arrow escapes, but only outside code
                            # fences — StreamingTextNormalizer buffers partial
                            # tokens so a chunk boundary cannot split an escape.
                            norm_val = normalizer.feed(ev_val)
                            if norm_val:
                                yield _sse("content_block_delta", {
                                    "type": "content_block_delta",
                                    "index": text_index,
                                    "delta": {"type": "text_delta", "text": norm_val},
                                })
                                output_chars += len(norm_val)

                self._buffer_tool_calls(delta, tool_buffers)
                if fr:
                    # Keep the provider's raw reason here. Mapping it to the
                    # Anthropic enum now loses information — "length" contains no
                    # "max" substring, so it silently became end_turn.
                    finish_reason = str(fr)

            # Flush extractor to handle any remaining tags or text
            events = extractor.flush()
            for ev_type, ev_val in events:
                if ev_type in ("thinking", "start_thinking", "end_thinking") and not include_thoughts:
                    continue
                if ev_type == "thinking":
                    accumulated_thought.append(ev_val)
                    if not thinking_started:
                        thinking_started = True
                        thinking_index = start_block_index + 1 if text_started else start_block_index
                        yield _sse("content_block_start", {
                            "type": "content_block_start",
                            "index": thinking_index,
                            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                        })
                    if not thinking_stopped:
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta",
                            "index": thinking_index,
                            "delta": {"type": "thinking_delta", "thinking": ev_val},
                        })
                    output_chars += len(ev_val)
                elif ev_type == "text":
                    accumulated_text.append(ev_val)
                    if not text_started:
                        text_started = True
                        text_index = start_block_index + 1 if thinking_started else start_block_index
                        if thinking_started and not thinking_stopped:
                            thinking_stopped = True
                            yield _sse("content_block_delta", {
                                "type": "content_block_delta",
                                "index": thinking_index,
                                "delta": {"type": "signature_delta", "signature": thinking_signature("".join(accumulated_thought))},
                            })
                            yield _sse("content_block_stop", {"type": "content_block_stop", "index": thinking_index})
                        yield _sse("content_block_start", {
                            "type": "content_block_start",
                            "index": text_index,
                            "content_block": {"type": "text", "text": ""},
                        })
                    norm_val = normalizer.feed(ev_val)
                    if norm_val:
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta",
                            "index": text_index,
                            "delta": {"type": "text_delta", "text": norm_val},
                        })
                        output_chars += len(norm_val)

            # Flush the normalizer so any trailing partial escape is not dropped.
            tail = normalizer.flush()
            if tail and text_started:
                yield _sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": text_index,
                    "delta": {"type": "text_delta", "text": tail},
                })
                accumulated_text.append(tail)
                output_chars += len(tail)

            if thinking_started and not thinking_stopped:
                thinking_stopped = True
                yield _sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": thinking_index,
                    "delta": {"type": "signature_delta", "signature": thinking_signature("".join(accumulated_thought))},
                })
                yield _sse("content_block_stop", {"type": "content_block_stop", "index": thinking_index})
            if text_started:
                # Citations ride on the text that used them, arriving as
                # `citations_delta` inside the block. Only the deepest turn has
                # them — the searches are recorded on the shared list, so by the
                # time the answer is closed they are all known.
                for cite in _search_citations(server_tool_calls):
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta", "index": text_index,
                        "delta": {"type": "citations_delta", "citation": cite},
                    })
                yield _sse("content_block_stop", {"type": "content_block_stop", "index": text_index})

            next_block_idx = start_block_index + (1 if thinking_started else 0) + (1 if text_started else 0)

            intercepted_tool_idx = None
            if recursion_depth < 5:
                for idx in sorted(tool_buffers.keys()):
                    buf = tool_buffers[idx]
                    name = buf["name"]
                    if name in ("web_search", "WebSearch", "web_fetch", "WebFetch"):
                        intercepted_tool_idx = idx
                        break

            if intercepted_tool_idx is not None:
                buf = tool_buffers[intercepted_tool_idx]
                name = buf["name"]
                tc_id = buf["id"]
                args_str = buf["args"]
                try:
                    args = json.loads(args_str) if args_str else {}
                except Exception:
                    args = {}

                tool_result, search_citations, search_error = await run_server_tool(
                    name, args, body=body, account=account,
                    auth_key_prefix=auth_key_prefix, limits=search_limits,
                    used=len(server_tool_calls))

                record = _server_tool_record(name, tc_id, args, tool_result,
                                             search_citations, search_error)
                server_tool_calls.append(record)

                # The search happened on this side of the wire, so the client
                # has to be told about it in Anthropic's hosted-tool shape.
                # Streaming these as ordinary content blocks is what the spec
                # describes; without them the streamed message silently dropped
                # every search and its sources.
                yield _sse("content_block_start", {
                    "type": "content_block_start", "index": next_block_idx,
                    "content_block": _server_tool_use_block(record),
                })
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": next_block_idx,
                    "delta": {"type": "input_json_delta",
                              "partial_json": json.dumps(args)},
                })
                yield _sse("content_block_stop", {
                    "type": "content_block_stop", "index": next_block_idx})
                next_block_idx += 1
                yield _sse("content_block_start", {
                    "type": "content_block_start", "index": next_block_idx,
                    "content_block": _web_search_result_block(record),
                })
                yield _sse("content_block_stop", {
                    "type": "content_block_stop", "index": next_block_idx})
                next_block_idx += 1

                thought = "".join(accumulated_thought)
                tsig_str = "".join(accumulated_thought_signature)
                text = "".join(accumulated_text)
                ast_text = text
                if thought:
                    ast_text = f"<thinking>\n{thought}\n</thinking>\n{ast_text}" if ast_text else f"<thinking>\n{thought}\n</thinking>"

                assistant_msg = {
                    "role": "assistant",
                    "content": ast_text or None,
                    "reasoning_content": thought or None,
                    "thought_signature": tsig_str or None,
                    "tool_calls": [
                        {
                            "id": tc_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(args)
                            }
                        }
                    ]
                }
                tool_result_msg = {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": name,
                    "content": tool_result
                }

                new_messages = list(openai_messages)
                new_messages.extend([assistant_msg, tool_result_msg])

                async for chunk in self._stream_message_impl(
                    body=body,
                    openai_messages=new_messages,
                    openai_tools=openai_tools,
                    model_alias=model_alias,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    thinking_config=thinking_config,
                    thinking_params=thinking_params,
                    account=account,
                    auth_key_prefix=auth_key_prefix,
                    msg_id=msg_id,
                    recursion_depth=recursion_depth + 1,
                    start_block_index=next_block_idx,
                    sampling_params=sampling_params,
                    emit_message_start=False,
                    search_limits=search_limits,
                    server_tool_calls=server_tool_calls,
                ):
                    yield chunk

            else:
                if tool_buffers:
                    logger.info("[ToolCallEmit] Emitting %d tool buffer(s) for model=%s depth=%d",
                                len(tool_buffers), model_alias, recursion_depth)
                    for tc_idx in sorted(tool_buffers.keys()):
                        buf = tool_buffers[tc_idx]
                        name = buf["name"]
                        logger.info("[ToolCallEmit]   idx=%d id=%s name=%s args_len=%d args_preview=%s",
                                    tc_idx, buf["id"], name, len(buf["args"]), buf["args"][:120])
                        if name in ("Agent", "Task"):
                            try:
                                parsed_args = json.loads(buf["args"]) if buf["args"] else {}
                                prompt_str = parsed_args.get("prompt", "") or buf["args"]
                            except Exception:
                                prompt_str = buf["args"]
                            yield _sse("content_block_start", {
                                "type": "content_block_start", "index": next_block_idx,
                                "content_block": {
                                    "type": "agent_use", "id": buf["id"],
                                    "agent_type": "general-purpose", "prompt": prompt_str,
                                },
                            })
                            yield _sse("content_block_stop", {"type": "content_block_stop", "index": next_block_idx})
                        else:
                            # A search is not a client tool call. When the
                            # recursion is exhausted the buffer still holds one,
                            # and emitting it as `tool_use` told the client the
                            # router had handed it a tool to answer — for a search
                            # the router had already run, or that was refused.
                            if name in ("web_search", "WebSearch", "web_fetch",
                                        "WebFetch"):
                                logger.info(
                                    "[Claude Stream] dropping unhandled %s at "
                                    "recursion limit depth=%d", name,
                                    recursion_depth)
                                continue
                            yield _sse("content_block_start", {
                                "type": "content_block_start", "index": next_block_idx,
                                "content_block": {"type": "tool_use", "id": buf["id"], "name": name, "input": {}},
                            })
                            if buf["args"]:
                                yield _sse("content_block_delta", {
                                    "type": "content_block_delta", "index": next_block_idx,
                                    "delta": {"type": "input_json_delta", "partial_json": buf["args"]},
                                })
                            yield _sse("content_block_stop", {"type": "content_block_stop", "index": next_block_idx})
                        next_block_idx += 1

                output_tokens = max(1, output_chars // 4) + len(tool_buffers) * 50
                final_stop_reason, final_stop_sequence = map_stop_reason(
                    finish_reason if finish_reason != "tool_use" else "tool_calls",
                    has_tool_calls=bool(tool_buffers),
                    stop_sequences=stop_sequences,
                    emitted_text="".join(accumulated_text),
                )
                client_input_tokens = estimate_input_tokens(body)
                final_usage = compute_usage(body, client_input_tokens, output_tokens)
                # Same counter the non-streaming path reports, so a client
                # reconciling its own spend sees one number either way.
                ran_search = sum(1 for r in server_tool_calls
                                 if r["name"] == "web_search" and not r["failed"])
                ran_fetch = sum(1 for r in server_tool_calls
                                if r["name"] == "web_fetch" and not r["failed"])
                if ran_search or ran_fetch:
                    final_usage["server_tool_use"] = {
                        "web_search_requests": ran_search,
                        "web_fetch_requests": ran_fetch}

                # Persist usage so Claude Code traffic shows up in the dashboard.
                try:
                    await log_usage(
                        used_model_id or model_alias,
                        (used_api_key or "")[-8:],
                        final_usage["input_tokens"],
                        final_usage["output_tokens"],
                        auth_key_prefix,
                        final_usage["cache_creation_input_tokens"],
                        final_usage["cache_read_input_tokens"],
                    )
                except Exception as e:
                    logger.warning("[Claude Stream] log_usage failed: %s", e)

                # message_delta carries cumulative usage, so input_tokens must be
                # repeated here — Anthropic clients read the final totals from it.
                yield _sse("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": final_stop_reason, "stop_sequence": final_stop_sequence},
                    "usage": final_usage,
                })
                yield _sse("message_stop", {"type": "message_stop"})

        except Exception as e:
            logger.error("[Claude Stream Recurse] PoolManager failed: %s", e, exc_info=True)
            if recursion_depth == 0:
                from src.core.sub_agent_detect import is_sub_agent_body
                if is_sub_agent_body(body):
                    from .helpers import _classify_error_reason
                    reason = _classify_error_reason(str(e))
                    summary_text = get_system_status_summary(model_alias, reason, str(e))
                    fake_result = {
                        "id": "msg_err_" + uuid.uuid4().hex[:8],
                        "type": "message",
                        "role": "assistant",
                        "model": body.get("model") or model_alias,
                        "content": [{"type": "text", "text": summary_text}],
                        "stop_reason": "end_turn",
                        "stop_sequence": None,
                        "usage": {"input_tokens": len(summary_text) // 4, "output_tokens": len(summary_text) // 4},
                    }
                    for chunk in _dict_to_sse_events(fake_result):
                        yield chunk
                else:
                    # Headers are already committed at this point, so a bare raise
                    # leaves the client with a truncated stream. Emit a proper
                    # Anthropic `error` event instead, then close the message.
                    logger.error("[Claude Stream] emitting SSE error event: %s", e)
                    yield _sse("error", build_error_event(e, "api_error"))
                    yield _sse("message_stop", {"type": "message_stop"})
            else:
                raise e

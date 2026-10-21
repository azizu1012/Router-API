"""Claude stream proxy — format converter only.

Pool/key/custom management is centralized in PoolManager.
"""

import json
import uuid
from typing import Any, Dict, List, Optional, AsyncIterator

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.orchestration.pool_manager import pool_manager
from src.core.observability.usage_logger import log_usage
from src.core.runtime.tool_name_match import declared_tool_names, resolve_declared_tool_name
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
    _search_budget_left,
    _MAX_SMART_BUDGET_CAP,
    is_redundant_query,
    _server_tool_record,
    _server_tool_use_block,
    _web_search_result_block,
    _search_citations,
    router_hosted_tools_enabled,
    SEARCH_SYNTHESIS_FALLBACK,
    is_raw_search_context,
)
from .compaction import _pre_compact_and_truncate
from .streaming import (
    KEEPALIVE_INTERVAL,
    _KEEPALIVE,
    buffer_tool_calls,
    iter_with_keepalive,
)


def _normalise_for_compare(text: str) -> str:
    """Whitespace-insensitive form used to check a retry reproduced the turn."""
    return " ".join(str(text or "").split())


async def _recover_tool_call(
    make_stream, budget: int, expected_text: str,
) -> Optional[Dict[int, Dict[str, Any]]]:
    """Re-issue a turn purely to recover a tool call that max_tokens cut off.

    Anthropic prescribes retrying a truncated tool use at a higher max_tokens,
    and a stream cannot be resumed token-perfect, so the obvious retry replays
    text the client already has. It does not have to. The client keeps receiving
    the first attempt's text exactly as before — this call is not streamed to it
    at all — and only the tool call is taken from the retry.

    Taking it is conditional. The recovered turn's text is compared with the text
    already delivered, and the tool call is accepted only if the two agree.
    Without that check a retry that diverged would attach a tool call to
    reasoning the client never saw, which is worse than losing the turn.

    Returns the tool buffers, or None when the retry did not reproduce the turn
    or produced no complete call.
    """
    buffers: Dict[int, Dict[str, Any]] = {}
    index_map: Dict[int, int] = {}
    text_parts: List[str] = []
    try:
        stream = make_stream(budget)
        async for item in stream:
            chunk = item.get("chunk") if isinstance(item, dict) else item
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if isinstance(content, str) and content:
                text_parts.append(content)
            buffer_tool_calls(delta, buffers, index_map)
    except Exception as exc:
        logger.warning("[Claude Stream] tool call recovery failed: %s", exc)
        return None

    if _normalise_for_compare("".join(text_parts)) != _normalise_for_compare(expected_text):
        logger.warning(
            "[Claude Stream] tool call recovery discarded: the retried turn did "
            "not reproduce the text already sent to the client")
        return None

    for buf in buffers.values():
        raw = buf.get("args") or ""
        if raw and not raw.strip().startswith("{"):
            continue
        if raw and raw.strip():
            try:
                json.loads(raw)
            except Exception:
                return None
    return buffers or None


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
        # Emit the Anthropic prelude before conversion and provider setup. A
        # slow conversion/lookup otherwise leaves Claude showing only its
        # generic working state with no progress event.
        msg_id = "msg_" + uuid.uuid4().hex[:24]
        yield _sse("message_start", {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": body.get("model", ""),
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": compute_usage(body, estimate_input_tokens(body), 0),
            },
        })
        yield _sse("ping", {"type": "ping"})

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
        from src.core.runtime.sub_agent_detect import is_sub_agent_body
        if router_hosted_tools_enabled() and not is_sub_agent_body(body) and should_enable_web_search(body, account) and not any(
            t.get("function", {}).get("name") in ("WebSearch", "web_search") for t in openai_tools
        ):
            openai_tools.append(_WEBSEARCH_TOOL_DEF)
            logger.info("[WebSearch] Injected WebSearch tool for Claude stream")
            
        from src.core.runtime.compaction_detect import strip_tools_for_compaction
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

            # No retry here, and that is a deliberate reading of the guidance
            # rather than an omission. Anthropic prescribes retrying a tool use
            # that max_tokens cut off, and the streaming guidance agrees a partial
            # is worth nothing for anything a machine will consume. It also says
            # a stream cannot be resumed token-perfect, so any retry re-emits
            # what the client already holds once a block is open. The only fully
            # safe form is "commit on completion only" - buffer the entire
            # response until the terminal event - which is exactly the streaming
            # latency this path exists to avoid.
            #
            # So the non-destructive half is done instead: a call that cannot be
            # parsed is never handed to the client, and the turn ends as
            # max_tokens, which is the signal a caller needs in order to ask
            # again at a higher ceiling. The caller retries; the gateway does not
            # double-stream on its behalf.
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
                emit_message_start=False,
            ):
                yield chunk
        except Exception as e:
            logger.error("[Claude Stream] stream_message failed: %s", e, exc_info=True)
            raise e

    async def _stream_message_impl(
        self,
        body: Dict[str, Any],        openai_messages: List[Dict[str, Any]],
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
        synthesis_retry: bool = False,
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
            # Gemini reports real token counts on the final chunk's `usage`.
            # Counting emitted characters instead under-reports every turn
            # whose text never reached the client (thoughts stripped, tool
            # turns, SAFETY stops) — an 8k-token reply logged as 1.
            provider_output_tokens = 0
            finish_reason = "end_turn"
            tool_buffers: Dict[int, Dict[str, Any]] = {}
            tool_index_map: Dict[int, int] = {}
            accumulated_text = []
            accumulated_thought = []
            accumulated_thought_signature = []
            # Once a hosted search has run, buffer the next model text until
            # the turn ends. This prevents a provider from streaming its raw
            # `[Web Search Results ...]` payload to the client before it can be
            # identified and discarded.
            deferred_search_text: List[str] = []
            buffering_search_echo = bool(server_tool_calls)
            extractor = XMLThinkingExtractor()
            normalizer = StreamingTextNormalizer()
            used_api_key = ""
            used_model_id = ""

            started = False

            stop_sequences = (sampling_params or {}).get("stop_sequences")

            # NEW: Check for custom endpoint passthrough BEFORE pool logic
            from src.core.providers.custom_endpoint.passthrough import (
                resolve_custom_endpoint_for_request,
                call_custom_endpoint_passthrough,
            )

            # ponytail: resolve custom endpoint at all recursion depths; tool turns must stay on the same endpoint
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
            async for item in iter_with_keepalive(stream, KEEPALIVE_INTERVAL):
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
                    if recursion_depth == 0 and input_tokens > 170000:
                        # Chat clients cannot run Claude Code's /compact command.
                        # Automatic compaction already happened before the provider
                        # call, so keep this as an internal diagnostic only.
                        logger.info(
                            "[Claude Stream] large context accepted after automatic compaction: %.1fk tokens",
                            input_tokens / 1000.0,
                        )

                chunk = item["chunk"]
                # Read usage before the choices guard: Gemini's final chunk can
                # carry usage with no delta alongside it.
                usage = getattr(chunk, "usage", None)
                if isinstance(usage, dict) and usage.get("completion_tokens"):
                    provider_output_tokens = int(usage["completion_tokens"])
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
                            if buffering_search_echo:
                                deferred_search_text.append(ev_val)
                                joined_prefix = "".join(deferred_search_text).lstrip()
                                raw_prefix = "[Web Search Results"
                                if len(joined_prefix) > 0 and not raw_prefix.startswith(joined_prefix[:len(raw_prefix)]):
                                    buffering_search_echo = False
                                    for buffered_chunk in deferred_search_text:
                                        accumulated_text.append(buffered_chunk)
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
                                        norm_val = normalizer.feed(buffered_chunk)
                                        if norm_val:
                                            yield _sse("content_block_delta", {
                                                "type": "content_block_delta",
                                                "index": text_index,
                                                "delta": {"type": "text_delta", "text": norm_val},
                                            })
                                            output_chars += len(norm_val)
                                    deferred_search_text.clear()
                                continue
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

                buffer_tool_calls(delta, tool_buffers, tool_index_map)
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
                    if buffering_search_echo:
                        deferred_search_text.append(ev_val)
                        continue
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

            if server_tool_calls and deferred_search_text:
                candidate = "".join(deferred_search_text)
                if is_raw_search_context(candidate):
                    logger.warning("[Claude Stream] Provider echoed raw search context")
                    deferred_search_text.clear()
                else:
                    buffered_normalizer = StreamingTextNormalizer()
                    normalized = buffered_normalizer.feed(candidate) + buffered_normalizer.flush()
                    if normalized:
                        if not text_started:
                            text_started = True
                            text_index = start_block_index + 1 if thinking_started else start_block_index
                            yield _sse("content_block_start", {
                                "type": "content_block_start",
                                "index": text_index,
                                "content_block": {"type": "text", "text": ""},
                            })
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta",
                            "index": text_index,
                            "delta": {"type": "text_delta", "text": normalized},
                        })
                        accumulated_text.append(normalized)
                        output_chars += len(normalized)
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
            # Assigned here rather than inside the branch that emits client tool
            # blocks: the intercepted path recurses and then falls through to the
            # stop-reason code that reads these, so declaring them in the branch
            # left that path reading names it never assigned.
            emitted_tool_blocks = 0
            dropped_incomplete_tool = False
            if router_hosted_tools_enabled() and recursion_depth < 5:
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

                # Anthropic emits the server tool-use block before doing the
                # network work. This lets Claude show "Searching" immediately
                # instead of staying silent until the results are ready.
                pending_record = _server_tool_record(name, tc_id, args, "")
                yield _sse("content_block_start", {
                    "type": "content_block_start", "index": next_block_idx,
                    "content_block": _server_tool_use_block(pending_record),
                })
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": next_block_idx,
                    "delta": {"type": "input_json_delta",
                              "partial_json": json.dumps(args)},
                })
                yield _sse("content_block_stop", {
                    "type": "content_block_stop", "index": next_block_idx})
                next_block_idx += 1

                tool_result, search_citations, search_error = await run_server_tool(
                    name, args, body=body, account=account,
                    auth_key_prefix=auth_key_prefix, limits=search_limits,
                    used=len(server_tool_calls))

                record = _server_tool_record(name, tc_id, args, tool_result,
                                             search_citations, search_error)
                server_tool_calls.append(record)
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

                # Smart Budgeting & Loop Prevention:
                # Allow multi-hop searching up to the budget cap (default 3), but filter out
                # web_search when the budget is exhausted or if redundant query is detected,
                # forcing the model to synthesize the final answer.
                recurse_tools = openai_tools
                if recurse_tools:
                    remaining_budget = _search_budget_left(search_limits, len(server_tool_calls), cap_override=_MAX_SMART_BUDGET_CAP)
                    past_queries = [
                        (r.get("args") or {}).get("query", "")
                        for r in server_tool_calls
                        if r.get("name") in ("web_search", "WebSearch")
                    ]
                    # If this last search was redundant or budget is now exhausted,
                    # force a plain synthesis turn. Leaving unrelated tools in the
                    # request lets the model emit another tool call instead of
                    # producing the final answer, which triggers the raw-search
                    # fallback at the recursion limit.
                    is_redundant = is_redundant_query(args.get("query", ""), past_queries[:-1]) if len(past_queries) > 1 else False
                    if not remaining_budget or is_redundant:
                        recurse_tools = None

                # A hosted search result is appended after the initial request
                # compaction. Re-apply the same chat budget before the recursive
                # synthesis turn so multiple searches cannot grow the payload
                # past the provider's TPM ceiling.
                await _pre_compact_and_truncate(
                    body, new_messages, recurse_tools or [], model_alias
                )

                # The first provider stream is wrapped with keepalive above,
                # but this recursive synthesis turn starts after the search
                # result and can also take a long time. Keep the client
                # connection alive while the model consumes the tool result.
                logger.info(
                    "[Claude Stream] Recurse after server tool name=%s depth=%d messages=%d",
                    name, recursion_depth + 1, len(new_messages),
                )
                recursive_stream = self._stream_message_impl(
                    body=body,
                    openai_messages=new_messages,
                    openai_tools=recurse_tools,
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
                )
                async for chunk in iter_with_keepalive(recursive_stream, KEEPALIVE_INTERVAL):
                    if chunk is _KEEPALIVE:
                        yield _sse("ping", {"type": "ping"})
                    else:
                        yield chunk
                return

            else:
                if tool_buffers:
                    logger.info("[ToolCallEmit] Emitting %d tool buffer(s) for model=%s depth=%d",
                                len(tool_buffers), model_alias, recursion_depth)
                    declared_names = declared_tool_names(openai_tools)
                    for tc_idx in sorted(tool_buffers.keys()):
                        buf = tool_buffers[tc_idx]
                        name = buf["name"]
                        # The client validates the name against its own
                        # declarations and rejects anything else outright, so an
                        # unambiguous respelling is repaired here, at emit time.
                        # The id is left alone: it is an opaque handle, and
                        # clients that echo it back only need it to be unique.
                        resolved = resolve_declared_tool_name(name, declared_names)
                        if resolved != name:
                            logger.warning(
                                "[ToolCallEmit] model emitted %r; the client declared %r",
                                name, resolved,
                            )
                            name = resolved
                            buf["name"] = name
                        logger.info("[ToolCallEmit]   idx=%d id=%s name=%s args_len=%d args_preview=%s",
                                    tc_idx, buf["id"], name, len(buf["args"]), buf["args"][:120])
                        # A provider that stops mid-arguments — max_tokens, a
                        # dropped connection, a proxy that closed early — still
                        # ends the stream cleanly, so the buffer holds half an
                        # object. Forwarding that half would tell the client a
                        # call is ready to execute and hand it arguments that do
                        # not parse, which is how a tool ends up running with no
                        # arguments at all. Both sibling paths below (the hosted
                        # search and Agent/Task arms) already parse defensively;
                        # this one did not, and it is the arm every client tool
                        # goes through.
                        if buf["args"]:
                            try:
                                json.loads(buf["args"])
                            except Exception as exc:
                                logger.warning(
                                    "[ToolCallEmit] dropping %s (idx=%d id=%s): arguments are "
                                    "not valid JSON (%s), the provider stopped mid-call",
                                    name, tc_idx, buf["id"], exc,
                                )
                                bigger = min(max_tokens * 2, config.MAX_OUTPUT_TOKENS)
                                if bigger > max_tokens:
                                    logger.warning(
                                        "[Claude Stream] %s cut off at max_tokens=%s; "
                                        "recovering the call at %s",
                                        name, max_tokens, bigger)
                                    recovered = await _recover_tool_call(
                                        lambda b: pool_manager.call_stream(
                                            model_alias=model_alias,
                                            messages=openai_messages,
                                            tools=openai_tools or None,
                                            temperature=temperature,
                                            max_tokens=b,
                                            thinking_config=thinking_config,
                                            account=account,
                                            thinking_params=thinking_params,
                                            sampling_params=sampling_params,
                                        ),
                                        bigger,
                                        "".join(accumulated_text),
                                    )
                                    if recovered is not None:
                                        for r_idx in sorted(recovered):
                                            tool_buffers[r_idx] = recovered[r_idx]
                                        buf = tool_buffers[tc_idx]
                                        name = buf["name"]
                                        resolved = resolve_declared_tool_name(name, declared_names)
                                        if resolved != name:
                                            logger.warning(
                                                "[ToolCallEmit] recovered call emitted %r; "
                                                "the client declared %r", name, resolved,
                                            )
                                            name = resolved
                                            buf["name"] = name
                                        emitted_tool_blocks += 1
                                        next_block_idx += 1
                                        yield _sse("content_block_start", {
                                            "type": "content_block_start",
                                            "index": next_block_idx - 1,
                                            "content_block": {
                                                "type": "tool_use", "id": buf["id"],
                                                "name": name, "input": {},
                                            },
                                        })
                                        if buf["args"]:
                                            yield _sse("content_block_delta", {
                                                "type": "content_block_delta",
                                                "index": next_block_idx - 1,
                                                "delta": {
                                                    "type": "input_json_delta",
                                                    "partial_json": buf["args"],
                                                },
                                            })
                                        yield _sse("content_block_stop", {
                                            "type": "content_block_stop",
                                            "index": next_block_idx - 1,
                                        })
                                        continue
                                    dropped_incomplete_tool = True
                                else:
                                    dropped_incomplete_tool = True
                                continue
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
                            if router_hosted_tools_enabled() and name in (
                                    "web_search", "WebSearch", "web_fetch", "WebFetch"):
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
                        emitted_tool_blocks += 1
                        next_block_idx += 1

                # Fail-safe: an Anthropic response must not finish with a
                # web_search_tool_result block. Give the provider one bounded
                # plain-text synthesis retry before returning a short message.
                if not text_started and server_tool_calls and not tool_buffers and emitted_tool_blocks == 0:
                    if not synthesis_retry:
                        logger.warning(
                            "[Claude Stream] Empty synthesis after hosted search; "
                            "retrying once without tools")
                        retry_stream = self._stream_message_impl(
                            body=body,
                            openai_messages=openai_messages,
                            openai_tools=None,
                            model_alias=model_alias,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            thinking_config=thinking_config,
                            thinking_params=thinking_params,
                            account=account,
                            auth_key_prefix=auth_key_prefix,
                            msg_id=msg_id,
                            recursion_depth=5,
                            start_block_index=next_block_idx,
                            sampling_params=sampling_params,
                            emit_message_start=False,
                            search_limits=search_limits,
                            server_tool_calls=server_tool_calls,
                            synthesis_retry=True,
                        )
                        async for chunk in iter_with_keepalive(retry_stream, KEEPALIVE_INTERVAL):
                            if chunk is _KEEPALIVE:
                                yield _sse("ping", {"type": "ping"})
                            else:
                                yield chunk
                        return

                    fallback_text = SEARCH_SYNTHESIS_FALLBACK

                    yield _sse("content_block_start", {
                        "type": "content_block_start",
                        "index": next_block_idx,
                        "content_block": {"type": "text", "text": ""},
                    })
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": next_block_idx,
                        "delta": {"type": "text_delta", "text": fallback_text},
                    })
                    yield _sse("content_block_stop", {
                        "type": "content_block_stop",
                        "index": next_block_idx,
                    })
                    accumulated_text.append(fallback_text)
                    text_started = True
                    next_block_idx += 1

                output_tokens = max(provider_output_tokens,
                                    max(1, output_chars // 4) + len(tool_buffers) * 50)
                final_stop_reason, final_stop_sequence = map_stop_reason(
                    finish_reason if finish_reason != "tool_use" else "tool_calls",
                    # What the client can actually act on, not what the provider
                    # started: a call dropped above was never announced, so
                    # claiming tool_calls would leave the client waiting on a
                    # tool_use block that does not exist.
                    has_tool_calls=bool(tool_buffers) and emitted_tool_blocks > 0,
                    incomplete_tool_call=dropped_incomplete_tool,
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
                        "[REDACTED]",
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
                from src.core.runtime.sub_agent_detect import is_sub_agent_body
                if is_sub_agent_body(body):
                    from .helpers import _classify_error_reason
                    reason = _classify_error_reason(str(e))
                    # ponytail: inform main agent to proceed directly rather than triggering an infinite retry crash loop
                    summary_text = (
                        f"[Notice: Sub-agent could not complete background query ({reason})]. "
                        "Proceed with the current session context directly."
                    )
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
        finally:
            # Ensure the underlying stream is closed
            try:
                if hasattr(stream, "aclose"):
                    await stream.aclose()
            except Exception as e:
                logger.warning("[Claude Stream] Error closing underlying stream: %s", e)

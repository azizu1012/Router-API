"""Claude stream proxy — format converter only.

Pool/key/custom management is centralized in PoolManager.
"""

import json
import uuid
from typing import Any, Dict, List, Optional, AsyncIterator

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.pool_manager import pool_manager
from src.logical_HQ_translator import (
    _convert_messages,
    _intercept_sub_agent,
    _dict_to_sse_events,
    _sse,
    _get_simulated_cache_usage,
    XMLThinkingExtractor,
)
from .compaction import _pre_compact_and_truncate
from .helpers import get_system_status_summary


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

        from src.api.opencode_proxy.handler.websearch import should_enable_web_search
        from src.api.opencode_proxy.handler.proxy import _WEBSEARCH_TOOL_DEF, _resolve_thinking_config
        from src.logical_HQ_translator.sse_cache_agent import is_sub_agent_body
        if not is_sub_agent_body(body) and should_enable_web_search(body, account) and not any(
            t.get("function", {}).get("name") in ("WebSearch", "web_search") for t in openai_tools
        ):
            openai_tools.append(_WEBSEARCH_TOOL_DEF)
            logger.info("[WebSearch] Injected WebSearch tool for Claude stream")

        override_alias = _intercept_sub_agent(body, account=account)
        model_alias = override_alias or router.resolve_model_alias(body.get("model", "")) or config.DEFAULT_MODEL_ALIAS
        await _pre_compact_and_truncate(body, openai_messages, openai_tools, model_alias)

        try:
            max_tokens = max(1, min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS))
            temperature = float(body.get("temperature", 0.7))
            thinking_config = _resolve_thinking_config(body, model_alias)
            thinking_params = {
                "thinking_level": body.get("thinking_level"),
                "thinking_budget": body.get("thinking_budget"),
                "include_thoughts": body.get("include_thoughts", True),
            }

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
            ):
                yield chunk
        except Exception as e:
            logger.error("[Claude Stream] stream_message failed: %s", e, exc_info=True)
            raise e

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
    ) -> AsyncIterator[bytes]:
        """
        Triển khai logic streaming chính cho các yêu cầu hoàn thành chat Claude.
        Phương thức này tương tác trực tiếp với `PoolManager` để nhận các chunk streaming
        và xử lý chúng để tạo ra các sự kiện SSE phù hợp cho client.

        **Các bước xử lý chính:**
        1. Khởi tạo trạng thái cho việc streaming, bao gồm các cờ (flags) cho `text_started`,
           `thinking_started`, `thinking_stopped`, các bộ đệm công cụ (`tool_buffers`),
           và các biến để tích lũy nội dung văn bản và suy nghĩ.
        2. Gọi `pool_manager.call_stream` để nhận một iterator bất đồng bộ của các chunk phản hồi từ mô hình.
        3. Lặp qua từng `item` được trả về từ `pool_manager.call_stream`:
           a. Xử lý các sự kiện `message_start` và `delta`.
           b. Trích xuất suy nghĩ (thoughts) và văn bản từ các chunk phản hồi, đặc biệt là
              khi mô hình trả về nội dung XML chứa các thẻ `<thinking>`.
           c. Xử lý các cuộc gọi công cụ (tool calls) được mô hình tạo ra, đệm các đối số của chúng.
           d. Kiểm tra xem có cuộc gọi công cụ bị chặn (WebSearch/WebFetch) nào không.
              Nếu có, nó sẽ tạm dừng luồng hiện tại, thực thi công cụ bị chặn,
              và sau đó gọi lại `_stream_message_impl` một cách đệ quy với kết quả của công cụ
              được thêm vào `openai_messages`. Điều này cho phép mô hình tiếp tục suy luận
              dựa trên kết quả của công cụ.
        4. Tạo và gửi các sự kiện SSE (`content_block_start`, `content_block_delta`,
           `content_block_stop`, `message_delta`, `message_stop`) cho client dựa trên
           nội dung, suy nghĩ và các cuộc gọi công cụ được xử lý.
        5. Xử lý các trường hợp lỗi, bao gồm việc trả về một thông báo tóm tắt trạng thái
           hệ thống nếu yêu cầu đến từ sub-agent và có lỗi xảy ra ở độ sâu đệ quy bằng 0.

        Args:
            body (Dict[str, Any]): Body của yêu cầu API gốc.
            openai_messages (List[Dict[str, Any]]): Danh sách các tin nhắn đã được chuyển đổi.
            openai_tools (Optional[List[Dict[str, Any]]]): Danh sách các công cụ đã được chuyển đổi.
            model_alias (str): Bí danh của mô hình được sử dụng.
            temperature (float): Tham số nhiệt độ cho yêu cầu mô hình.
            max_tokens (int): Số lượng token đầu ra tối đa.
            thinking_config (Optional[Dict[str, Any]]): Cấu hình "thinking" của mô hình.
            thinking_params (Dict[str, Any]): Các tham số "thinking" bổ sung.
            account (Optional[Dict[str, Any]]): Thông tin tài khoản người dùng.
            auth_key_prefix (str): Tiền tố khóa xác thực.
            msg_id (str): ID của tin nhắn hiện tại.
            recursion_depth (int): Độ sâu đệ quy hiện tại (để xử lý các cuộc gọi công cụ bị chặn).
            start_block_index (int): Chỉ số khối nội dung bắt đầu.

        Yields:
            AsyncIterator[bytes]: Một iterator bất đồng bộ của các khối phản hồi streaming theo định dạng SSE.
        """
        try:
            text_started = False
            thinking_started = False
            thinking_stopped = False
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

            started = False

            async for item in pool_manager.call_stream(
                model_alias=model_alias,
                messages=openai_messages,
                tools=openai_tools or None,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking_config=thinking_config,
                account=account,
                thinking_params=thinking_params,
            ):
                input_tokens = item.get("input_tokens", input_tokens)
                if not started:
                    started = True
                    if recursion_depth == 0:
                        resolved_input_tokens = input_tokens or 0
                        cache_usage = _get_simulated_cache_usage(body or {}, resolved_input_tokens)
                        cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
                        cr = cache_usage.get("cache_read_input_tokens", 0) or 0
                        client_input_tokens = max(1, resolved_input_tokens - cc - cr)
                        yield _sse("ping", {"type": "ping", "retry": 0, "reason": "initial"})
                        yield _sse("message_start", {
                            "type": "message_start",
                            "message": {
                                "id": msg_id,
                                "type": "message",
                                "role": "assistant",
                                "model": body.get("model") or model_alias,
                                "content": [],
                                "stop_reason": None,
                                "stop_sequence": None,
                                "usage": {
                                    "input_tokens": client_input_tokens,
                                    "output_tokens": 0,
                                    **cache_usage
                                },
                            },
                        })

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

                if reasoning:
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
                                    "delta": {"type": "signature_delta", "signature": ""},
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
                                        "delta": {"type": "signature_delta", "signature": ""},
                                    })
                                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": thinking_index})
                                yield _sse("content_block_start", {
                                    "type": "content_block_start",
                                    "index": text_index,
                                    "content_block": {"type": "text", "text": ""},
                                })
                            yield _sse("content_block_delta", {
                                "type": "content_block_delta",
                                "index": text_index,
                                "delta": {"type": "text_delta", "text": ev_val},
                            })
                            output_chars += len(ev_val)

                tool_calls_val = getattr(delta, "tool_calls", None) or delta.get("tool_calls") if hasattr(delta, "get") else None
                if tool_calls_val:
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

                if fr:
                    finish_reason = "tool_use" if str(fr).lower() == "tool_calls" else ("max_tokens" if "max" in str(fr).lower() else "end_turn")

            # Flush extractor to handle any remaining tags or text
            events = extractor.flush()
            for ev_type, ev_val in events:
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
                                "delta": {"type": "signature_delta", "signature": ""},
                            })
                            yield _sse("content_block_stop", {"type": "content_block_stop", "index": thinking_index})
                        yield _sse("content_block_start", {
                            "type": "content_block_start",
                            "index": text_index,
                            "content_block": {"type": "text", "text": ""},
                        })
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": text_index,
                        "delta": {"type": "text_delta", "text": ev_val},
                    })
                    output_chars += len(ev_val)

            if thinking_started and not thinking_stopped:
                thinking_stopped = True
                yield _sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": thinking_index,
                    "delta": {"type": "signature_delta", "signature": ""},
                })
                yield _sse("content_block_stop", {"type": "content_block_stop", "index": thinking_index})
            if text_started:
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

                tool_result = ""
                if name in ("web_search", "WebSearch"):
                    query = args.get("query", "")
                    logger.info("[Claude Proxy Intercept Stream - WebSearch] executing query=%r", query[:160])
                    try:
                        from src.core.providers.search_manager import execute_hybrid_search
                        from src.api.opencode_proxy.handler.websearch import resolve_search_engine
                        se = resolve_search_engine(body, account)
                        search_context, combined_citations = await execute_hybrid_search(
                            [query], search_engine=se, auth_key_prefix=auth_key_prefix, account=account
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
                            tool_result = "\n".join(result_lines)
                        else:
                            tool_result = "No search results found."
                    except Exception as e:
                        tool_result = f"Search error: {e}"
                        logger.warning("[Claude Proxy Intercept Stream - WebSearch] query failed: %s", e)

                elif name in ("web_fetch", "WebFetch"):
                    url = args.get("url", "")
                    logger.info("[Claude Proxy Intercept Stream - WebFetch] fetching url=%r", url[:200])
                    try:
                        import aiohttp
                        async with aiohttp.ClientSession() as session:
                            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10),
                                                   headers={"User-Agent": "Mozilla/5.0 (Router API; +http://127.0.0.1:58100)"}) as resp_fetch:
                                if resp_fetch.status == 200:
                                    raw = await resp_fetch.text()
                                    tool_result = raw[:8000]
                                else:
                                    tool_result = f"HTTP error {resp_fetch.status}"
                    except Exception as e:
                        tool_result = f"WebFetch error: {e}"
                        logger.warning("[Claude Proxy Intercept Stream - WebFetch] fetch failed: %s", e)

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
                ):
                    yield chunk

            else:
                if tool_buffers:
                    finish_reason = "tool_use"
                    for tc_idx in sorted(tool_buffers.keys()):
                        buf = tool_buffers[tc_idx]
                        name = buf["name"]
                        if name == "Task":
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
                yield _sse("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": finish_reason, "stop_sequence": None},
                    "usage": {"output_tokens": output_tokens},
                })
                yield _sse("message_stop", {"type": "message_stop"})

        except Exception as e:
            logger.error("[Claude Stream Recurse] PoolManager failed: %s", e, exc_info=True)
            if recursion_depth == 0:
                from src.logical_HQ_translator.sse_cache_agent import is_sub_agent_body
                if is_sub_agent_body(body):
                    summary_text = get_system_status_summary(model_alias)
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
                    raise e
            else:
                raise e

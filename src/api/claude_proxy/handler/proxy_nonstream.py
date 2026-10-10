"""Claude non-stream proxy — format converter only.

Pool/key/custom management is centralized in PoolManager.
"""

import json
import uuid
from typing import Any, Dict, Optional

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.pool_manager import pool_manager
from src.core.usage_logger import log_usage
from src.logical_HQ_translator import _convert_messages, XMLThinkingExtractor
from .anthropic_spec import (
    apply_tool_choice_to_tools,
    compute_usage,
    estimate_input_tokens,
    extract_sampling_params,
    extract_tool_choice,
    map_stop_reason,
    thinking_signature,
    _server_tool_record,
    _server_tool_use_block,
    _web_search_result_block,
    _search_citations,
    server_search_limits,
    run_server_tool,
)
from .compaction import _pre_compact_and_truncate


class ClaudeProxyNonstreamMixin:
    """
    `ClaudeProxyNonstreamMixin` cung cấp logic để xử lý các yêu cầu hoàn thành chat không streaming
    cho API Claude. Mixin này tập trung vào việc chuyển đổi định dạng, chèn công cụ WebSearch
    và xử lý các cuộc gọi công cụ bị chặn (intercepted tool calls) như WebSearch hoặc WebFetch.

    Nó ủy quyền việc gọi API thực tế đến `PoolManager` và sau đó định dạng lại phản hồi từ
    `PoolManager` thành định dạng mong muốn của client Claude non-streaming.

    **Các chức năng chính bao gồm:**
    - Chuyển đổi định dạng tin nhắn từ OpenCode sang Claude và ngược lại.
    - Chèn công cụ WebSearch nếu được yêu cầu và không phải là yêu cầu từ sub-agent.
    - Xử lý các yêu cầu "thinking" và nén ngữ cảnh (context compaction).
    - Thực thi các cuộc gọi công cụ bị chặn (ví dụ: WebSearch, WebFetch) trong một vòng lặp đệ quy.
    - Định dạng phản hồi cuối cùng, bao gồm cả việc trích xuất suy nghĩ (thoughts) từ phản hồi XML.
    """

    async def create_message(
        self, body: Dict[str, Any], auth_key_prefix: str = "", account: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Tạo và xử lý một yêu cầu hoàn thành chat không streaming cho API Claude.

        Phương thức này thực hiện các bước sau:
        1. Chuyển đổi định dạng tin nhắn từ OpenCode sang định dạng nội bộ của Claude.
        2. Kiểm tra và chèn công cụ WebSearch nếu tìm kiếm web được kích hoạt và yêu cầu không phải từ sub-agent.
        3. Giải quyết bí danh mô hình và thực hiện nén ngữ cảnh (context compaction) nếu cần.
        4. Thiết lập cấu hình "thinking" và các tham số khác cho cuộc gọi API.
        5. Gọi `pool_manager.call_nonstream` trong một vòng lặp đệ quy để xử lý các cuộc gọi công cụ bị chặn.
           Nếu mô hình trả về một cuộc gọi công cụ bị chặn (WebSearch hoặc WebFetch),
           phương thức sẽ thực thi công cụ đó và sau đó gửi lại kết quả vào mô hình.
           Vòng lặp này sẽ tiếp tục cho đến khi không còn cuộc gọi công cụ bị chặn nào hoặc đạt đến độ sâu đệ quy tối đa (5 lần).
        6. Trích xuất nội dung, suy nghĩ và các cuộc gọi công cụ từ phản hồi của mô hình.
        7. Định dạng lại phản hồi cuối cùng thành một dictionary tương thích với API Claude,
           bao gồm thông tin sử dụng token và lý do dừng.

        Args:
            body (Dict[str, Any]): Body của yêu cầu API gốc.
            auth_key_prefix (str, optional): Tiền tố khóa xác thực. Mặc định là "".
            account (Optional[Dict[str, Any]], optional): Thông tin tài khoản người dùng. Mặc định là None.

        Returns:
            Dict[str, Any]: Một dictionary biểu diễn phản hồi hoàn thành chat không streaming đã được định dạng.
        """
        openai_messages, openai_tools = _convert_messages(body)

        from src.api.opencode_proxy.handler.websearch import should_enable_web_search
        from src.api.opencode_proxy.handler.proxy import _WEBSEARCH_TOOL_DEF, _resolve_thinking_config, _extract_thinking_params
        from src.core.sub_agent_detect import is_sub_agent_body
        if not is_sub_agent_body(body) and should_enable_web_search(body, account) and not any(
            t.get("function", {}).get("name") in ("WebSearch", "web_search") for t in openai_tools
        ):
            openai_tools.append(_WEBSEARCH_TOOL_DEF)
            logger.info("[WebSearch] Injected WebSearch tool for Claude non-stream")
            
        from src.core.compaction_detect import strip_tools_for_compaction
        strip_tools_for_compaction(body, openai_messages, openai_tools, is_claude_or_opencode=True)

        model_alias = router.resolve_model_alias(body.get("model", "")) or config.DEFAULT_MODEL_ALIAS

        await _pre_compact_and_truncate(body, openai_messages, openai_tools, model_alias)

        max_tokens = max(1, min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS))
        temperature = float(body.get("temperature", 0.7))
        thinking_config = _resolve_thinking_config(body, model_alias)
        thinking_params = _extract_thinking_params(body)

        # tool_choice: "none" removes tools entirely; anything else is passed down.
        tool_choice = extract_tool_choice(body)
        if tool_choice:
            openai_tools = apply_tool_choice_to_tools(tool_choice, openai_tools)

        sampling_params = extract_sampling_params(body)
        stop_sequences = sampling_params.get("stop_sequences")

        recursion_depth = 0
        # The model id the client asked for, before any resolution. Every model
        # path that ends in "this model doesn't work" needs this line: the id
        # never reaches the log otherwise, because a failed call writes nothing
        # to usage_logs and the traceback names the pool, not the request.
        logger.info(
            "[Claude NonStream] requested model=%r stream=%s msgs=%d tools=%d",
            body.get("model"), bool(body.get("stream")),
            len(body.get("messages") or []), len(body.get("tools") or []),
        )
        # Searches the router ran for the model, reported as
        # server-tool blocks instead of client tool_use blocks.
        server_tool_calls: list = []
        # `max_uses`, `allowed_domains` and `blocked_domains` are declared on the
        # client's tool definition, so honouring them is part of speaking the
        # protocol rather than a nicety: a client that set max_uses=3 expects
        # three searches, and one that blocked a domain expects it kept out.
        search_limits = server_search_limits(body)
        output_tokens = 0
        text = ""
        thought = None
        finish_reason = "stop"
        msg = None
        used_api_key = ""
        used_model_id = ""
        while recursion_depth < 5:
            result = await pool_manager.call_nonstream(
                model_alias=model_alias,
                messages=openai_messages,
                tools=openai_tools or None,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking_config=thinking_config,
                account=account,
                extra_body=None,
                thinking_params=thinking_params,
                sampling_params=sampling_params,
            )

            resp = result["response"]
            used_api_key = result.get("api_key", "") or ""
            used_model_id = result.get("model_id", "") or model_alias
            usage = getattr(resp, "usage", None) or {}
            output_tokens = 0
            if isinstance(usage, dict):
                output_tokens = usage.get("completion_tokens", 0) or 0

            choice = resp.choices[0] if getattr(resp, "choices", None) else None
            msg = getattr(choice, "message", None) if choice else None
            text = getattr(msg, "content", "") if msg else ""
            thought = getattr(msg, "reasoning_content", None) if msg else None
            tsig = None
            if msg:
                if isinstance(msg, dict):
                    tsig = msg.get("thought_signature")
                else:
                    tsig = getattr(msg, "thought_signature", None)
            finish_reason = getattr(choice, "finish_reason", "stop") if choice else "stop"

            if not thought and text:
                extractor = XMLThinkingExtractor()
                events = extractor.feed(text) + extractor.flush()
                clean_parts = []
                thought_parts = []
                for ev_type, ev_val in events:
                    if ev_type == "thinking":
                        thought_parts.append(ev_val)
                    elif ev_type == "text":
                        clean_parts.append(ev_val)
                if thought_parts:
                    thought = "".join(thought_parts)
                    text = "".join(clean_parts)

            # Check if there is an intercepted tool call (web_search / web_fetch)
            raw_tool_calls = []
            if msg:
                if isinstance(msg, dict):
                    raw_tool_calls = msg.get("tool_calls") or []
                else:
                    raw_tool_calls = getattr(msg, "tool_calls", None) or []

            intercepted_call = None
            for tc in raw_tool_calls:
                if isinstance(tc, dict):
                    name = tc.get("function", {}).get("name", "")
                else:
                    name = getattr(tc.function, "name", "")
                if name in ("web_search", "WebSearch", "web_fetch", "WebFetch"):
                    intercepted_call = tc
                    break

            if not intercepted_call:
                break

            # Execute the intercepted tool
            tc_id = intercepted_call.get("id") if isinstance(intercepted_call, dict) else getattr(intercepted_call, "id", f"call_{uuid.uuid4().hex[:16]}")
            if isinstance(intercepted_call, dict):
                fn = intercepted_call.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", "{}")
            else:
                name = getattr(intercepted_call.function, "name", "")
                args = getattr(intercepted_call.function, "arguments", "{}")

            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}

            searches_used = sum(1 for r in server_tool_calls
                                if r["name"] == "web_search")
            tool_result, search_citations, search_error = await run_server_tool(
                name, args, body=body, account=account,
                auth_key_prefix=auth_key_prefix, limits=search_limits,
                used=searches_used)

            # Construct messages for recursive turn
            ast_text = text
            if thought:
                ast_text = f"<thinking>\n{thought}\n</thinking>\n{ast_text}" if ast_text else f"<thinking>\n{thought}\n</thinking>"

            assistant_msg = {
                "role": "assistant",
                "content": ast_text or None,
                "reasoning_content": thought or None,
                "thought_signature": tsig or None,
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
            openai_messages.extend([assistant_msg, tool_result_msg])
            # The router ran this search itself, so the caller sees Anthropic's
            # hosted-tool shape rather than a client tool_use it must answer.
            server_tool_calls.append(
                _server_tool_record(name, tc_id or "", args, tool_result,
                                    search_citations, search_error)
            )
            recursion_depth += 1

        content_blocks = []
        # Searches the router ran on the model's behalf, reported in Anthropic's
        # hosted-tool shape rather than as a client `tool_use` the caller would
        # be obliged to answer.
        if thought:
            content_blocks.append({
                "type": "thinking",
                "thinking": thought,
                "signature": thinking_signature(thought),
            })
        for rec in server_tool_calls:
            content_blocks.append(_server_tool_use_block(rec))
            content_blocks.append(_web_search_result_block(rec))
        if text:
            text_block = {"type": "text", "text": text}
            # Citations ride on the text that used them, which is where
            # Anthropic puts them.
            cites = _search_citations(server_tool_calls)
            if cites:
                text_block["citations"] = cites
            content_blocks.append(text_block)

        if isinstance(msg, dict):
            raw_tool_calls = msg.get("tool_calls")
        else:
            raw_tool_calls = getattr(msg, "tool_calls", None) if msg else None
        has_tool_calls = False
        if raw_tool_calls:
            for tc in raw_tool_calls:
                if isinstance(tc, dict):
                    fn = tc.get("function", {})
                    name = fn.get("name", "") if isinstance(fn, dict) else ""
                    args = fn.get("arguments", "{}") if isinstance(fn, dict) else "{}"
                    tc_id = tc.get("id", f"toolu_{uuid.uuid4().hex[:16]}")
                else:
                    fn = getattr(tc, "function", None)
                    name = getattr(fn, "name", "") if fn else ""
                    args = getattr(fn, "arguments", "{}") if fn else "{}"
                    tc_id = getattr(tc, "id", f"toolu_{uuid.uuid4().hex[:16]}")
                if name:
                    has_tool_calls = True
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if name in ("web_search", "WebSearch", "web_fetch",
                                "WebFetch"):
                        # Reached only when the recursion ran out. This is not a
                        # tool the client owns — the router already ran it, or
                        # refused it past `max_uses` — so emitting `tool_use`
                        # would hand the client work it has no definition for.
                        logger.info(
                            "[Claude NonStream] dropping unhandled %s at "
                            "recursion limit", name)
                        has_tool_calls = False
                        continue
                    if name in ("Agent", "Task"):
                        prompt_str = args.get("prompt", "") if isinstance(args, dict) else str(args)
                        content_blocks.append({
                            "type": "agent_use",
                            "id": tc_id,
                            "agent_type": "general-purpose",
                            "prompt": prompt_str,
                        })
                    else:
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc_id,
                            "name": name,
                            "input": args if isinstance(args, dict) else {},
                        })
                        
        has_text = any(b.get("type") == "text" for b in content_blocks)
        has_tool = any(b.get("type") == "tool_use" for b in content_blocks)
        if not has_text:
            if thought and not has_tool:
                content_blocks.append({"type": "text", "text": thought})
            else:
                content_blocks.append({"type": "text", "text": ""})

        stop_reason, stop_sequence = map_stop_reason(
            finish_reason,
            has_tool_calls=has_tool_calls,
            stop_sequences=stop_sequences,
            emitted_text=text or "",
        )

        client_input_tokens = estimate_input_tokens(body)
        usage = compute_usage(body, client_input_tokens, output_tokens)
        # Anthropic bills server-side searches separately and reports the count
        # here; a client reconciling its own spend needs the real number.
        ran_search = sum(1 for r in server_tool_calls
                         if r["name"] == "web_search" and not r.get("failed"))
        ran_fetch = sum(1 for r in server_tool_calls
                        if r["name"] == "web_fetch" and not r.get("failed"))
        if ran_search or ran_fetch:
            usage["server_tool_use"] = {"web_search_requests": ran_search,
                                        "web_fetch_requests": ran_fetch}

        # Persist usage so Claude Code traffic shows up in the dashboard.
        try:
            await log_usage(
                used_model_id or model_alias,
                (used_api_key or "")[-8:],
                usage["input_tokens"],
                usage["output_tokens"],
                auth_key_prefix,
                usage["cache_creation_input_tokens"],
                usage["cache_read_input_tokens"],
            )
        except Exception as e:
            logger.warning("[Claude NonStream] log_usage failed: %s", e)

        return {
            "id": "msg_" + uuid.uuid4().hex[:24],
            "type": "message",
            "role": "assistant",
            "model": body.get("model") or model_alias,
            "content": content_blocks,
            "stop_reason": stop_reason,
            "stop_sequence": stop_sequence,
            "usage": usage,
        }

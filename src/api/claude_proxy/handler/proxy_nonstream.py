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
from src.logical_HQ_translator import _convert_messages, XMLThinkingExtractor
from .compaction import _pre_compact_and_truncate
from .helpers import get_system_status_summary


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
        from src.logical_HQ_translator.sse_cache_agent import is_sub_agent_body
        if not is_sub_agent_body(body) and should_enable_web_search(body, account) and not any(
            t.get("function", {}).get("name") in ("WebSearch", "web_search") for t in openai_tools
        ):
            openai_tools.append(_WEBSEARCH_TOOL_DEF)
            logger.info("[WebSearch] Injected WebSearch tool for Claude non-stream")

        model_alias = router.resolve_model_alias(body.get("model", "")) or config.DEFAULT_MODEL_ALIAS

        await _pre_compact_and_truncate(body, openai_messages, openai_tools, model_alias)

        max_tokens = max(1, min(int(body.get("max_tokens", 4096)), config.MAX_OUTPUT_TOKENS))
        temperature = float(body.get("temperature", 0.7))
        thinking_config = _resolve_thinking_config(body, model_alias)
        thinking_params = _extract_thinking_params(body)

        recursion_depth = 0
        input_tokens = 0
        output_tokens = 0
        text = ""
        thought = None
        finish_reason = "stop"
        msg = None
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
            )

            resp = result["response"]
            input_tokens = result.get("input_tokens", 0) or 0
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

            tool_result = ""
            if name in ("web_search", "WebSearch"):
                query = args.get("query", "")
                logger.info("[Claude Proxy Intercept - WebSearch] executing query=%r", query[:160])
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
                    logger.warning("[Claude Proxy Intercept - WebSearch] query failed: %s", e)

            elif name in ("web_fetch", "WebFetch"):
                url = args.get("url", "")
                logger.info("[Claude Proxy Intercept - WebFetch] fetching url=%r", url[:200])
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
                    logger.warning("[Claude Proxy Intercept - WebFetch] fetch failed: %s", e)

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
            recursion_depth += 1

        content_blocks = []
        if thought:
            content_blocks.append({"type": "thinking", "thinking": thought, "signature": ""})
        content_blocks.append({"type": "text", "text": text or ""})

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

        if has_tool_calls:
            stop_reason = "tool_use"
        elif finish_reason and "max" in str(finish_reason).lower():
            stop_reason = "max_tokens"
        else:
            stop_reason = "end_turn"

        total_text = ""
        for m in (body.get("messages") or []):
            c = m.get("content", "")
            if isinstance(c, str):
                total_text += c
            elif isinstance(c, list):
                total_text += "".join(
                    b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"
                )
        client_input_tokens = max(1, len(total_text) // 4)
        return {
            "id": "msg_" + uuid.uuid4().hex[:24],
            "type": "message",
            "role": "assistant",
            "model": body.get("model") or model_alias,
            "content": content_blocks,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": client_input_tokens,
                "output_tokens": output_tokens,
            },
        }

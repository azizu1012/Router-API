import asyncio
import hashlib
import json
import uuid
from typing import Any, Dict, List, Tuple, Optional

from src.core.providers.gemini_facade import acompletion, token_counter
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.usage_logger import log_usage
from src.logical_HQ_translator import (
    _get_simulated_cache_usage,
    is_sub_agent_body,
    is_claude_code_body,
    normalize_text,
    XMLThinkingExtractor,
)

async def _resolve_gemini_with_tools_stream(
    kwargs: Dict[str, Any],
    body: Dict[str, Any],
    proxy_instance: Any,
    auth_key_prefix: str = "",
    account: Optional[Dict[str, Any]] = None,
    _recursion_depth: int = 0,
):
    """
    Một async generator xử lý luồng phản hồi từ Gemini API, bao gồm cả việc thực thi công cụ.
    Hàm này có khả năng đệ quy để xử lý các cuộc gọi công cụ bị chặn (intercepted tool calls)
    như WebSearch hoặc WebFetch, gửi kết quả của công cụ trở lại mô hình.

    **Các giai đoạn chính của quá trình xử lý:**
    1. **Khởi tạo luồng Gemini:** Gọi `acompletion` với `stream=True` để bắt đầu nhận các chunk từ Gemini.
    2. **Đệm và phân tích chunk:** Lặp qua từng chunk nhận được từ Gemini, đệm nội dung văn bản,
       nội dung suy nghĩ (`reasoning_content`) và các cuộc gọi công cụ (`tool_calls`).
       - `("text", str)`: Nội dung văn bản hiển thị.
       - `("reasoning", str)`: Nội dung suy nghĩ/lý luận.
       - `("result", text, tool_calls, finish_reason, thought_text)`: Dữ liệu tích lũy cuối cùng.
    3. **Xử lý đệ quy cuộc gọi công cụ:**
       - Nếu mô hình trả về một cuộc gọi công cụ bị chặn (hiện tại là WebSearch hoặc WebFetch)
         và chưa đạt đến độ sâu đệ quy tối đa (3 lần), hàm sẽ thực thi công cụ đó.
       - Kết quả của công cụ sẽ được định dạng và gửi lại cho mô hình bằng cách gọi đệ quy
         `_resolve_gemini_with_tools_stream` với `_recursion_depth` tăng lên.
       - Điều này cho phép mô hình tiếp tục suy luận dựa trên kết quả của công cụ.
    4. **Định dạng và trả về kết quả cuối cùng:**
       - Tổng hợp nội dung văn bản và suy nghĩ đã đệm.
       - Phân tích các cuộc gọi công cụ thành định dạng `tool_use` hoặc `agent_use`.
       - Tính toán số lượng token đầu ra và ghi lại việc sử dụng.
       - Trả về một dictionary phản hồi hoàn chỉnh ở định dạng OpenAI.

    Args:
        kwargs (Dict[str, Any]): Các đối số được truyền trực tiếp đến `acompletion` của Gemini.
        body (Dict[str, Any]): Body của yêu cầu API gốc.
        proxy_instance (Any): Instance của proxy gọi (ví dụ: `ClaudeProxyNonstreamMixin`).
        auth_key_prefix (str, optional): Tiền tố khóa xác thực. Mặc định là "".
        account (Optional[Dict[str, Any]], optional): Thông tin tài khoản người dùng. Mặc định là None.
        _recursion_depth (int, optional): Độ sâu đệ quy hiện tại để theo dõi các cuộc gọi công cụ bị chặn. Mặc định là 0.

    Yields:
        Tuple[str, Any]: Một tuple chứa loại sự kiện và dữ liệu tương ứng trong quá trình streaming.

    Returns:
        Dict[str, Any]: Khi hoàn tất hoặc đạt đến độ sâu đệ quy tối đa, trả về một dictionary
                       phản hồi hoàn chỉnh ở định dạng OpenAI.
    """
    try:
        kwargs["stream"] = True
        gen = await acompletion(**kwargs)
    except Exception as e:
        logger.error("[_resolve_gemini_with_tools_stream] acompletion error: %s", e, exc_info=True)
        raise

    text_buf: List[str] = []
    tool_call_buf: Dict = {}
    finish_reason = "stop"
    thought_buf: List[str] = []

    async for chunk in gen:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        finish = chunk.choices[0].finish_reason

        content = getattr(delta, "content", None)
        rc = getattr(delta, "reasoning_content", None)

        if content:
            text_buf.append(content)
            yield ("text", content)
        if rc:
            thought_buf.append(rc)
            yield ("reasoning", rc)
        if getattr(delta, "tool_calls", None):
            for tc in delta.tool_calls:
                if isinstance(tc, dict):
                    idx = tc.get("index", 0)
                    if idx not in tool_call_buf:
                        tool_call_buf[idx] = {"id": tc.get("id", f"call_{uuid.uuid4().hex}"), "name": "", "arguments": ""}
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "") if isinstance(fn, dict) else ""
                    args_val = fn.get("arguments") if isinstance(fn, dict) else None
                else:
                    idx = tc.index
                    if idx not in tool_call_buf:
                        tool_call_buf[idx] = {"id": getattr(tc, "id", f"call_{uuid.uuid4().hex}"), "name": "", "arguments": ""}
                    fn = getattr(tc, "function", None)
                    fn_name = getattr(fn, "name", "") if fn else ""
                    args_val = getattr(fn, "arguments", None) if fn else None
                if fn_name:
                    tool_call_buf[idx]["name"] = fn_name
                if args_val:
                    if isinstance(args_val, dict):
                        args_val = json.dumps(args_val)
                    elif not isinstance(args_val, str):
                        args_val = str(args_val)
                    cur = tool_call_buf[idx]["arguments"]
                    tool_call_buf[idx]["arguments"] = cur + args_val if isinstance(cur, str) else args_val
        if finish:
            finish_reason = finish

    text = "".join(text_buf)
    thought_text = "".join(thought_buf)
    tool_calls = list(tool_call_buf.values())

    if _recursion_depth >= 3:
        logger.warning("[ToolRecursion] Max recursion depth reached (3), returning tool calls as-is")
        yield ("result", text, tool_calls, finish_reason, thought_text)
        return

    # Skip tool call interception for custom endpoints (LM Studio, etc.)
    # — let the client handle WebSearch/WebFetch directly
    is_custom_endpoint = bool(kwargs.get("api_base"))

    web_call = next((tc for tc in tool_calls if tc.get("name") == "WebSearch"), None)
    if web_call and not is_custom_endpoint:
        try:
            args = json.loads(web_call["arguments"]) if isinstance(web_call["arguments"], str) else web_call["arguments"]
            query = args.get("query", "")
            logger.info("[WebSearch] executing query=%r model=%s", query[:160], kwargs.get("model", "-"))

            from src.core.providers.search_manager import execute_hybrid_search
            from src.api.opencode_proxy.handler.websearch import resolve_search_engine
            se = resolve_search_engine(body, account)
            search_context, combined_citations = await execute_hybrid_search([query], search_engine=se, auth_key_prefix=auth_key_prefix, account=account)

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
                result = "\n".join(result_lines)
            else:
                result = "No search results found."
        except Exception as e:
            result = f"Search error: {e}"
            logger.warning("[WebSearch] query failed: %s", e)

        assistant_msg = {
            "role": "assistant",
            "content": text or None,
            "tool_calls": [{"id": web_call["id"], "type": "function", "function": {"name": "WebSearch", "arguments": web_call["arguments"]}}]
        }
        tool_result_msg = {"role": "tool", "tool_call_id": web_call["id"], "content": result}

        new_kwargs = dict(kwargs)
        new_kwargs["messages"] = list(kwargs["messages"]) + [assistant_msg, tool_result_msg]

        async for evt in _resolve_gemini_with_tools_stream(new_kwargs, body, proxy_instance, auth_key_prefix=auth_key_prefix, account=account, _recursion_depth=_recursion_depth + 1):
            yield evt
        return

    web_fetch_call = next((tc for tc in tool_calls if tc.get("name") == "WebFetch"), None)
    if web_fetch_call and not is_custom_endpoint:
        try:
            args = json.loads(web_fetch_call["arguments"]) if isinstance(web_fetch_call["arguments"], str) else web_fetch_call["arguments"]
            url = args.get("url", "")
            logger.info("[WebFetch] fetching url=%r model=%s", url[:200], kwargs.get("model", "-"))

            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10),
                                       headers={"User-Agent": "Mozilla/5.0 (Router API; +http://127.0.0.1:58100)"}) as resp:
                    if resp.status == 200:
                        raw = await resp.text()
                        result = raw[:8000]
                    else:
                        result = f"HTTP error {resp.status}"
        except Exception as e:
            result = f"WebFetch error: {e}"
            logger.warning("[WebFetch] fetch failed: %s", e)

        assistant_msg = {
            "role": "assistant",
            "content": text or None,
            "tool_calls": [{"id": web_fetch_call["id"], "type": "function", "function": {"name": "WebFetch", "arguments": web_fetch_call["arguments"]}}]
        }
        tool_result_msg = {"role": "tool", "tool_call_id": web_fetch_call["id"], "content": result}

        new_kwargs = dict(kwargs)
        new_kwargs["messages"] = list(kwargs["messages"]) + [assistant_msg, tool_result_msg]

        async for evt in _resolve_gemini_with_tools_stream(new_kwargs, body, proxy_instance, auth_key_prefix=auth_key_prefix, account=account, _recursion_depth=_recursion_depth + 1):
            yield evt
        return

    yield ("result", text, tool_calls, finish_reason, thought_text)


async def _resolve_gemini_with_tools(kwargs: Dict[str, Any], body: Dict[str, Any], proxy_instance: Any, auth_key_prefix: str = "", account: Optional[Dict[str, Any]] = None, _recursion_depth: int = 0) -> Tuple[str, List[Dict[str, Any]], str, str]:
    try:
        kwargs["stream"] = True
        gen = await acompletion(**kwargs)
    except Exception as e:
        logger.error("[_resolve_gemini_with_tools] acompletion error: %s", e, exc_info=True)
        raise

    text_buf = []
    tool_call_buf = {}
    finish_reason = "stop"
    thought_buf = []

    async for chunk in gen:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        finish = chunk.choices[0].finish_reason

        if getattr(delta, "content", None):
            text_buf.append(delta.content)
        rc = getattr(delta, "reasoning_content", None)
        if rc:
            thought_buf.append(rc)
        if getattr(delta, "tool_calls", None):
            for tc in delta.tool_calls:
                if isinstance(tc, dict):
                    idx = tc.get("index", 0)
                    if idx not in tool_call_buf:
                        tool_call_buf[idx] = {"id": tc.get("id", f"call_{uuid.uuid4().hex}"), "name": "", "arguments": ""}
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "") if isinstance(fn, dict) else ""
                    args_val = fn.get("arguments") if isinstance(fn, dict) else None
                else:
                    idx = tc.index
                    if idx not in tool_call_buf:
                        tool_call_buf[idx] = {"id": getattr(tc, "id", f"call_{uuid.uuid4().hex}"), "name": "", "arguments": ""}
                    fn = getattr(tc, "function", None)
                    fn_name = getattr(fn, "name", "") if fn else ""
                    args_val = getattr(fn, "arguments", None) if fn else None
                if fn_name:
                    tool_call_buf[idx]["name"] = fn_name
                if args_val:
                    if isinstance(args_val, dict):
                        args_val = json.dumps(args_val)
                    elif not isinstance(args_val, str):
                        args_val = str(args_val)
                    cur = tool_call_buf[idx]["arguments"]
                    tool_call_buf[idx]["arguments"] = cur + args_val if isinstance(cur, str) else args_val
        if finish:
            finish_reason = finish

    text = "".join(text_buf)
    thought_text = "".join(thought_buf)
    tool_calls = list(tool_call_buf.values())

    if _recursion_depth >= 3:
        logger.warning("[ToolRecursion] Max recursion depth reached (3), returning tool calls as-is")
        return text, tool_calls, finish_reason, thought_text

    is_custom_endpoint = bool(kwargs.get("api_base"))

    web_call = next((tc for tc in tool_calls if tc.get("name") == "WebSearch"), None)
    if web_call and not is_custom_endpoint:
        try:
            args = json.loads(web_call["arguments"]) if isinstance(web_call["arguments"], str) else web_call["arguments"]
            query = args.get("query", "")
            logger.info("[WebSearch] executing query=%r model=%s", query[:160], kwargs.get("model", "-"))
            
            from src.core.providers.search_manager import execute_hybrid_search
            from src.api.opencode_proxy.handler.websearch import resolve_search_engine
            se = resolve_search_engine(body, account)
            search_context, combined_citations = await execute_hybrid_search([query], search_engine=se, auth_key_prefix=auth_key_prefix, account=account)

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
                result = "\n".join(result_lines)
            else:
                result = "No search results found."
        except Exception as e:
            result = f"Search error: {e}"
            logger.warning("[WebSearch] query failed: %s", e)

        assistant_msg = {
            "role": "assistant",
            "content": text or None,
            "tool_calls": [{"id": web_call["id"], "type": "function", "function": {"name": "WebSearch", "arguments": web_call["arguments"]}}]
        }
        tool_result_msg = {"role": "tool", "tool_call_id": web_call["id"], "content": result}

        new_kwargs = dict(kwargs)
        new_kwargs["messages"] = list(kwargs["messages"]) + [assistant_msg, tool_result_msg]

        ntext, ntools, nfr, nth = await _resolve_gemini_with_tools(new_kwargs, body, proxy_instance, auth_key_prefix=auth_key_prefix, account=account, _recursion_depth=_recursion_depth + 1)
        return ntext, ntools, nfr, nth

    web_fetch_call = next((tc for tc in tool_calls if tc.get("name") == "WebFetch"), None)
    if web_fetch_call and not is_custom_endpoint:
        try:
            args = json.loads(web_fetch_call["arguments"]) if isinstance(web_fetch_call["arguments"], str) else web_fetch_call["arguments"]
            url = args.get("url", "")
            logger.info("[WebFetch] fetching url=%r model=%s", url[:200], kwargs.get("model", "-"))

            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10),
                                       headers={"User-Agent": "Mozilla/5.0 (Router API; +http://127.0.0.1:58100)"}) as resp:
                    if resp.status == 200:
                        raw = await resp.text()
                        result = raw[:8000]
                    else:
                        result = f"HTTP error {resp.status}"
        except Exception as e:
            result = f"WebFetch error: {e}"
            logger.warning("[WebFetch] fetch failed: %s", e)

        assistant_msg = {
            "role": "assistant",
            "content": text or None,
            "tool_calls": [{"id": web_fetch_call["id"], "type": "function", "function": {"name": "WebFetch", "arguments": web_fetch_call["arguments"]}}]
        }
        tool_result_msg = {"role": "tool", "tool_call_id": web_fetch_call["id"], "content": result}

        new_kwargs = dict(kwargs)
        new_kwargs["messages"] = list(kwargs["messages"]) + [assistant_msg, tool_result_msg]

        ntext2, ntools2, nfr2, nth2 = await _resolve_gemini_with_tools(new_kwargs, body, proxy_instance, auth_key_prefix=auth_key_prefix, account=account, _recursion_depth=_recursion_depth + 1)
        return ntext2, ntools2, nfr2, nth2

    return text, tool_calls, finish_reason, thought_text

async def _execute_nonstream(proxy_instance: Any, kwargs: Dict[str, Any], api_key: str, model_id: str, model_alias: str, input_tokens: int, pool: Any, body: Dict[str, Any], auth_key_prefix: str = "", account: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    t0 = asyncio.get_event_loop().time()
    text, tool_calls, finish_reason, thought_text = await _resolve_gemini_with_tools(kwargs, body, proxy_instance, auth_key_prefix=auth_key_prefix, account=account)
    elapsed = asyncio.get_event_loop().time() - t0
    logger.info("[NonStream] model=%s elapsed=%.2fs tools=%d text_len=%d",
                model_alias, elapsed, len(tool_calls), len(text))

    router.record_success(api_key, model_id)
    if pool:
        pool.record_success()

    if not text and not tool_calls:
        logger.warning("[Empty Response] Model returned completely empty text and no tools. Finish reason: %s", finish_reason)
        text = "..."

    warning_message = ""
    is_sub = is_sub_agent_body(body)
    warning_threshold = 178000 if is_claude_code_body(body) else 170000
    if input_tokens > warning_threshold and not is_sub:
        warning_message = (
            "\n⚠️  [ROUTER-API WARNING] Context is extremely large (%.1fk tokens). "
            "Please run '/compact' in your terminal immediately to avoid 250k TPM rate limits! ⚠️\n"
            "⚠️  [CẢNH BÁO] Context hiện tại cực kỳ lớn (%.1fk tokens). "
            "Vui lòng chạy lệnh '/compact' ngay lập tức để tránh bị lỗi giới hạn 250k TPM! ⚠️\n\n"
        ) % (input_tokens / 1000.0, input_tokens / 1000.0)
        text = warning_message + text

    # Strip <think>/<thinking> XML tags from text, extract as thinking
    if text:
        _extractor = XMLThinkingExtractor()
        _events = _extractor.feed(text) + _extractor.flush()
        _extracted = ""
        _clean = []
        for _et, _ev in _events:
            if _et == "thinking":
                _extracted += _ev
            elif _et == "text":
                _clean.append(_ev)
        if _extracted:
            if not thought_text:
                thought_text = _extracted
            text = "".join(_clean) if _clean else ""

    content_blocks = []
    if thought_text:
        norm_thought = normalize_text(thought_text)
        sig = "gmni_" + hashlib.sha256(norm_thought.encode()).hexdigest()[:60]
        content_blocks.append({"type": "thinking", "thinking": norm_thought, "signature": sig})
    if text:
        content_blocks.append({"type": "text", "text": normalize_text(text)})
    for tc in tool_calls:
        try:
            args = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
        except Exception:
            args = {}
        if tc["name"] == "Task":
            content_blocks.append({
                "type": "agent_use",
                "id": tc["id"],
                "agent_type": "general-purpose",
                "prompt": args.get("prompt", "") or str(args)
            })
        else:
            content_blocks.append({
                "type": "tool_use", "id": tc["id"], "name": tc["name"], "input": args,
            })

    finish_str = "tool_use" if tool_calls else ("length" if "max" in str(finish_reason).lower() else "end_turn")
    try:
        out_tokens = await token_counter(
            model=kwargs.get("model", "gemini/gemini-1.5-pro"),
            messages=[{"role": "assistant", "content": text}]
        )
    except Exception:
        out_tokens = max(1, len(text) // 4) if text else 1

    adjusted_input_tokens = input_tokens
    cache_usage = _get_simulated_cache_usage(body, adjusted_input_tokens)
    cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
    cr = cache_usage.get("cache_read_input_tokens", 0) or 0
    await log_usage(model_id, (api_key or "")[-8:], input_tokens, out_tokens, auth_key_prefix, cc, cr)

    client_input_tokens = max(1, adjusted_input_tokens - cc - cr)

    return {
        "id": "msg_" + uuid.uuid4().hex,
        "type": "message",
        "role": "assistant",
        "model": body.get("model") or model_alias,
        "content": content_blocks,
        "stop_reason": finish_str,
        "stop_sequence": None,
        "usage": {
            "input_tokens": client_input_tokens,
            "output_tokens": out_tokens,
            **cache_usage
        },
    }

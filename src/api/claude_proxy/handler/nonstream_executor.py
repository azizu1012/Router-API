import asyncio
import json
import uuid
from typing import Any, Dict, List, Tuple, Optional

import litellm
from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.usage_logger import log_usage
from src.api.claude_proxy.utils import (
    _resolve_model,
    _get_simulated_cache_usage,
    is_sub_agent_body,
    is_claude_code_body,
)

async def _resolve_gemini_with_tools(kwargs: Dict[str, Any], body: Dict[str, Any], proxy_instance: Any, auth_key_prefix: str = "", account: Optional[Dict[str, Any]] = None) -> Tuple[str, List[Dict[str, Any]], str]:
    try:
        kwargs["stream"] = True
        gen = await litellm.acompletion(**kwargs)
    except Exception as e:
        logger.error("[_resolve_gemini_with_tools] litellm.acompletion error: %s", e, exc_info=True)
        raise

    text_buf = []
    tool_call_buf = {}
    finish_reason = "stop"

    async for chunk in gen:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        finish = chunk.choices[0].finish_reason

        if getattr(delta, "content", None):
            text_buf.append(delta.content)
        if getattr(delta, "tool_calls", None):
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_call_buf:
                    tool_call_buf[idx] = {"id": getattr(tc, "id", f"call_{uuid.uuid4().hex}"), "name": "", "arguments": ""}
                if getattr(tc.function, "name", None):
                    tool_call_buf[idx]["name"] = tc.function.name
                args_val = getattr(tc.function, "arguments", None)
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
    tool_calls = list(tool_call_buf.values())

    web_call = next((tc for tc in tool_calls if tc.get("name") == "WebSearch"), None)
    if web_call:
        try:
            args = json.loads(web_call["arguments"]) if isinstance(web_call["arguments"], str) else web_call["arguments"]
            query = args.get("query", "")
            logger.info("[WebSearch] executing query=%r model=%s", query[:160], kwargs.get("model", "-"))
            
            from src.core.providers.search_manager import execute_hybrid_search
            search_context, combined_citations = await execute_hybrid_search([query], auth_key_prefix=auth_key_prefix, account=account)
            
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

        return await _resolve_gemini_with_tools(new_kwargs, body, proxy_instance, auth_key_prefix=auth_key_prefix, account=account)

    return text, tool_calls, finish_reason

async def _execute_nonstream(proxy_instance: Any, kwargs: Dict[str, Any], api_key: str, model_id: str, model_alias: str, input_tokens: int, pool: Any, body: Dict[str, Any], auth_key_prefix: str = "", account: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    t0 = asyncio.get_event_loop().time()
    text, tool_calls, finish_reason = await _resolve_gemini_with_tools(kwargs, body, proxy_instance, auth_key_prefix=auth_key_prefix, account=account)
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

    content_blocks = []
    if text:
        content_blocks.append({"type": "text", "text": text})
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
        out_tokens = await asyncio.to_thread(
            litellm.token_counter,
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

    return {
        "id": "msg_" + uuid.uuid4().hex,
        "type": "message",
        "role": "assistant",
        "model": body.get("model") or model_alias,
        "content": content_blocks,
        "stop_reason": finish_str,
        "stop_sequence": None,
        "usage": {
            "input_tokens": adjusted_input_tokens,
            "output_tokens": out_tokens,
            **cache_usage
        },
    }

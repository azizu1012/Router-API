import asyncio
import json
import uuid
import time
from typing import Any, Dict, List, Tuple, Optional

from src.core.providers.litellm_wrapper import acompletion, token_counter
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.usage_logger import log_usage
from src.api.claude_proxy.utils import _get_simulated_cache_usage, normalize_text


async def _resolve_gemini_with_tools_stream(
    kwargs: Dict[str, Any],
    body: Dict[str, Any],
    proxy_instance: Any,
    auth_key_prefix: str = "",
    account: Optional[Dict[str, Any]] = None,
    _recursion_depth: int = 0,
):
    """Async generator — yields real-time (type, data) tuples from LiteLLM stream.

    Types:
      ("reasoning", str)   — thinking/reasoning content
      ("text", str)        — visible text content
      ("result", text, tool_calls, finish_reason, thought_text) — final accumulated data
    """
    try:
        kwargs["stream"] = True
        gen = await acompletion(**kwargs)
    except Exception as e:
        logger.error("[OpenCode _resolve_gemini_with_tools_stream] litellm.acompletion error: %s", e, exc_info=True)
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
        rc = getattr(delta, "reasoning_content", None) or getattr(delta, "thought", None) or getattr(delta, "reasoning", None)

        if content:
            text_buf.append(content)
            yield ("text", content)
        if rc:
            thought_buf.append(rc)
            yield ("reasoning", rc)
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
    thought_text = "".join(thought_buf)
    tool_calls = list(tool_call_buf.values())

    if _recursion_depth >= 3:
        logger.warning("[OpenCode ToolRecursion] Max recursion depth reached (3), returning tool calls as-is")
        yield ("result", text, tool_calls, finish_reason, thought_text)
        return

    web_call = next((tc for tc in tool_calls if tc.get("name") == "WebSearch"), None)
    if web_call:
        try:
            args = json.loads(web_call["arguments"]) if isinstance(web_call["arguments"], str) else web_call["arguments"]
            query = args.get("query", "")
            logger.info("[OpenCode WebSearch] executing query=%r model=%s", query[:160], kwargs.get("model", "-"))

            from .search import execute_opencode_search
            search_context, combined_citations = await execute_opencode_search(
                [query], model_alias_or_name=kwargs.get("model", "-"),
                auth_key_prefix=auth_key_prefix, account=account
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
                result = "\n".join(result_lines)
            else:
                result = "No search results found."
        except Exception as e:
            result = f"Search error: {e}"
            logger.warning("[OpenCode WebSearch] query failed: %s", e)

        assistant_msg = {
            "role": "assistant",
            "content": text or None,
            "tool_calls": [{"id": web_call["id"], "type": "function", "function": {"name": "WebSearch", "arguments": web_call["arguments"]}}]
        }
        tool_result_msg = {"role": "tool", "tool_call_id": web_call["id"], "content": result}

        new_kwargs = dict(kwargs)
        new_kwargs["messages"] = list(kwargs["messages"]) + [assistant_msg, tool_result_msg]

        async for evt in _resolve_gemini_with_tools_stream(
            new_kwargs, body, proxy_instance,
            auth_key_prefix=auth_key_prefix, account=account,
            _recursion_depth=_recursion_depth + 1
        ):
            yield evt
        return

    yield ("result", text, tool_calls, finish_reason, thought_text)


async def _resolve_gemini_with_tools(
    kwargs: Dict[str, Any],
    body: Dict[str, Any],
    proxy_instance: Any,
    auth_key_prefix: str = "",
    account: Optional[Dict[str, Any]] = None,
    _recursion_depth: int = 0,
) -> Tuple[str, List[Dict[str, Any]], str, str]:
    try:
        kwargs["stream"] = False
        resp = await acompletion(**kwargs)
    except Exception as e:
        logger.error("[_resolve_gemini_with_tools] litellm.acompletion error: %s", e, exc_info=True)
        raise

    choice = resp.choices[0] if resp.choices else None
    if not choice:
        return "", [], "stop", ""

    msg = choice.message
    text = getattr(msg, "content", "") or ""
    finish_reason = getattr(choice, "finish_reason", "stop") or "stop"
    thought_text = getattr(msg, "reasoning_content", None) or getattr(msg, "thinking", None) or ""

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

    if _recursion_depth >= 3:
        logger.warning("[OpenCode ToolRecursion] Max recursion depth reached (3), returning tool calls as-is")
        return text, tool_calls, finish_reason, thought_text

    web_call = next((tc for tc in tool_calls if tc.get("name") == "WebSearch"), None)
    if web_call:
        try:
            args = json.loads(web_call["arguments"]) if isinstance(web_call["arguments"], str) else web_call["arguments"]
            query = args.get("query", "")
            logger.info("[OpenCode WebSearch] executing query=%r model=%s", query[:160], kwargs.get("model", "-"))

            from .search import execute_opencode_search
            search_context, combined_citations = await execute_opencode_search([query], model_alias_or_name=kwargs.get("model", "-"), auth_key_prefix=auth_key_prefix, account=account)

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
            logger.warning("[OpenCode WebSearch] query failed: %s", e)

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

    return text, tool_calls, finish_reason, thought_text


async def _execute_nonstream(
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
) -> Dict[str, Any]:
    t0 = asyncio.get_event_loop().time()
    text, tool_calls, finish_reason, thought_text = await _resolve_gemini_with_tools(
        kwargs, body, proxy_instance, auth_key_prefix=auth_key_prefix, account=account
    )
    elapsed = asyncio.get_event_loop().time() - t0

    logger.info(
        "[_execute_nonstream] model=%s elapsed=%.2fs text_len=%d tools=%d",
        model_alias, elapsed, len(text), len(tool_calls)
    )

    try:
        out_tokens = await token_counter(model=kwargs.get("model", "gemini/gemini-1.5-flash"), messages=[{"role": "assistant", "content": text}])
    except Exception:
        out_tokens = max(1, len(text) // 4) if text else 1
    out_tokens += len(tool_calls) * 50

    cost = proxy_instance._estimate_cost(input_tokens, out_tokens, model_alias)
    kp = api_key[-8:] if api_key else ""
    cache_usage = _get_simulated_cache_usage(body, input_tokens)
    cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
    cr = cache_usage.get("cache_read_input_tokens", 0) or 0

    await log_usage(model_id, kp, input_tokens, out_tokens, auth_key_prefix, cc, cr)
    router.record_success(api_key, model_id, input_tokens, out_tokens)
    if pool:
        pool.record_success()

    from .response import get_client_model_name
    requested_model = body.get("model") or model_alias
    model_name = get_client_model_name(requested_model)

    content_val = normalize_text(text) if text else ""
    if thought_text:
        thought_norm = normalize_text(thought_text)
        content_val = f"<think>\n{thought_norm}\n</think>\n\n{content_val}"

    message_content: Dict[str, Any] = {"role": "assistant", "content": content_val if content_val else None}
    if thought_text:
        message_content["reasoning_content"] = normalize_text(thought_text)
    if tool_calls:
        message_content["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["arguments"]
                }
            }
            for tc in tool_calls
        ]

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": message_content,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": out_tokens,
            "total_tokens": input_tokens + out_tokens,
            "cost": cost,
        },
    }

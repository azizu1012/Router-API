"""Claude stream proxy — format converter only.

Pool/key/custom management is centralized in PoolManager.
"""

import json
import uuid
from typing import Any, Dict, Optional, AsyncIterator

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.pool_manager import pool_manager
from src.logical_HQ_translator import (
    _convert_messages,
    _intercept_sub_agent,
    _dict_to_sse_events,
    _sse,
)
from .compaction import _pre_compact_and_truncate
from .helpers import get_system_status_summary


class ClaudeProxyStreamMixin:

    async def stream_message(
        self, body: Dict[str, Any], auth_key_prefix: str = "", account: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[bytes]:
        openai_messages, openai_tools = _convert_messages(body)

        from src.api.opencode_proxy.handler.websearch import should_enable_web_search
        from src.api.opencode_proxy.handler.proxy import _WEBSEARCH_TOOL_DEF, _resolve_thinking_config
        from src.logical_HQ_translator.sse_cache_agent import is_sub_agent_body
        if not is_sub_agent_body(body) and should_enable_web_search(body, account) and not any(
            t.get("function", {}).get("name") in ("WebSearch", "web_search") for t in openai_tools
        ):
            openai_tools.append(_WEBSEARCH_TOOL_DEF)
            logger.info("[WebSearch] Injected WebSearch tool for Claude stream")

        override_alias = _intercept_sub_agent(body)
        model_alias = override_alias or router.resolve_model_alias(body.get("model", "")) or config.DEFAULT_MODEL_ALIAS
        await _pre_compact_and_truncate(body, openai_messages, openai_tools, model_alias)

        yield _sse("ping", {"type": "ping", "retry": 0, "reason": "initial"})

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
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            })

            text_started = False
            thinking_started = False
            text_index = 0
            thinking_index = 0
            output_chars = 0
            input_tokens = 0
            finish_reason = "end_turn"
            tool_buffers: Dict[int, Dict[str, Any]] = {}

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
                chunk = item["chunk"]
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                fr = chunk.choices[0].finish_reason
                content = getattr(delta, "content", None)
                reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "thought", None)

                if reasoning:
                    if not thinking_started:
                        thinking_started = True
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
                    if not text_started:
                        text_started = True
                        text_index = 1 if thinking_started else 0
                        if thinking_started:
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
                        "delta": {"type": "text_delta", "text": content},
                    })
                    output_chars += len(content)

                tool_calls_val = getattr(delta, "tool_calls", None) or delta.get("tool_calls") if hasattr(delta, "get") else None
                if tool_calls_val:
                    for tc in tool_calls_val:
                        if isinstance(tc, dict):
                            tc_idx = tc.get("index", 0)
                            tc_id = tc.get("id", f"toolu_{uuid.uuid4().hex}")
                            fn = tc.get("function", {})
                            fn_name = fn.get("name", "") if isinstance(fn, dict) else (getattr(fn, "name", "") if hasattr(fn, "name") else "")
                            fn_args = fn.get("arguments") if isinstance(fn, dict) else (getattr(fn, "arguments", None) if hasattr(fn, "arguments") else None)
                        else:
                            tc_idx = getattr(tc, "index", 0)
                            tc_id = getattr(tc, "id", f"toolu_{uuid.uuid4().hex}")
                            fn = getattr(tc, "function", {}) if hasattr(tc, "function") else {}
                            fn_name = getattr(fn, "name", "") if hasattr(fn, "name") else ""
                            fn_args = getattr(fn, "arguments", None) if hasattr(fn, "arguments") else None
                        if tc_idx not in tool_buffers:
                            tool_buffers[tc_idx] = {"id": tc_id, "name": fn_name, "args": ""}
                        if fn_name:
                            tool_buffers[tc_idx]["name"] = fn_name
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

            if thinking_started and not text_started:
                yield _sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": thinking_index,
                    "delta": {"type": "signature_delta", "signature": ""},
                })
                yield _sse("content_block_stop", {"type": "content_block_stop", "index": thinking_index})
            if text_started:
                yield _sse("content_block_stop", {"type": "content_block_stop", "index": text_index})

            if tool_buffers:
                finish_reason = "tool_use"
                next_block_idx = (1 if thinking_started else 0) + (1 if text_started else 0)
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
            logger.error("[Claude Stream] PoolManager failed: %s", e, exc_info=True)
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

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
from src.logical_HQ_translator import _convert_messages, _intercept_sub_agent, XMLThinkingExtractor
from .compaction import _pre_compact_and_truncate
from .helpers import get_system_status_summary


class ClaudeProxyNonstreamMixin:

    async def create_message(
        self, body: Dict[str, Any], auth_key_prefix: str = "", account: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        openai_messages, openai_tools = _convert_messages(body)

        from src.api.opencode_proxy.handler.websearch import should_enable_web_search
        from src.api.opencode_proxy.handler.proxy import _WEBSEARCH_TOOL_DEF, _resolve_thinking_config
        from src.logical_HQ_translator.sse_cache_agent import is_sub_agent_body
        if not is_sub_agent_body(body) and should_enable_web_search(body, account) and not any(
            t.get("function", {}).get("name") in ("WebSearch", "web_search") for t in openai_tools
        ):
            openai_tools.append(_WEBSEARCH_TOOL_DEF)
            logger.info("[WebSearch] Injected WebSearch tool for Claude non-stream")

        override_alias = _intercept_sub_agent(body)
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
        except Exception as e:
            logger.error("[Claude NonStream] PoolManager failed: %s", e, exc_info=True)
            summary_text = get_system_status_summary(model_alias)
            return {
                "id": "msg_err_" + uuid.uuid4().hex[:8],
                "type": "message",
                "role": "assistant",
                "model": body.get("model") or model_alias,
                "content": [{"type": "text", "text": summary_text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": len(summary_text) // 4, "output_tokens": len(summary_text) // 4},
            }

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
                    if name == "Task":
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

        return {
            "id": "msg_" + uuid.uuid4().hex[:24],
            "type": "message",
            "role": "assistant",
            "model": body.get("model") or model_alias,
            "content": content_blocks,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }

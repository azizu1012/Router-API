import asyncio
import json
import uuid
import time
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

import litellm
from fastapi import HTTPException

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.limits import apply_error_penalty
from src.core.usage_logger import log_usage
from src.api.claude_proxy.utils import (
    _resolve_model,
    _retry_delay,
    _emergency_truncate_to_limit,
    _get_simulated_cache_usage,
)
from src.api.claude_proxy.handler.helpers import (
    get_system_status_summary,
    _classify_error_reason,
    _reinforce_messages_for_retry,
)

from .detection import detect_sub_agent_override
from .nonstream_executor import _execute_nonstream
from .stream_executor import _stream_with_pool, _stream_standalone, LiteLLMTransientError
from src.api.claude_proxy.utils import _sanitize_schema_for_gemini

_TODO_INSTRUCTION = (
    "## Task Management\n"
    "- When given a complex task (3+ steps or touching multiple files), "
    "create a TODO list to break it down.\n"
    "- Complete one step at a time and mark it done before moving to the next.\n"
    "- For simple tasks (1-2 steps), skip the TODO list.\n"
)


def _inject_todo_instruction(messages: list) -> list:
    for m in messages:
        role = m.get("role", "")
        if role == "system":
            existing = str(m.get("content", ""))
            m["content"] = existing + "\n\n" + _TODO_INSTRUCTION
            return messages
    messages.insert(0, {"role": "system", "content": _TODO_INSTRUCTION.strip()})
    return messages


def get_client_model_name(requested_model: str) -> str:
    # Trả về requested_model trực tiếp để OpenCode khớp chính xác cấu hình models trong opencode.json
    return requested_model



def _classify_error_reason_static(error_text: str, api_key_val: Optional[str], model_id_val: Optional[str]) -> str:
    """Phiên bản static của _classify_error_reason để dùng trong stream_executor."""
    return _classify_error_reason(error_text, api_key_val, model_id_val)


def _get_auth_key_prefix(account: Optional[Dict[str, Any]]) -> str:
    if not account:
        return ""
    ak = account.get("auth_key") or ""
    return ak[-8:] if len(ak) >= 8 else ak


class OpenCodeProxy:

    async def _resolve_alias(self, body: Dict[str, Any], account: Optional[Dict[str, Any]] = None, is_opencode: bool = False) -> str:
        override = detect_sub_agent_override(body, account=account, is_opencode=is_opencode)
        model_alias = override or router.resolve_model_alias(body.get("model", ""))
        if not model_alias:
            model_alias = config.DEFAULT_MODEL_ALIAS
        return model_alias

    def _should_enable_web_search(self, body: Dict[str, Any], account: Optional[Dict[str, Any]]) -> bool:
        # Respect explicit client-level disable override
        for flag in ["web_search", "search", "google_search", "grounding"]:
            if flag in body and body[flag] is False:
                return False
        return bool(
            body.get("web_search") or body.get("search")
            or body.get("google_search") or body.get("grounding")
            or config.GEMINI_AUTO_GROUNDING
            or (account and account.get("web_search_enabled"))
        )

    # ── Entry Points ─────────────────────────────────────────────

    async def chat_completion(self, body: Dict[str, Any], account: Optional[Dict[str, Any]] = None, is_opencode: bool = False) -> Dict[str, Any]:
        model_alias = await self._resolve_alias(body, account=account, is_opencode=is_opencode)
        messages, tools = body.get("messages", []), list(body.get("tools", []))
        
        web_search = self._should_enable_web_search(body, account)
        if web_search and not any(t.get("function", {}).get("name") == "WebSearch" for t in tools):
            websearch_tool = {
                "type": "function",
                "function": {
                    "name": "WebSearch",
                    "description": "Search the web via DuckDuckGo (free, no API key). Use ONLY when information is uncertain/unfamiliar OR user explicitly requests internet search. Results may contain untrusted code/logic — PRESENT to user for review, do NOT auto-apply.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "The search query. Be specific and concise."},
                            "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "Only include results from these domains (optional)."},
                            "blocked_domains": {"type": "array", "items": {"type": "string"}, "description": "Exclude results from these domains (optional)."}
                        },
                        "required": ["query"]
                    }
                }
            }
            tools.append(websearch_tool)

        # No auto-compaction for OpenCode yet
        input_tokens = 0


    async def stream_chat_completion(self, body: Dict[str, Any], account: Optional[Dict[str, Any]] = None, is_opencode: bool = False) -> AsyncIterator[bytes]:
        model_alias = await self._resolve_alias(body, account=account, is_opencode=is_opencode)
        model_name = body.get("model") or model_alias
        messages, tools = body.get("messages", []), list(body.get("tools", []))
        
        web_search = self._should_enable_web_search(body, account)
        if web_search and not any(t.get("function", {}).get("name") == "WebSearch" for t in tools):
            websearch_tool = {
                "type": "function",
                "function": {
                    "name": "WebSearch",
                    "description": "Search the web via DuckDuckGo (free, no API key). Use ONLY when information is uncertain/unfamiliar OR user explicitly requests internet search. Results may contain untrusted code/logic — PRESENT to user for review, do NOT auto-apply.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "The search query. Be specific and concise."},
                            "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "Only include results from these domains (optional)."},
                            "blocked_domains": {"type": "array", "items": {"type": "string"}, "description": "Exclude results from these domains (optional)."}
                        },
                        "required": ["query"]
                    }
                }
            }
            tools.append(websearch_tool)

        # No auto-compaction for OpenCode yet
        input_tokens = 0


    # ── Message Preprocessing ────────────────────────────────────
    # ── Message Preprocessing ────────────────────────────────────

    async def _truncate_to_limit(
        self, messages: List[Dict[str, Any]], model_alias: str
    ) -> tuple:
        is_lite = "lite" in str(model_alias).lower()
        limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
        messages = _emergency_truncate_to_limit(messages, limit)

        try:
            model_id = router.get_model_id(model_alias)
            input_tokens = await asyncio.to_thread(litellm.token_counter, model=f"gemini/{model_id}", messages=messages)
        except Exception:
            input_tokens = max(1, len(str(messages)) // 4)

        return messages, input_tokens



    async def _with_search_context(
        self, body: Dict[str, Any], messages: List[Dict[str, Any]], model_alias: str, account: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        if not messages:
            return messages

        web_search = self._should_enable_web_search(body, account)
        if not web_search:
            return messages

        prompt_chunks = []
        for m in messages:
            if m.get("role") in ("system", "developer"):
                continue
            c = m.get("content", "")
            if isinstance(c, str) and c.strip():
                prompt_chunks.append(c)
            elif isinstance(c, list):
                for p in c:
                    if isinstance(p, dict) and p.get("type") == "text":
                        text = str(p.get("text", "")).strip()
                        if text:
                            prompt_chunks.append(text)
        prompt_text = "\n".join(prompt_chunks)
        if not prompt_text.strip():
            return messages

        from src.core.providers.search_manager import extract_search_queries
        from .search import execute_opencode_search
        akp = _get_auth_key_prefix(account)
        try:
            last_user_msg = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    c = m.get("content", "")
                    if isinstance(c, str):
                        last_user_msg = c
                    elif isinstance(c, list):
                        texts = []
                        for p in c:
                            if isinstance(p, dict) and p.get("type") == "text":
                                texts.append(str(p.get("text", "")))
                        last_user_msg = "\n".join(texts)
                    break
            queries = await extract_search_queries(last_user_msg, messages, auth_key_prefix=akp, account=account) if last_user_msg else []
        except Exception as qerr:
            logger.warning("[OpenCode Search] extract_search_queries failed: %s", qerr)
            queries = []

        if not queries:
            return messages

        try:
            search_context, citations = await execute_opencode_search(queries, model_alias_or_name=model_alias, auth_key_prefix=akp, account=account)
        except Exception as serr:
            logger.warning("[OpenCode Search] execute_opencode_search failed: %s", serr)
            return messages

        if not search_context:
            return messages

        citations_block = ""
        if citations:
            seen = set()
            unique = []
            for c in citations:
                url = c.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    unique.append(c)
            if unique:
                citations_block = "\n\n— Sources —\n" + "\n".join(
                    f"• [{c.get('title', 'Source')}]({c.get('url', '#')})" for c in unique
                )

        current_time = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p")
        context_block = (
            "\n\n---\n"
            f"[Web Search Context — {current_time}]\n"
            "CRITICAL: Use the search results above to write a comprehensive, detailed response. "
            "Include all relevant dates, numbers, names. Do NOT summarize briefly — be exhaustive.\n"
            f"{search_context}\n"
            f"{citations_block}\n"
            "[/Web Search Context]"
        )

        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                existing = messages[i].get("content", "")
                if isinstance(existing, str):
                    messages[i] = {**messages[i], "content": existing + context_block}
                elif isinstance(existing, list):
                    messages[i] = {**messages[i], "content": list(existing) + [{"type": "text", "text": context_block}]}
                return messages

        messages.insert(0, {"role": "system", "content": context_block.strip()})
        return messages

    # ── Non-Stream ───────────────────────────────────────────────

    async def _call_nonstream(
        self, body: Dict[str, Any], messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
        model_alias: str, pool: Any, account: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if pool:
            return await self._nonstream_pool(body, messages, tools, pool, account=account)
        return await self._nonstream_standalone(body, messages, tools, model_alias, account=account)

    async def _nonstream_pool(
        self, body: Dict[str, Any], messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
        pool: Any, account: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        pool.start()
        while not pool.exhausted:
            api_key_val = None
            model_id_val = None
            try:
                resp, api_key_val, model_id_val, input_tokens = await self._resolve_and_call(body, messages, tools, pool.current_model, pool_mode=True, pool=pool, account=account, is_stream=False)
                saved_key = api_key_val
                api_key_val = None
                router.record_success(saved_key, model_id_val)
                pool.record_success()
                return self._build_response(body, resp, pool.current_model, saved_key, input_tokens)
            except HTTPException:
                raise
            except Exception as e:
                if not await self._classify_pool_error(e, pool, pool.current_model, api_key_val, model_id_val):
                    raise
                api_key_val = None

        return self._error_response(body, pool.current_model if pool.current_model else model_alias)

    async def _nonstream_standalone(
        self, body: Dict[str, Any], messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
        model_alias: str, account: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        last_error = None
        for attempt in range(config.MAX_RETRIES):
            api_key_val = None
            model_id_val = None
            try:
                resp, api_key_val, model_id_val, input_tokens = await self._resolve_and_call(body, messages, tools, model_alias, pool_mode=False, account=account, is_stream=False, attempt=attempt)
                saved_key = api_key_val
                api_key_val = None
                router.record_success(saved_key, model_id_val)
                return self._build_response(body, resp, model_id_val, saved_key, input_tokens)
            except HTTPException:
                raise
            except Exception as e:
                last_error = e
                if not self._classify_standalone_error(e, attempt, api_key_val, model_id_val):
                    break
                api_key_val = None
                await asyncio.sleep(_retry_delay(attempt))

        return self._error_response(body, model_alias)

    # ── Resolution + Call ─────────────────────────────────────────

    def _prepare_litellm_kwargs(
        self, litellm_model_val: str, reinforced_messages: List[Dict[str, Any]],
        api_key_val: str, max_output: int, body: Dict[str, Any],
        openai_tools: List[Dict[str, Any]], reservation: Dict[str, Any], is_stream: bool
    ) -> Dict[str, Any]:
        kwargs = {
            "model": litellm_model_val,
            "messages": reinforced_messages,
            "api_key": api_key_val,
            "max_tokens": max_output,
            "temperature": float(body.get("temperature", 0.7)),
            "stream": is_stream,
            "request_timeout": config.REQUEST_TIMEOUT_SECONDS,
        }
        if openai_tools:
            sanitized = []
            for tool in openai_tools:
                fn = tool.get("function", {})
                if fn.get("parameters"):
                    fn = {**fn, "parameters": _sanitize_schema_for_gemini(fn["parameters"])}
                sanitized.append({**tool, "function": fn})
            kwargs["tools"] = sanitized
        if reservation.get("provider") == "custom":
            kwargs["api_base"] = reservation["api_base"]
        return kwargs

    async def _resolve_and_call(
        self, body: Dict[str, Any], messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
        resolve_alias: str, pool_mode: bool = False, pool: Any = None,
        account: Optional[Dict[str, Any]] = None, is_stream: bool = False, attempt: int = 0
    ) -> tuple:
        max_output = min(int(body.get("max_tokens", config.MAX_OUTPUT_TOKENS)), config.MAX_OUTPUT_TOKENS)
        estimated_tokens = len(str(messages)) // 4 + max_output

        retry = pool.total_attempts if pool else attempt
        model_alias_val, model_id_val, api_key_val, litellm_model_val, reservation = await _resolve_model(
            body, resolve_alias, account=account, estimated_tokens=estimated_tokens,
            retry_attempt=retry, pool_mode=pool_mode
        )

        reinforced_messages = _reinforce_messages_for_retry(messages, retry)
        is_lite = "lite" in str(litellm_model_val).lower()
        limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
        if retry >= 10:
            _div = max(3, retry - 7)
            limit = max(20000, limit // _div)
        truncated = _emergency_truncate_to_limit(reinforced_messages, limit)

        try:
            input_tokens = await asyncio.to_thread(litellm.token_counter, model=litellm_model_val, messages=truncated)
        except Exception:
            input_tokens = max(1, len(str(truncated)) // 4)

        has_quota = await router.acquire_quota(input_tokens + max_output, resolve_alias)
        if not has_quota:
            apply_error_penalty(api_key_val, "rate_limit_rpm_tpm", model_id_val)
            router.freeze_key(api_key_val, 15, model_id_val, "rate_limit")
            raise HTTPException(status_code=429, detail={"error": {"message": "Rate limit", "type": "rate_limit_error"}})

        kwargs = self._prepare_litellm_kwargs(
            litellm_model_val=litellm_model_val,
            reinforced_messages=truncated,
            api_key_val=api_key_val,
            max_output=max_output,
            body=body,
            openai_tools=tools,
            reservation=reservation,
            is_stream=False,
        )

        # Trực tiếp gọi _execute_nonstream để xử lý WebSearch tool
        resp = await _execute_nonstream(
            self, kwargs, api_key_val, model_id_val, model_alias_val,
            input_tokens, pool, body, auth_key_prefix=_get_auth_key_prefix(account), account=account
        )
        return resp, api_key_val, model_id_val, input_tokens

    # ── Cost Estimation ──────────────────────────────────────────

    def _estimate_cost(self, input_tokens: int, output_tokens: int, model_alias: str) -> float:
        is_lite = "lite" in str(model_alias).lower()
        input_rate = 0.001 if is_lite else 0.0025
        output_rate = 0.004 if is_lite else 0.010

        try:
            from src.backend.model_prices import get_model_price
            cfg = get_model_price(model_alias)
            if not cfg:
                pool_key = "gemini-flash-lite" if is_lite else "gemini-flash"
                cfg = get_model_price(pool_key)
            if cfg:
                input_rate = float(cfg.get("input_rate_per_1k", input_rate))
                output_rate = float(cfg.get("output_rate_per_1k", output_rate))
        except Exception as e:
            logger.warning("[Cost Estimate] DB lookup failed: %s", e)

        return round(input_tokens * input_rate / 1000 + output_tokens * output_rate / 1000, 6)

    # ── Response Builders ────────────────────────────────────────

    def _build_response(self, body: Dict[str, Any], resp: Any, model_alias: str, api_key: str, input_tokens: int) -> Dict[str, Any]:
        # Nếu resp là dict (đã được format bởi _execute_nonstream), trả về luôn
        if isinstance(resp, dict):
            return resp

        choice = resp.choices[0] if resp.choices else None
        if not choice:
            text, finish = "", "stop"
        else:
            text = ""
            content = getattr(choice.message, "content", None)
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                text = "\n".join(parts)
            else:
                text = str(content or "")
            finish = getattr(choice, "finish_reason", "stop")

        out_tokens = 0
        usage = getattr(resp, "usage", None)
        if usage:
            out_tokens = getattr(usage, "completion_tokens", 0) or 0
            input_tokens = getattr(usage, "prompt_tokens", 0) or input_tokens

        cost = self._estimate_cost(input_tokens, out_tokens, model_alias)
        kp = api_key[-8:] if api_key else ""
        cache_usage = _get_simulated_cache_usage(body, input_tokens)
        cc = cache_usage.get("cache_creation_input_tokens", 0) or 0
        cr = cache_usage.get("cache_read_input_tokens", 0) or 0
        asyncio.ensure_future(log_usage(model_alias, kp, input_tokens, out_tokens, "", cc, cr))

        requested_model = body.get("model") or model_alias
        model_name = get_client_model_name(requested_model)
        
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish,
                }
            ],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": out_tokens,
                "total_tokens": input_tokens + out_tokens,
                "cost": cost,
            },
        }

    def _error_response(self, body: Dict[str, Any], model_name: str, reason: str = "pool_exhausted") -> Dict[str, Any]:
        text = get_system_status_summary(model_name, reason)
        mapped_model = get_client_model_name(model_name)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": mapped_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
        }

    # ── Error Classification ─────────────────────────────────────

    async def _classify_pool_error(self, e: Exception, pool: Any, actual_alias: str, api_key_val: Optional[str], model_id_val: Optional[str]) -> bool:
        if isinstance(e, LiteLLMTransientError):
            reason = "rate_limit"
            is_region_quota = e.is_region_quota
        else:
            error_text = str(e).lower()

            if "400" in error_text and "failed_precondition" not in error_text and ("invalid_argument" in error_text or "bad_request" in error_text):
                logger.error("[OpenCode Bad Request] %s", error_text[:200])
                if api_key_val:
                    router.freeze_key(api_key_val, 2, model_id_val, "bad_request_spam_prevent")
                raise HTTPException(status_code=400, detail={
                    "error": {"message": f"LLM rejected payload: {error_text[:200]}", "type": "invalid_request_error"}
                })

            if "400" in error_text and "failed_precondition" in error_text:
                logger.error("[OpenCode Billing] %s", error_text[:200])
                if api_key_val:
                    router.freeze_key(api_key_val, 300, model_id_val, "billing_error")
                    apply_error_penalty(api_key_val, "billing_error", model_id_val)
                router.record_failure("billing_error")
                pool.record_failure(actual_alias, "billing_error")
                if not pool.swap():
                    if pool.exhausted:
                        raise HTTPException(status_code=503, detail={"error": {"message": "Pool exhausted", "type": "api_error"}})
                    await asyncio.sleep(min(15.0, pool.remaining_time()))
                    pool.reset_cycle()
                return True

            if "499" in error_text or "cancelled" in error_text:
                raise HTTPException(status_code=503, detail={
                    "error": {"message": "Request cancelled by client", "type": "api_error"}
                })

            is_region_quota = "apirequestsperminuteperprojectperregion" in error_text or "api_requests_per_minute_per_project_per_region" in error_text
            reason = _classify_error_reason(error_text, api_key_val, model_id_val)

        if reason == "rate_limit":
            router.record_429()
        if reason == "unknown_error":
            logger.error("[OpenCode Pool Error] key=...%s model=%s: %s", (api_key_val or "N/A")[-4:], actual_alias, e)

        if api_key_val:
            router.freeze_key(api_key_val, 0, model_id_val, reason)
            if reason not in ("bad_request_spam_prevent", "invalid_key"):
                apply_error_penalty(api_key_val, reason, model_id_val)
        router.record_failure(reason)
        logger.warning("[OpenCode Pool Retry] key=...%s model=%s reason=%s region_quota=%s", (api_key_val or "N/A")[-4:], actual_alias, reason, is_region_quota)

        pool.record_failure(actual_alias, reason)
        if not pool.swap():
            if pool.exhausted:
                raise HTTPException(status_code=503, detail={"error": {"message": "Pool exhausted", "type": "api_error"}})
            if is_region_quota:
                await asyncio.sleep(25)
            wait = min(15.0, pool.remaining_time())
            await asyncio.sleep(wait)
            pool.reset_cycle()
            return True

        if is_region_quota:
            logger.warning("[Region Quota] Hit region limit, waiting 25s before retry...")
            await asyncio.sleep(25)

        return True

    def _classify_standalone_error(self, e: Exception, attempt: int, api_key_val: Optional[str], model_id_val: Optional[str]) -> bool:
        error_text = str(e).lower()

        if "400" in error_text and "failed_precondition" not in error_text and ("invalid_argument" in error_text or "bad_request" in error_text):
            logger.error("[OpenCode Bad Request] %s", error_text[:200])
            if api_key_val:
                router.freeze_key(api_key_val, 2, model_id_val, "bad_request_spam_prevent")
            return False

        if "499" in error_text or "cancelled" in error_text:
            return False

        reason = _classify_error_reason(error_text, api_key_val, model_id_val)
        if reason == "rate_limit":
            router.record_429()
        if reason == "unknown_error":
            logger.error("[OpenCode Error] key=...%s: %s", (api_key_val or "N/A")[-4:], e)

        if api_key_val:
            router.freeze_key(api_key_val, 0, model_id_val, reason)
            if reason not in ("bad_request_spam_prevent", "invalid_key"):
                apply_error_penalty(api_key_val, reason, model_id_val)
        router.record_failure(reason)
        logger.warning("[OpenCode Retry] key=...%s attempt=%d/%d reason=%s", (api_key_val or "N/A")[-4:], attempt + 1, config.MAX_RETRIES, reason)

        if attempt >= config.MAX_RETRIES - 1:
            return False
        return True


opencode_proxy = OpenCodeProxy()

"""OpenCode proxy — routes requests to Gemini via LiteLLM with retry, web search, and sub-agent detection.

Orchestrates the request lifecycle:
  1. Resolve model alias (with sub-agent model override)
  2. Inject web search context
  3. Resolve key, call Gemini (pool or standalone)
  4. Build response, log usage
  5. Handle errors with pool swap / retry
"""

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

from src.core.providers.litellm_wrapper import token_counter
from fastapi import HTTPException

from src.core.config_n_logg import config
from src.core.router import router
from src.api.claude_proxy.utils import (
    _resolve_model,
    _retry_delay,
    _emergency_truncate_to_limit,
    _sanitize_schema_for_gemini,
)
from src.api.claude_proxy.handler.helpers import _reinforce_messages_for_retry

from .detection import detect_sub_agent_override
from .websearch import (
    should_enable_web_search,
    get_auth_key_prefix,
)
from .response import build_response, error_response
from . import error as ocerror
from .nonstream_executor import _execute_nonstream
from .stream_executor import _stream_with_pool, _stream_standalone


def _is_gemini_v3(litellm_model: str) -> bool:
    m = litellm_model.lower()
    return "gemini-3" in m and "gemini-2" not in m


def _clean_kwargs_for_model(kwargs: Dict[str, Any], litellm_model: str) -> Dict[str, Any]:
    if _is_gemini_v3(litellm_model):
        kwargs.pop("temperature", None)
        kwargs.pop("top_p", None)
    return kwargs


def _build_litellm_thinking(body: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    m = model_id.lower()
    is_v3 = _is_gemini_v3(m)

    thinking_level = body.get("thinking_level")
    thinking_budget = body.get("thinking_budget")
    include_thoughts = body.get("include_thoughts", False)

    if thinking_level is None and thinking_budget is None and not include_thoughts:
        if is_v3:
            return {"reasoning_effort": "medium"}
        budget = 32768 if "pro" in m else 24576
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}

    # 1. If V3 model, translate all thinking requests to reasoning_effort
    if is_v3:
        if thinking_level is not None:
            return {"reasoning_effort": thinking_level}
        return {"reasoning_effort": "medium"}

    # 2. V2.5 thinking_level overrides
    if thinking_level is not None:
        budget_map = {"low": 1024, "medium": 2048, "high": 4096}
        return {"thinking": {"type": "enabled", "budget_tokens": budget_map.get(thinking_level, 2048)}}

    # 3. V2.5 thinking_budget / include_thoughts
    d: Dict[str, Any] = {}
    budget = thinking_budget
    if budget == -1 or (budget is None and include_thoughts):
        budget = 32768 if "pro" in m else 24576
    if budget is not None:
        d["budget_tokens"] = budget
    if include_thoughts:
        d["include_thoughts"] = True
    if d:
        d["type"] = "enabled"
        return {"thinking": d}
    return {}


class OpenCodeProxy:
    """Orchestrates OpenCode chat completion requests."""

    def _estimate_cost(self, input_tokens: int, output_tokens: int, model_alias: str) -> float:
        from .response import estimate_cost
        return estimate_cost(input_tokens, output_tokens, model_alias)

    def _error_response(self, body: Dict[str, Any], model_name: str, reason: str = "pool_exhausted") -> Dict[str, Any]:
        from .response import error_response
        return error_response(body, model_name, reason)

    async def _resolve_alias(
        self, body: Dict[str, Any], account: Optional[Dict[str, Any]] = None, is_opencode: bool = False
    ) -> str:
        """Resolve model alias, checking sub-agent override first."""
        override = detect_sub_agent_override(body, account=account, is_opencode=is_opencode)
        model_alias = override or router.resolve_model_alias(body.get("model", ""))
        return model_alias or config.DEFAULT_MODEL_ALIAS

    # ── Entry Points ─────────────────────────────────────────────

    async def chat_completion(
        self, body: Dict[str, Any], account: Optional[Dict[str, Any]] = None, is_opencode: bool = False
    ) -> Dict[str, Any]:
        model_alias = await self._resolve_alias(body, account=account, is_opencode=is_opencode)
        messages, tools = body.get("messages", []), list(body.get("tools", []))
        messages, tools = _inject_websearch_tool(body, messages, tools, account)

        pool = router.resolve_pool(model_alias)
        if pool:
            return await self._nonstream_pool(body, messages, tools, pool, model_alias=model_alias, account=account)
        return await self._nonstream_standalone(body, messages, tools, model_alias, account=account)


    async def stream_chat_completion(
        self, body: Dict[str, Any], account: Optional[Dict[str, Any]] = None, is_opencode: bool = False
    ) -> AsyncIterator[bytes]:
        model_alias = await self._resolve_alias(body, account=account, is_opencode=is_opencode)
        messages, tools = body.get("messages", []), list(body.get("tools", []))
        messages, tools = _inject_websearch_tool(body, messages, tools, account)

        pool = router.resolve_pool(model_alias)
        if pool:
            async for chunk in _stream_with_pool(
                self, body, messages, tools, pool, model_alias,
                auth_key_prefix=get_auth_key_prefix(account), account=account,
                is_opencode=is_opencode
            ):
                yield chunk
        else:
            async for chunk in _stream_standalone(
                self, body, messages, tools, model_alias,
                auth_key_prefix=get_auth_key_prefix(account), account=account,
                is_opencode=is_opencode
            ):
                yield chunk


    # ── Message Processing ───────────────────────────────────────

    async def _truncate_to_limit(self, messages: List[Dict[str, Any]], model_alias: str) -> tuple:
        is_lite = "lite" in str(model_alias).lower()
        limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
        messages = _emergency_truncate_to_limit(messages, limit)
        try:
            model_id = router.get_model_id(model_alias)
            input_tokens = await token_counter(model=f"gemini/{model_id}", messages=messages)
        except Exception:
            input_tokens = max(1, len(str(messages)) // 4)
        return messages, input_tokens

    # ── Non-Stream ───────────────────────────────────────────────

    async def _call_nonstream(
        self, body: Dict[str, Any], messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
        model_alias: str, pool: Any, account: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if pool:
            return await self._nonstream_pool(body, messages, tools, pool, model_alias=model_alias, account=account)
        return await self._nonstream_standalone(body, messages, tools, model_alias, account=account)

    async def _nonstream_pool(
        self, body: Dict[str, Any], messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
        pool: Any, model_alias: str = "", account: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        pool.start()
        while not pool.exhausted:
            api_key_val = None
            model_id_val = None
            try:
                resp, api_key_val, model_id_val, input_tokens = await self._resolve_and_call(
                    body, messages, tools, pool.current_model,
                    pool_mode=True, pool=pool, account=account, is_stream=False,
                )
                saved_key = api_key_val
                api_key_val = None
                router.record_success(saved_key, model_id_val)
                pool.record_success()
                return build_response(body, resp, pool.current_model, saved_key, input_tokens)
            except HTTPException:
                raise
            except Exception as e:
                if not await ocerror.classify_pool_error(e, pool, pool.current_model, api_key_val, model_id_val):
                    raise
                api_key_val = None
        return error_response(body, pool.current_model if pool.current_model else model_alias)

    async def _nonstream_standalone(
        self, body: Dict[str, Any], messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
        model_alias: str, account: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        last_error = None
        for attempt in range(config.MAX_RETRIES):
            api_key_val = None
            model_id_val = None
            try:
                resp, api_key_val, model_id_val, input_tokens = await self._resolve_and_call(
                    body, messages, tools, model_alias,
                    pool_mode=False, account=account, is_stream=False, attempt=attempt,
                )
                saved_key = api_key_val
                api_key_val = None
                router.record_success(saved_key, model_id_val)
                return build_response(body, resp, model_id_val, saved_key, input_tokens)
            except HTTPException:
                raise
            except Exception as e:
                last_error = e
                if not ocerror.classify_standalone_error(e, attempt, api_key_val, model_id_val):
                    break
                api_key_val = None
                await asyncio.sleep(_retry_delay(attempt))
        return error_response(body, model_alias)

    # ── Key Resolution + Call ────────────────────────────────────

    def _prepare_litellm_kwargs(
        self, litellm_model_val: str, reinforced_messages: List[Dict[str, Any]],
        api_key_val: str, max_output: int, body: Dict[str, Any],
        openai_tools: List[Dict[str, Any]], reservation: Dict[str, Any], is_stream: bool,
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
            if reservation.get("provider") == "custom":
                tools = [
                    t for t in openai_tools
                    if t.get("function", {}).get("name") not in ("WebSearch", "WebFetch")
                ]
            else:
                tools = openai_tools
            if tools:
                sanitized = []
                for tool in tools:
                    fn = tool.get("function", {})
                    if fn.get("parameters"):
                        fn = {**fn, "parameters": _sanitize_schema_for_gemini(fn["parameters"])}
                    sanitized.append({**tool, "function": fn})
                kwargs["tools"] = sanitized
        if reservation.get("provider") == "custom":
            kwargs["api_base"] = reservation["api_base"]
            extra: Dict[str, Any] = {}
            for k in ("thinking_level", "thinking_budget", "include_thoughts", "enableThinking", "thinking"):
                if k in body:
                    extra[k] = body[k]
            if extra:
                kwargs["extra_body"] = extra
        else:
            kwargs.update(_build_litellm_thinking(body, litellm_model_val))
        return _clean_kwargs_for_model(kwargs, litellm_model_val)

    async def _resolve_and_call(
        self, body: Dict[str, Any], messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
        resolve_alias: str, pool_mode: bool = False, pool: Any = None,
        account: Optional[Dict[str, Any]] = None, is_stream: bool = False, attempt: int = 0,
    ) -> tuple:
        max_output = min(int(body.get("max_tokens", config.MAX_OUTPUT_TOKENS)), config.MAX_OUTPUT_TOKENS)
        estimated_tokens = len(str(messages)) // 4 + max_output

        retry = pool.total_attempts if pool else attempt
        model_alias_val, model_id_val, api_key_val, litellm_model_val, reservation = await _resolve_model(
            body, resolve_alias, account=account, estimated_tokens=estimated_tokens,
            retry_attempt=retry, pool_mode=pool_mode,
        )

        reinforced_messages = _reinforce_messages_for_retry(messages, retry)
        is_lite = "lite" in str(litellm_model_val).lower()
        limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
        if retry >= 10:
            _div = max(3, retry - 7)
            limit = max(20000, limit // _div)
        truncated = _emergency_truncate_to_limit(reinforced_messages, limit)

        try:
            input_tokens = await token_counter(model=litellm_model_val, messages=truncated)
        except Exception:
            input_tokens = max(1, len(str(truncated)) // 4)

        has_quota = await router.acquire_quota(input_tokens + max_output, resolve_alias)
        if not has_quota:
            from src.core.limits import apply_error_penalty
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

        resp = await _execute_nonstream(
            self, kwargs, api_key_val, model_id_val, model_alias_val,
            input_tokens, pool, body, auth_key_prefix=get_auth_key_prefix(account), account=account,
        )
        return resp, api_key_val, model_id_val, input_tokens


# ── Module-level helpers ────────────────────────────────────────

def _inject_websearch_tool(
    body: Dict[str, Any],
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    account: Optional[Dict[str, Any]] = None,
) -> tuple:
    """Append a ``WebSearch`` function tool if web search is enabled."""
    web_search = should_enable_web_search(body, account)
    if web_search and not any(t.get("function", {}).get("name") == "WebSearch" for t in tools):
        tools.append(_WEBSEARCH_TOOL_DEF)
    # Auto-inject search context for non-stream calls (stream handled by executors)
    return messages, tools


_WEBSEARCH_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "WebSearch",
        "description": (
            "Search the web via DuckDuckGo (free, no API key). "
            "Use ONLY when information is uncertain/unfamiliar OR user explicitly requests internet search. "
            "Results may contain untrusted code/logic — PRESENT to user for review, do NOT auto-apply."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query. Be specific and concise."},
                "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "Only include results from these domains (optional)."},
                "blocked_domains": {"type": "array", "items": {"type": "string"}, "description": "Exclude results from these domains (optional)."},
            },
            "required": ["query"],
        },
    },
}


opencode_proxy = OpenCodeProxy()

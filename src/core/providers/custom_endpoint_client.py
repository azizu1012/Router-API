"""Custom endpoint HTTP client — OpenAI-format, non-Gemini backends.

Extracted from ``gemini_facade.py`` to keep concerns separate.
Exposes:
  - ``call_custom_nonstream`` — POST OpenAI-format → SimpleNamespace response
  - ``CustomEndpointStreamGen`` — async generator yielding ``NativeChunk``
  - ``check_custom_pool_rate`` — RPM limiter for custom pool models
"""

import asyncio
import time
from collections import defaultdict
import json
import types
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from .anthropic_wire import is_anthropic_format

TIMEOUT = 120

# ── Custom pool rate limiting ────────────────────────────────────────────

_CUSTOM_POOL_RPM = 10
_custom_pool_usage: Dict[str, List[float]] = defaultdict(list)


async def check_custom_pool_rate(model_id: str) -> bool:
    """Sliding-window RPM check. Returns True if under limit."""
    now = time.time()
    window = now - 60
    _custom_pool_usage[model_id] = [t for t in _custom_pool_usage[model_id] if t > window]
    if len(_custom_pool_usage[model_id]) >= _CUSTOM_POOL_RPM:
        return False
    _custom_pool_usage[model_id].append(now)
    return True


class NativeChunk:
    """Mimics an OpenAI chunk object: .choices[0].delta.content, etc."""

    def __init__(self, delta_dict: dict):
        self.id: str = delta_dict.get("id", "")
        self.object: str = delta_dict.get("object", "chat.completion.chunk")
        self.created: int = delta_dict.get("created", 0)
        self.model: str = delta_dict.get("model", "")
        choices = delta_dict.get("choices", [])
        self.choices: list = []
        if choices:
            c = choices[0]
            delta = c.get("delta", {})
            fr = c.get("finish_reason")
            self.choices = [_make_choice(delta, fr)]
        self.usage: Optional[dict] = delta_dict.get("usage")


class _DeltaChoice:
    def __init__(self, delta: dict, finish_reason: Optional[str] = None):
        self.delta = types.SimpleNamespace()
        if delta.get("content"):
            self.delta.content = delta["content"]
        else:
            self.delta.content = None
        if delta.get("reasoning_content"):
            self.delta.reasoning_content = delta["reasoning_content"]
        else:
            self.delta.reasoning_content = None
        if delta.get("tool_calls"):
            self.delta.tool_calls = [_make_tool_call(tc) for tc in delta["tool_calls"]]
        else:
            self.delta.tool_calls = None
        self.finish_reason = finish_reason
        self.index = 0


def _make_choice(delta: dict, finish_reason: Optional[str] = None) -> Any:
    return _DeltaChoice(delta, finish_reason)


def _make_tool_call(tc: dict) -> Any:
    obj = types.SimpleNamespace()
    obj.id = tc.get("id", "")
    obj.type = tc.get("type", "function")
    obj.function = types.SimpleNamespace()
    obj.function.name = tc.get("function", {}).get("name", "")
    obj.function.arguments = tc.get("function", {}).get("arguments", "")
    return obj


def _build_payload(
    model: str,
    messages: List[Dict[str, Any]],
    stream: bool = False,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
    tool_choice: Any = None,
    parallel_tool_calls: Optional[bool] = None,
    response_format: Any = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    # Already the OpenAI spelling, so an OpenAI endpoint reads it as-is.
    if isinstance(response_format, dict) and response_format.get("type"):
        payload["response_format"] = response_format
    # Both are already in the spelling an OpenAI-compatible endpoint reads.
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if parallel_tool_calls is not None:
        payload["parallel_tool_calls"] = parallel_tool_calls
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
    if extra_body:
        payload.update(extra_body)
    return payload


def anthropic_messages_url(api_base: str) -> str:
    """Join an endpoint's base URL with /v1/messages exactly once.

    Operators paste base URLs both as ``https://host`` and as
    ``https://host/v1``. Naive concatenation produces ``/v1/v1/messages`` for
    the second form, which is the same trap this router already had to defend
    against on its own client-facing routes.
    """
    base = (api_base or "").rstrip("/")
    if base.endswith("/v1"):
        return base + "/messages"
    return base + "/v1/messages"


def anthropic_headers(api_key: str) -> Dict[str, str]:
    """Send the key both ways.

    Anthropic's own API reads x-api-key; a lot of self-hosted proxies in front
    of it read Authorization. Sending both is harmless and avoids making the
    admin guess which one their provider wants.
    """
    return {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "Authorization": f"Bearer {api_key}",
        "anthropic-version": "2023-06-01",
    }


async def call_anthropic_nonstream(
    api_base: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
    tool_choice: Any = None,
) -> Any:
    """POST Anthropic-format to a custom endpoint. Returns a SimpleNamespace
    shaped like the OpenAI response, so every caller above is unchanged."""
    from .anthropic_wire import openai_to_anthropic_body, anthropic_to_openai_message

    url = anthropic_messages_url(api_base)
    passthrough = dict(extra_body or {})
    if tool_choice is not None:
        passthrough["tool_choice"] = tool_choice
    # Deliberately not forwarded: Anthropic has no equivalent field, and
    # sending one it does not declare fails the whole request.
    passthrough.pop("response_format", None)
    payload = openai_to_anthropic_body(
        model, messages,
        max_tokens=max_tokens, temperature=temperature,
        stream=False, tools=tools, extra_body=passthrough,
    )
    payload.pop("stream", None)

    async with aiohttp.ClientSession(headers=anthropic_headers(api_key)) as session:
        async with session.post(
            url, json=payload, timeout=aiohttp.ClientTimeout(total=TIMEOUT)
        ) as response:
            if response.status >= 400:
                body_text = await response.text()
                raise RuntimeError(f"Custom endpoint HTTP {response.status}: {body_text[:500]}")
            data = await response.json()

    return _openai_namespace(anthropic_to_openai_message(data), model)


def _openai_namespace(data: Dict[str, Any], fallback_model: str) -> Any:
    """Wrap a chat.completion dict in the attribute shape callers already use."""
    resp = types.SimpleNamespace()
    resp.id = data.get("id") or f"chatcmpl-{id(data)}"
    resp.object = data.get("object", "chat.completion")
    resp.created = data.get("created", 0)
    resp.model = data.get("model") or fallback_model
    resp.usage = data.get("usage")

    choices = []
    for c in data.get("choices", []) or []:
        msg_dict = c.get("message", {}) or {}
        msg = types.SimpleNamespace()
        msg.content = msg_dict.get("content", "") or ""
        msg.role = msg_dict.get("role", "assistant")
        msg.reasoning_content = msg_dict.get("reasoning_content")
        tool_calls = []
        for tc in msg_dict.get("tool_calls", []) or []:
            tc_obj = types.SimpleNamespace()
            tc_obj.id = tc.get("id", "")
            tc_obj.type = tc.get("type", "function")
            tc_obj.function = types.SimpleNamespace()
            tc_obj.function.name = (tc.get("function") or {}).get("name", "")
            tc_obj.function.arguments = (tc.get("function") or {}).get("arguments", "")
            tool_calls.append(tc_obj)
        msg.tool_calls = tool_calls or None

        choice = types.SimpleNamespace()
        choice.message = msg
        choice.finish_reason = c.get("finish_reason", "stop")
        choice.index = c.get("index", 0)
        choices.append(choice)

    resp.choices = choices
    return resp


async def call_custom_nonstream(
    api_base: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
    api_format: str = "openai",
    tool_choice: Any = None,
    parallel_tool_calls: Optional[bool] = None,
    response_format: Any = None,
) -> Any:
    """POST OpenAI-format to custom endpoint. Returns SimpleNamespace response."""
    if is_anthropic_format(api_format):
        return await call_anthropic_nonstream(
            api_base, api_key, model, messages,
            temperature=temperature, max_tokens=max_tokens,
            tools=tools, extra_body=extra_body, tool_choice=tool_choice,
        )
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = _build_payload(model, messages, False, temperature, max_tokens, tools,
                          extra_body, tool_choice, parallel_tool_calls, response_format)

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.post(
            url, json=payload, timeout=aiohttp.ClientTimeout(total=TIMEOUT)
        ) as response:
            if response.status >= 400:
                body_text = await response.text()
                raise RuntimeError(f"Custom endpoint HTTP {response.status}: {body_text[:500]}")
            data = await response.json()

    resp = types.SimpleNamespace()
    resp.id = data.get("id", f"chatcmpl-{id(data)}")
    resp.object = data.get("object", "chat.completion")
    resp.created = data.get("created", 0)
    resp.model = data.get("model", model)
    resp.usage = data.get("usage")

    choices = []
    for c in data.get("choices", []):
        msg_dict = c.get("message", {})
        msg = types.SimpleNamespace()
        msg.content = msg_dict.get("content", "") or ""
        msg.role = msg_dict.get("role", "assistant")
        msg.reasoning_content = msg_dict.get("reasoning_content")
        raw_tcs = msg_dict.get("tool_calls", []) or []
        msg.tool_calls = []
        for tc in raw_tcs:
            tc_obj = types.SimpleNamespace()
            tc_obj.id = tc.get("id", "")
            tc_obj.type = tc.get("type", "function")
            tc_obj.function = types.SimpleNamespace()
            tc_obj.function.name = tc.get("function", {}).get("name", "")
            tc_obj.function.arguments = tc.get("function", {}).get("arguments", "")
            msg.tool_calls.append(tc_obj)
        if not msg.tool_calls:
            msg.tool_calls = None

        choice = types.SimpleNamespace()
        choice.message = msg
        choice.finish_reason = c.get("finish_reason", "stop")
        choice.index = c.get("index", 0)
        choices.append(choice)

    resp.choices = choices
    return resp


class CustomEndpointStreamGen:
    """Async generator for custom endpoint SSE. Yields NativeChunk objects."""

    def __init__(
        self, api_base: str, api_key: str, model: str,
        messages: List[Dict[str, Any]], temperature: Optional[float] = None,
        max_tokens: Optional[int] = None, tools: Optional[List[Dict[str, Any]]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        api_format: str = "openai",
        tool_choice: Any = None,
        parallel_tool_calls: Optional[bool] = None,
        response_format: Any = None,
    ):
        self._tool_choice = tool_choice
        self._parallel_tool_calls = parallel_tool_calls
        self._response_format = response_format
        self._api_base = api_base
        self._api_key = api_key
        self._model = model
        self._messages = messages
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._tools = tools
        self._extra_body = extra_body
        self._api_format = api_format
        self._sse_gen: Optional[AsyncIterator[Dict[str, Any]]] = None
        self._buf: List[dict] = []
        self._started = False
        self._session: Optional[Any] = None
        self._response: Optional[Any] = None

    async def _start(self) -> None:
        if is_anthropic_format(self._api_format):
            await self._start_anthropic()
            return
        url = f"{self._api_base.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        payload = _build_payload(
            self._model, self._messages, True,
            self._temperature, self._max_tokens, self._tools, self._extra_body,
            self._tool_choice, self._parallel_tool_calls, self._response_format,
        )

        self._session = aiohttp.ClientSession(headers=headers)
        try:
            resp = await self._session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=TIMEOUT)
            )
            if resp.status >= 400:
                body_text = await resp.text()
                await self._cleanup()
                raise RuntimeError(f"Custom endpoint HTTP {resp.status}: {body_text[:500]}")
            self._response = resp
            self._sse_gen = self._parse_sse_stream(resp)
            self._started = True
        except Exception:
            await self._cleanup()
            raise

    async def _start_anthropic(self) -> None:
        """Anthropic-compatible endpoints speak a typed event stream.

        The events are translated to OpenAI chunks here so that __anext__ and
        everything above it stay unaware of which dialect the owner runs.
        """
        from .anthropic_wire import openai_to_anthropic_body

        url = anthropic_messages_url(self._api_base)
        passthrough = dict(self._extra_body or {})
        if self._tool_choice is not None:
            passthrough["tool_choice"] = self._tool_choice
        passthrough.pop("response_format", None)
        payload = openai_to_anthropic_body(
            self._model, self._messages,
            max_tokens=self._max_tokens, temperature=self._temperature,
            stream=True, tools=self._tools, extra_body=passthrough,
        )

        self._session = aiohttp.ClientSession(headers=anthropic_headers(self._api_key))
        try:
            resp = await self._session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=TIMEOUT)
            )
            if resp.status >= 400:
                body_text = await resp.text()
                await self._cleanup()
                raise RuntimeError(f"Custom endpoint HTTP {resp.status}: {body_text[:500]}")
            self._response = resp
            self._sse_gen = self._parse_anthropic_stream(resp)
            self._started = True
        except Exception:
            await self._cleanup()
            raise

    async def _parse_anthropic_stream(self, response: Any) -> AsyncIterator[Dict[str, Any]]:
        from .anthropic_wire import anthropic_event_to_openai_delta

        buffer = ""
        async for chunk_bytes in response.content:
            buffer += chunk_bytes.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                trimmed = line.strip()
                if not trimmed.startswith("data:"):
                    continue
                data_str = trimmed[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                chunk = anthropic_event_to_openai_delta(event)
                if chunk is None:
                    continue
                chunk.setdefault("id", f"chatcmpl-{id(event)}")
                chunk.setdefault("created", 0)
                chunk.setdefault("model", self._model)
                yield chunk

    async def _cleanup(self) -> None:
        if self._response:
            self._response.close()
            self._response = None
        if self._session:
            await self._session.close()
            self._session = None

    async def _parse_sse_stream(self, response: Any) -> AsyncIterator[Dict[str, Any]]:
        buffer = ""
        async for chunk_bytes in response.content:
            buffer += chunk_bytes.decode("utf-8", errors="replace")
            lines = buffer.split("\n")
            buffer = lines.pop() or ""
            for line in lines:
                trimmed = line.strip()
                if not trimmed:
                    continue
                if trimmed.startswith("data: "):
                    data_str = trimmed[6:]
                elif trimmed.startswith("data:"):
                    data_str = trimmed[5:]
                else:
                    continue
                if data_str.strip() == "[DONE]":
                    return
                try:
                    yield json.loads(data_str)
                except json.JSONDecodeError:
                    continue
        if buffer.strip():
            trimmed = buffer.strip()
            if trimmed.startswith("data: "):
                try:
                    yield json.loads(trimmed[6:])
                except json.JSONDecodeError:
                    pass

    def __aiter__(self):
        return self

    async def __anext__(self) -> NativeChunk:
        if self._buf:
            return NativeChunk(self._buf.pop(0))
        if not self._started:
            await self._start()

        while True:
            gen = self._sse_gen
            if gen is None:
                raise RuntimeError("Custom endpoint stream not initialized")
            try:
                raw_chunk = await asyncio.wait_for(gen.__anext__(), timeout=30.0)
            except asyncio.TimeoutError:
                from src.core.config_n_logg.logger import logger_proxy as logger
                logger.warning("[Custom Endpoint Stream] Chunk read timeout (30s) reached. Closing stream.")
                await self._cleanup()
                raise StopAsyncIteration
            except StopAsyncIteration:
                await self._cleanup()
                raise

            choices = raw_chunk.get("choices", [])
            if not choices:
                continue

            delta = {}
            c = choices[0]
            c_delta = c.get("delta", {})
            if c_delta.get("content"):
                delta["content"] = c_delta["content"]
            if c_delta.get("reasoning_content"):
                delta["reasoning_content"] = c_delta["reasoning_content"]
            if c_delta.get("tool_calls"):
                tcs = []
                for tc in c_delta["tool_calls"]:
                    tcs.append({
                        "id": tc.get("id"),
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": tc.get("function", {}).get("name", ""),
                            "arguments": tc.get("function", {}).get("arguments", ""),
                        },
                    })
                delta["tool_calls"] = tcs

            fr = c.get("finish_reason")

            chunk_dict = {
                "id": raw_chunk.get("id", f"chatcmpl-{id(raw_chunk)}"),
                "object": "chat.completion.chunk",
                "created": raw_chunk.get("created", 0),
                "model": raw_chunk.get("model", self._model),
                "choices": [{"index": 0, "delta": delta, "finish_reason": fr}],
            }
            usage = raw_chunk.get("usage")
            if usage:
                chunk_dict["usage"] = usage

            self._buf.append(chunk_dict)
            return NativeChunk(self._buf.pop(0))

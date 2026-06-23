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
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
    if extra_body:
        payload.update(extra_body)
    return payload


async def call_custom_nonstream(
    api_base: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Any:
    """POST OpenAI-format to custom endpoint. Returns SimpleNamespace response."""
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = _build_payload(model, messages, False, temperature, max_tokens, tools, extra_body)

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
    ):
        self._api_base = api_base
        self._api_key = api_key
        self._model = model
        self._messages = messages
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._tools = tools
        self._extra_body = extra_body
        self._sse_gen: Optional[AsyncIterator[Dict[str, Any]]] = None
        self._buf: List[dict] = []
        self._started = False
        self._session: Optional[Any] = None
        self._response: Optional[Any] = None

    async def _start(self) -> None:
        url = f"{self._api_base.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        payload = _build_payload(
            self._model, self._messages, True,
            self._temperature, self._max_tokens, self._tools, self._extra_body,
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

"""Custom endpoint HTTP client — OpenAI-format, non-Gemini backends.

Extracted from the Gemini provider facade to keep endpoint transport concerns separate.
Exposes:
  - ``call_custom_nonstream`` — POST OpenAI-format → SimpleNamespace response
  - ``CustomEndpointStreamGen`` — async generator yielding ``NativeChunk``
  - ``check_custom_pool_rate`` — RPM limiter for custom pool models
"""

import asyncio
import os
import re
import time
from collections import defaultdict
import json
import types
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from src.core.config_n_logg.logger import logger_proxy as logger

from ..anthropic_wire import is_anthropic_format
from ...runtime.effort import sanitize_custom_endpoint_payload
from ...runtime.sse_format import sse_data

TIMEOUT = int(os.getenv("CUSTOM_ENDPOINT_TIMEOUT", "900"))
NETWORK_RETRIES_BEFORE_OUTPUT = int(os.getenv("CUSTOM_ENDPOINT_NETWORK_RETRIES", "2"))


def _is_retryable_network_error(exc: BaseException) -> bool:
    """Retry transport failures before a provider response exists."""
    return isinstance(exc, (aiohttp.ClientError, asyncio.TimeoutError, OSError))


def _is_muse_endpoint(api_base: str, model: str) -> bool:
    """Identify only Meta's direct Muse API, not aggregators such as 9router."""
    # Model names are not sufficient: 9router advertises Muse models too and
    # may support the full OpenAI tool-choice contract on its own endpoint.
    return "api.meta.ai" in (api_base or "").lower()


def _normalize_muse_tool_choice(api_base: str, model: str, tool_choice: Any) -> Any:
    """Keep tools enabled while using Muse's restricted tool-choice dialect."""
    if not _is_muse_endpoint(api_base, model):
        return tool_choice
    if isinstance(tool_choice, dict) and "tool_choice" in tool_choice:
        choice = tool_choice.get("tool_choice")
    else:
        choice = tool_choice
    if choice is None:
        return tool_choice
    if choice == "auto":
        return "auto"
    # Muse does not accept required/none or the OpenAI forced-function object.
    # `none` is handled earlier by removing tools from the request.
    if choice == "none":
        return None
    return "auto"

# ── Output-limit retry ───────────────────────────────────────────────────
#
# Some OpenAI-compatible backends cap the *response* at a fraction of what the
# router asks for and answer 400 instead of clamping — e.g. "Response exceeded
# the output limit". The model and the prompt are fine, so the useful move is a
# smaller budget, not a failed turn. Bounded because a provider that rejects
# every budget must surface its error rather than spin.

_OUTPUT_LIMIT_FLOOR = 4096
_OUTPUT_LIMIT_MAX_RETRIES = 4
_OUTPUT_BUDGET_KEYS = ("max_tokens", "max_completion_tokens")

_OUTPUT_LIMIT_PATTERN = re.compile(
    r"exceeded\s+the\s+output\s+limit"
    r"|output[\s_]+limit[\s_]+(?:exceeded|reached|hit)"
    r"|(?:max|maximum)[\s_]*(?:output[\s_]*|completion[\s_]*|new[\s_]*)?tokens?"
    r"[^.\n;]{0,80}?(?:exceed|too\s+(?:large|big|high)|greater\s+than"
    r"|less\s+than\s+or\s+equal|out\s+of\s+range|invalid)"
    r"|(?:invalid|unsupported|illegal)[^.\n;]{0,40}?"
    r"(?:max|maximum)[\s_]*(?:output[\s_]*|completion[\s_]*|new[\s_]*)?tokens?",
    re.IGNORECASE,
)


def is_output_limit_error(status: int, body: str) -> bool:
    """True only for a 4xx that names the output budget as the problem.

    429 is excluded on purpose: it is a rate limit, so halving ``max_tokens``
    would spend a retry on the wrong axis. 5xx is excluded as well — the
    request was not rejected for its size.
    """
    if not isinstance(status, int) or not 400 <= status < 500 or status == 429:
        return False
    return bool(_OUTPUT_LIMIT_PATTERN.search(body or ""))


def next_output_retry_budget(budget: Any) -> Optional[int]:
    """Halve a budget, stopping at the floor. ``None`` means don't retry."""
    try:
        current = int(budget)
    except (TypeError, ValueError):
        return None
    if current <= _OUTPUT_LIMIT_FLOOR:
        return None
    return max(_OUTPUT_LIMIT_FLOOR, current // 2)


def _output_budget_keys(payload: Dict[str, Any]) -> List[str]:
    return [key for key in _OUTPUT_BUDGET_KEYS if payload.get(key) is not None]


def _output_budget_key(payload: Dict[str, Any]) -> Optional[str]:
    keys = _output_budget_keys(payload)
    return keys[0] if keys else None


def _retry_budget_for_output_limit(
    status: int, body: str, payload: Dict[str, Any], retries: int
) -> Optional[int]:
    if retries >= _OUTPUT_LIMIT_MAX_RETRIES:
        return None
    if not is_output_limit_error(status, body):
        return None
    keys = _output_budget_keys(payload)
    if not keys:
        return None
    valid_budgets = []
    for k in keys:
        try:
            valid_budgets.append(int(payload[k]))
        except (TypeError, ValueError):
            pass
    if not valid_budgets:
        return None
    return next_output_retry_budget(max(valid_budgets))


def _first_line(body: str, limit: int = 200) -> str:
    line = (body or "").strip().splitlines()[0] if (body or "").strip() else ""
    return line[:limit]


async def _post_with_output_limit_retry(
    session: Any,
    url: str,
    payload: Dict[str, Any],
    *,
    log_context: str = "Custom endpoint",
) -> Any:
    """POST ``payload``, lowering the output budget when the endpoint rejects it.

    Mutates ``payload`` in place so the next attempt sends the smaller budget —
    the caller built it once, including any ``extra_body`` override, and
    re-deriving it from the call arguments would silently discard that.

    Returns the first response under 400. Every other status is raised as the
    same ``RuntimeError`` the call sites used before, so error text visible to
    the client does not change.
    """
    retries = 0
    network_retries = 0
    while True:
        try:
            response = await session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=TIMEOUT)
            )
        except Exception as exc:
            if not _is_retryable_network_error(exc) or network_retries >= NETWORK_RETRIES_BEFORE_OUTPUT:
                raise
            network_retries += 1
            logger.warning(
                "[CustomEndpoint] transport failure before response (%d/%d) "
                "for %s: %s; retrying with the same payload",
                network_retries, NETWORK_RETRIES_BEFORE_OUTPUT, url, exc,
            )
            await asyncio.sleep(min(2 ** network_retries, 8))
            continue
        if response.status < 400:
            return response

        # Read the rejection body inside the context manager: that is what
        # returns the connection to the pool, exactly as the ``async with
        # session.post(...)`` these call sites used to use did.
        async with response:
            body_text = await response.text()
        status = response.status
        keys = _output_budget_keys(payload)
        budget = _retry_budget_for_output_limit(status, body_text, payload, retries)
        if budget is None:
            raise RuntimeError(f"{log_context} HTTP {status}: {body_text[:500]}")
        keys_summary = ", ".join(f"{k}={payload.get(k)}" for k in keys)
        logger.warning(
            "[CustomEndpoint] endpoint rejected %s (%s); retrying with %s",
            keys_summary, _first_line(body_text), budget,
        )
        for k in keys:
            payload[k] = budget
        retries += 1

# ── Custom pool rate limiting ────────────────────────────────────────────

# ponytail: 120 RPM custom pool rate; adjust when per-endpoint RPM config is added
_DEFAULT_CUSTOM_POOL_RPM = 120
_CUSTOM_POOL_RPM = _DEFAULT_CUSTOM_POOL_RPM
_custom_pool_usage: Dict[str, List[float]] = defaultdict(list)
_custom_pool_lock = asyncio.Lock()


async def check_custom_pool_rate(model_id: str, endpoint_name: Optional[str] = None, rpm: int = 0) -> bool:
    """Sliding-window RPM check keyed by endpoint. Returns True if under limit.

    rpm is the provider-declared ceiling from the endpoint row (0 = unset, use
    the default). The account-side gate runs separately in the request path, so
    the effective ceiling is min(provider_rpm, account_rpm) with neither layer
    knowing about the other. Two endpoints serving the same model_id get their
    own buckets, keyed by endpoint name.
    """
    try:
        limit = int(rpm or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        limit = _DEFAULT_CUSTOM_POOL_RPM
    key = endpoint_name or model_id
    now = time.time()
    window = now - 60
    async with _custom_pool_lock:
        _custom_pool_usage[key] = [t for t in _custom_pool_usage[key] if t > window]
        if len(_custom_pool_usage[key]) >= limit:
            return False
        _custom_pool_usage[key].append(now)
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
    if "index" in tc:
        obj.index = tc["index"]
    obj.function = types.SimpleNamespace()
    obj.function.name = tc.get("function", {}).get("name", "")
    obj.function.arguments = tc.get("function", {}).get("arguments", "")
    return obj


def _drop_orphaned_tool_results(
    messages: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Remove tool-result messages whose ``tool_use`` no longer exists.

    Both custom-endpoint dialects enforce the same pairing, and both reject the
    entire request when it is broken. Anthropic is explicit about it — ``400
    orphaned tool_result in conversation history`` — and the OpenAI-compatible
    servers that validate ``role: "tool"`` against a preceding assistant
    ``tool_calls`` fail the same way. One endpoint can serve both formats, so
    this runs for both.

    Several stages above rewrite message history, and each can break a pair for
    its own reasons:

    * ``ensure_tool_ids_in_messages`` synthesises an id for an empty
      ``tool_use.id`` but leaves the paired result's ``tool_use_id`` empty, so
      the two sides disagree from the very first hop.
    * ``compress_messages`` (RTK) rewrites tool output in place.
    * ``emergency_truncate_to_limit`` prunes the middle, and its cut point is a
      message boundary rather than a tool-exchange boundary.
    * ``strip_tools_for_compaction`` rewrites the body for /compact requests.

    Every one of those runs *after* the converter's own orphan handling, so
    nothing upstream can be trusted to leave the history consistent. This sits
    at the last point before the request is serialised, which is the only place
    the invariant holds for all of them at once.

    Dropping rather than rebuilding the pair is deliberate: the cut has already
    been accounted for against the token budget, and re-attaching the assistant
    turn would put the request back over it. The truncation notice already tells
    the model that middle turns were dropped.
    """
    if not messages:
        return []

    # An empty id is a real id here. A tool_calls entry that carries no `id`
    # produces a tool_use with id "", and the tool message answering it carries
    # tool_call_id "" as well — the two match, so dropping that pair would leave
    # the tool_use unanswered, which is its own 400.
    called: set = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict):
                    called.add(str(tc.get("id") or ""))

    # The reverse orphan is the 9router failure: an assistant tool_calls whose
    # tool result was pruned upstream (middle-out truncation cuts on message,
    # not exchange, boundaries). Sending the call without its result makes
    # strict aggregators answer `No tool output found for function call ...`.
    # Drop the unanswered call rather than one side of the pair — the model
    # simply re-issues it on the next turn if it still needs it.
    answered: set = set()
    for msg in messages:
        role = str(msg.get("role") or "")
        if role == "tool" or role.startswith("tool"):
            answered.add(str(msg.get("tool_call_id") or ""))

    kept: List[Dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        if role == "tool" or role.startswith("tool"):
            cid = str(msg.get("tool_call_id") or "")
            if cid not in called:
                logger.warning(
                    "[CustomEndpoint] dropped orphaned tool_result (tool_call_id=%r)",
                    cid,
                )
                continue
        elif role == "assistant" and msg.get("tool_calls"):
            live = [tc for tc in msg.get("tool_calls") or []
                    if not isinstance(tc, dict) or str(tc.get("id") or "") in answered]
            dropped = len(msg.get("tool_calls") or []) - len(live)
            if dropped:
                logger.warning(
                    "[CustomEndpoint] dropped %d unanswered tool_call(s); "
                    "9router rejects calls without results", dropped,
                )
            if not live and not (msg.get("content")):
                # The turn was only unanswered calls — nothing left to send.
                continue
            if len(live) != len(msg.get("tool_calls") or []):
                msg = dict(msg, tool_calls=live)
        kept.append(msg)

    return _merge_adjacent_assistant_turns(kept)


def _merge_adjacent_assistant_turns(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Join assistant turns that a drop left next to each other.

    A ``tool`` message is a user turn once it reaches the wire, so it is what
    keeps an assistant turn from butting up against the next one. Removing an
    orphan removes that separator: a history shaped like

        assistant(tool_calls A) / tool(orphan) / assistant("done")

    becomes two adjacent assistant turns, and Anthropic rejects consecutive
    assistant messages as firmly as it rejects an orphaned tool_result. Without
    this the guard would trade one 400 for another.

    Tool calls are collected rather than dropped so that merging cannot silently
    discard a call the model made.
    """
    merged: List[Dict[str, Any]] = []
    for msg in messages:
        if (
            merged
            and msg.get("role") == "assistant"
            and merged[-1].get("role") == "assistant"
        ):
            prev = merged[-1]
            calls = list(prev.get("tool_calls") or []) + list(msg.get("tool_calls") or [])
            text = "\n".join(
                part for part in (prev.get("content"), msg.get("content"))
                if isinstance(part, str) and part
            )
            joined = dict(prev)
            if text:
                joined["content"] = text
            if calls:
                joined["tool_calls"] = calls
            merged[-1] = joined
            continue
        merged.append(msg)
    return merged


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
    endpoint_base: str = "",
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
    normalized_tool_choice = _normalize_muse_tool_choice(endpoint_base, model, tool_choice)
    if normalized_tool_choice is not None:
        payload["tool_choice"] = normalized_tool_choice
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
    # Sampling params / extra_body may carry the same field; normalize after
    # the merge so Muse cannot receive the unsupported OpenAI
    # "required"/forced-tool form. Re-read the MERGED value — the original
    # `tool_choice` arg may be None while extra_body smuggles "required".
    if "tool_choice" in payload:
        merged_choice = payload.get("tool_choice")
        normalized_after = _normalize_muse_tool_choice(endpoint_base, model, merged_choice)
        if normalized_after is None:
            payload.pop("tool_choice", None)
            effective = merged_choice.get("tool_choice") if isinstance(merged_choice, dict) and "tool_choice" in merged_choice else merged_choice
            if effective == "none":
                payload.pop("tools", None)
        else:
            payload["tool_choice"] = normalized_after
    # Last stop before serialisation: the endpoint must never receive a
    # client placeholder like "auto", nor a dialect field it does not declare.
    sanitize_custom_endpoint_payload(payload, api_format="openai")
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
    from ..anthropic_wire import openai_to_anthropic_body, anthropic_to_openai_message

    url = anthropic_messages_url(api_base)
    passthrough = dict(extra_body or {})
    if tool_choice is not None:
        passthrough["tool_choice"] = tool_choice
    # Deliberately not forwarded: Anthropic has no equivalent field, and
    # sending one it does not declare fails the whole request.
    passthrough.pop("response_format", None)
    payload = openai_to_anthropic_body(
        model, _drop_orphaned_tool_results(messages),
        max_tokens=max_tokens, temperature=temperature,
        stream=False, tools=tools, extra_body=passthrough,
    )
    payload.pop("stream", None)
    sanitize_custom_endpoint_payload(payload, api_format="anthropic")

    async with aiohttp.ClientSession(headers=anthropic_headers(api_key)) as session:
        response = await _post_with_output_limit_retry(session, url, payload)
        async with response:
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
    payload = _build_payload(model, _drop_orphaned_tool_results(messages), False,
                             temperature, max_tokens, tools,
                             extra_body, tool_choice, parallel_tool_calls, response_format,
                             api_base)

    async with aiohttp.ClientSession(headers=headers) as session:
        response = await _post_with_output_limit_retry(session, url, payload)
        async with response:
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
            self._model, _drop_orphaned_tool_results(self._messages), True,
            self._temperature, self._max_tokens, self._tools, self._extra_body,
            self._tool_choice, self._parallel_tool_calls, self._response_format,
            self._api_base,
        )

        self._session = aiohttp.ClientSession(headers=headers)
        try:
            resp = await _post_with_output_limit_retry(self._session, url, payload)
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
        from ..anthropic_wire import openai_to_anthropic_body

        url = anthropic_messages_url(self._api_base)
        passthrough = dict(self._extra_body or {})
        if self._tool_choice is not None:
            passthrough["tool_choice"] = self._tool_choice
        passthrough.pop("response_format", None)
        payload = openai_to_anthropic_body(
            self._model, _drop_orphaned_tool_results(self._messages),
            max_tokens=self._max_tokens, temperature=self._temperature,
            stream=True, tools=self._tools, extra_body=passthrough,
        )
        sanitize_custom_endpoint_payload(payload, api_format="anthropic")

        self._session = aiohttp.ClientSession(headers=anthropic_headers(self._api_key))
        try:
            resp = await _post_with_output_limit_retry(self._session, url, payload)
            self._response = resp
            self._sse_gen = self._parse_anthropic_stream(resp)
            self._started = True
        except Exception:
            await self._cleanup()
            raise

    async def _parse_anthropic_stream(self, response: Any) -> AsyncIterator[Dict[str, Any]]:
        from ..anthropic_wire import anthropic_event_to_openai_delta

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

    # SSE data-line parsing lives in src/core/runtime/sse_format.py as
    # ``sse_data`` so every parser in the repo shares one spelling.
    # (It used to be a static method here; only _parse_sse_stream used it,
    # and manager._call_chat kept its own narrower copy.)
    _sse_data = staticmethod(sse_data)

    async def _parse_sse_stream(self, response: Any) -> AsyncIterator[Dict[str, Any]]:
        # 9router-class aggregators have closed the connection after several
        # chunks without a finish_reason or [DONE] — the pool then raises
        # stream_ended_without_finish_reason with no trace of what the wire
        # actually carried. Count frames and remember the tail so the abnormal
        # EOF log below can name endpoint, chunk count and last-frame shape
        # (metadata only, never tool/result content which can be huge).
        buffer = ""
        frames = 0
        saw_done = False
        last_shape = ""
        async for chunk_bytes in response.content:
            buffer += chunk_bytes.decode("utf-8", errors="replace")
            lines = buffer.split("\n")
            buffer = lines.pop() or ""
            for line in lines:
                trimmed = line.strip()
                if not trimmed:
                    continue
                data_str = self._sse_data(line)
                if data_str is None:
                    continue
                if data_str.strip() == "[DONE]":
                    saw_done = True
                    return
                try:
                    obj = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                frames += 1
                if isinstance(obj, dict):
                    ch = (obj.get("choices") or [{}])[0] if obj.get("choices") else {}
                    delta = ch.get("delta") or {}
                    last_shape = (
                        f"keys={sorted(obj.keys())} "
                        f"delta_keys={sorted(delta.keys()) if isinstance(delta, dict) else '?'} "
                        f"finish={ch.get('finish_reason')}"
                    )
                yield obj
        if buffer.strip():
            # The connection closed mid-line: the final frame never got its
            # newline. Parse it with the same shapes the loop accepts, or a
            # trailing finish_reason is lost and a good turn looks broken.
            data_str = self._sse_data(buffer)
            if data_str is not None and data_str.strip() != "[DONE]":
                try:
                    obj = json.loads(data_str)
                except json.JSONDecodeError:
                    pass
                else:
                    frames += 1
                    if isinstance(obj, dict):
                        ch = (obj.get("choices") or [{}])[0] if obj.get("choices") else {}
                        delta = ch.get("delta") or {}
                        last_shape = (
                            f"keys={sorted(obj.keys())} "
                            f"delta_keys={sorted(delta.keys()) if isinstance(delta, dict) else '?'} "
                            f"finish={ch.get('finish_reason')}"
                        )
                    yield obj
            elif data_str is not None:
                saw_done = True
        if not saw_done:
            logger.warning(
                "[CustomEndpoint] SSE closed without [DONE]: endpoint=%s model=%s "
                "frames=%d last=%s",
                self._api_base, self._model, frames, last_shape or "none",
            )

    def __aiter__(self):
        return self

    async def __anext__(self) -> NativeChunk:
        if self._buf:
            return NativeChunk(self._buf.pop(0))
        if not self._started:
            await self._start()

        chunk_timeout = float(os.getenv("CUSTOM_ENDPOINT_CHUNK_TIMEOUT", "600.0"))
        while True:
            gen = self._sse_gen
            if gen is None:
                raise RuntimeError("Custom endpoint stream not initialized")
            try:
                raw_chunk = await asyncio.wait_for(gen.__anext__(), timeout=chunk_timeout)
            except asyncio.TimeoutError:
                from src.core.config_n_logg.logger import logger_proxy as logger
                logger.warning("[Custom Endpoint Stream] Chunk read timeout (%.0fs) reached. Closing stream.", chunk_timeout)
                await self._cleanup()
                raise TimeoutError("custom_endpoint_stream_timeout")
            except StopAsyncIteration:
                await self._cleanup()
                raise

            choices = raw_chunk.get("choices", [])
            if not choices:
                # 9router can emit non-OpenAI frames mid-stream (usage-only
                # frames, error objects, keepalive comments already stripped by
                # the parser). A frame with an error payload is the upstream
                # telling us the turn failed — surfacing it beats looping until
                # EOF and then reporting a contentless
                # stream_ended_without_finish_reason.
                err = raw_chunk.get("error")
                if err:
                    logger.warning(
                        "[CustomEndpoint] upstream error frame: endpoint=%s "
                        "model=%s error=%s",
                        self._api_base, self._model, str(err)[:300],
                    )
                    raise RuntimeError(f"custom_endpoint_upstream_error: {err}")
                usage_only = raw_chunk.get("usage")
                if usage_only and not any(
                    k in raw_chunk for k in ("id", "model", "created")
                ):
                    logger.info(
                        "[CustomEndpoint] usage-only frame outside chunk envelope; "
                        "endpoint=%s", self._api_base,
                    )
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
                    tc_dict = {
                        "id": tc.get("id"),
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": tc.get("function", {}).get("name", ""),
                            "arguments": tc.get("function", {}).get("arguments", ""),
                        },
                    }
                    if "index" in tc:
                        tc_dict["index"] = tc["index"]
                    tcs.append(tc_dict)
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


"""A deterministic stand-in for the paid LLM providers.

Every provider seam in this repo is a method on one of two singletons:

    src.core.orchestration.pool_manager.pool_manager            call_nonstream / call_stream / acompletion
    src.core.providers.gemini.manager.api_manager call_gemini / call_gemini_json / call_gemini_stream

The proxy modules import those singletons by reference, so replacing a method on
the singleton replaces the provider for every caller at once — including the
function-local `from src.tools.search.orchestration import manager as search_manager` sites that a
per-module patch would miss. That is why this kit needed no production change:
the seam it needs already existed, it just was not being used.

Determinism is the point. Every field is fixed: the api key, the model id, the
token counts, the ordering of chunks, the key order of a JSON payload. Two runs
of the same test produce the same bytes, so a failure means the router changed
and not that a provider felt differently about the question.

`install()` also stubs `log_usage`. A fake call must not append a usage row to
the developer's `usage.db` — `tests/conftest.py` has a session guard for exactly
that, and a provider fake that still bills is not a fake.

Scenarios
---------
    text_scenario(...)             deterministic non-stream text
    json_scenario({...})           non-stream JSON, keys sorted
    stream_scenario("a", "b")      async chunk stream (+ finish/usage)
    sse_scenario("event: ...")     raw SSE frames, passed through verbatim
    tool_call_scenario(name, args) a function/tool call turn
    rate_limit_scenario()          HTTP 429, in the wording providers use
    server_error_scenario()        HTTP 503 / 5xx
    timeout_scenario()             a read timeout
    malformed_scenario()           an HTML error page where JSON was promised
    retry_then_success_scenario()  N transient failures, then a good answer

Each returns a `FakeProvider` holding a script of turns; the last turn repeats if
the caller asks for more turns than were scripted, which keeps a multi-turn test
from having to count its own calls.

Web search is local by design. `FakeSearch` replaces either the DuckDuckGo
scrape (`install`) or the engine dispatch itself (`install_manager`), so a search
test costs nothing and never reaches Gemini Router search.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
from contextlib import suppress
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "DEFAULT_API_KEY", "DEFAULT_MODEL_ALIAS", "DEFAULT_MODEL_ID",
    "DEFAULT_INPUT_TOKENS", "DEFAULT_OUTPUT_TOKENS", "DEFAULT_TOOL_CALL_ID",
    "FakeChoice", "FakeDelta", "FakeMessage", "FakeProvider", "FakeProviderError",
    "FakeProviderTimeout", "FakeResponse", "FakeSearch", "FakeStreamChoice",
    "FakeStreamChunk", "JsonTurn", "MalformedResponse", "SSETurn", "StreamTurn",
    "TextTurn", "ToolCallTurn", "FailureTurn", "dual",
    "finish_chunk", "malformed_error", "rate_limit_error", "server_error",
    "sse_frame", "text_chunk", "timeout_error", "tool_call_chunk",
    "json_scenario", "malformed_scenario", "rate_limit_scenario",
    "retry_then_success_scenario", "sse_scenario", "server_error_scenario",
    "stream_scenario", "text_scenario", "timeout_scenario", "tool_call_scenario",
    "drain", "run",
]

# A credential-shaped string so code that logs or slices the key has something
# realistic to chew on. Not a key: it authenticates with nothing.
DEFAULT_API_KEY = "sk-fake-mock-key"
DEFAULT_MODEL_ID = "gemini-3.5-flash"
DEFAULT_MODEL_ALIAS = "gemini-flash"
DEFAULT_INPUT_TOKENS = 1000
DEFAULT_OUTPUT_TOKENS = 42
DEFAULT_TOOL_CALL_ID = "call_fake_0000000001"

DEFAULT_TEXT = "Paris is the capital of France."
DEFAULT_CITATIONS = [
    {"title": "Paris", "url": "https://example.org/paris"},
    {"title": "France", "url": "https://example.net/france"},
]

# Modules that bind `log_usage` at import. Listed rather than discovered so the
# list is auditable: a module added to the codebase is not silently uncovered,
# it is a line someone has to add here and notice is missing.
_USAGE_LOG_MODULES: Tuple[str, ...] = (
    "src.core.observability.usage_logger",
    "src.api.claude_proxy.handler.proxy_nonstream",
    "src.api.claude_proxy.handler.proxy_stream",
    "src.api.opencode_proxy.handler.nonstream_executor",
    "src.api.opencode_proxy.handler.response",
    "src.api.opencode_proxy.handler.stream_executor",
    "src.server.pass_through_server.routes.gemini_handlers",
    "src.server.pass_through_server.routes.gemini_streaming",
    "src.server.openai_server.routes.completions.legacy",
    "src.server.openai_server.routes.completions.responses",
)

# Modules that bind `acompletion` by name at import, so patching the
# `pool_manager` singleton does not reach them.
_ACOMPLETION_IMPORTERS: Tuple[str, ...] = (
    "src.core.orchestration.pool_manager",
    "src.api.opencode_proxy.handler.nonstream_executor",
    "src.server.openai_server.routes.completions.admin_probe",
    "src.console.interactive.endpoint",
)


# ── response shapes ────────────────────────────────────────────────────────
# Named after what they mirror, not after the OpenAI SDK classes: these are
# duck-types of what the router actually reads, so they carry exactly the
# attributes a consumer touches and nothing that needs a real SDK installed.

class FakeMessage:
    """`response.choices[0].message` in the chat-completions dialect."""

    def __init__(self, content: Optional[str] = None,
                 tool_calls: Optional[List[Dict[str, Any]]] = None,
                 *, reasoning_content: Optional[str] = None,
                 thought_signature: Optional[str] = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []
        self.reasoning_content = reasoning_content
        self.thought_signature = thought_signature

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class FakeChoice:
    def __init__(self, message: FakeMessage, finish_reason: str = "stop",
                 index: int = 0) -> None:
        self.message = message
        self.finish_reason = finish_reason
        self.index = index


class FakeResponse:
    """What `pool_manager.call_nonstream` hands back under `"response"`."""

    def __init__(self, message: Optional[FakeMessage] = None, *,
                 prompt_tokens: int = DEFAULT_INPUT_TOKENS,
                 completion_tokens: int = DEFAULT_OUTPUT_TOKENS,
                 finish_reason: str = "stop") -> None:
        self.id = "chatcmpl-fake0000000000"
        self.object = "chat.completion"
        self.model = DEFAULT_MODEL_ID
        self.choices = [FakeChoice(message if message is not None
                                   else FakeMessage(""), finish_reason)]
        self.usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }


class FakeDelta:
    """`chunk.choices[0].delta` for a streamed turn."""

    def __init__(self, content: Optional[str] = None,
                 tool_calls: Optional[List[Dict[str, Any]]] = None,
                 *, reasoning_content: Optional[str] = None,
                 thought: Optional[str] = None,
                 thought_signature: Optional[str] = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []
        self.reasoning_content = reasoning_content
        self.thought = thought
        self.thought_signature = thought_signature

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class FakeStreamChoice:
    def __init__(self, delta: FakeDelta, finish_reason: Optional[str] = None,
                 index: int = 0) -> None:
        self.delta = delta
        self.finish_reason = finish_reason
        self.index = index


class FakeStreamChunk:
    """One streamed chunk. `choices` is empty when there is nothing to report,
    which is how a usage-only final chunk arrives from a real provider."""

    def __init__(self, delta: Optional[FakeDelta] = None,
                 finish_reason: Optional[str] = None,
                 usage: Optional[Dict[str, int]] = None) -> None:
        self.choices = ([FakeStreamChoice(delta, finish_reason)]
                        if (delta is not None or finish_reason is not None)
                        else [])
        self.usage = usage

    @property
    def text(self) -> str:
        return "".join(delta.content or ""
                       for choice in self.choices
                       for delta in [choice.delta] if delta.content)


def text_chunk(text: str) -> FakeStreamChunk:
    return FakeStreamChunk(FakeDelta(content=text))


class _DualToolCall:
    """A tool call readable both ways, because the two adapters read it both ways.

    A real Gemini response carries `types.SimpleNamespace` objects
    (`gemini_facade._parse_native_nonstream`), so `tc.function.name` is the
    spelling production uses. `build_response` in the OpenAI proxy reads exactly
    that: `getattr(tc, "function", None)` then `getattr(fn, "name", "")`.

    A plain dict is a *plausible* fake and a wrong one. The Anthropic proxy
    branches on `isinstance(tc, dict)` and so tolerates it, but the OpenAI
    builder does not — it gets `None` and emits `"name": "", "arguments": ""`.
    The test still passes, because it asserts the empty string the fake caused.
    That is the worst kind of fixture bug: it makes the adapter look covered
    while the adapter is in fact never exercised.

    So the fake emits an object with both spellings. `__getitem__` and `.get`
    keep the Anthropic branch and the existing dict-shaped assertions working;
    the attributes give the OpenAI builder something real to read.
    """

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = dict(data)
        fn = self._data.get("function")
        self.function = _DualFunction(fn) if isinstance(fn, dict) else fn

    # attribute access for `.id` / `.type` / `.index`
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._data.get(name)

    # mapping access for `tc["function"]["name"]` and `tc.get("function")`
    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __repr__(self) -> str:                      # pragma: no cover - debug aid
        return f"_DualToolCall({self._data!r})"


class _DualFunction:
    """The `function` half of a `_DualToolCall`."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = dict(data)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._data.get(name)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


def dual(data: Dict[str, Any]) -> _DualToolCall:
    """Wrap a tool-call dict so it answers to attributes and to `[]`."""
    return _DualToolCall(data)


def tool_call_chunk(name: str, arguments: Dict[str, Any],
                    call_id: str = DEFAULT_TOOL_CALL_ID,
                    index: int = 0) -> FakeStreamChunk:
    return FakeStreamChunk(FakeDelta(tool_calls=[dual({
        "id": call_id, "type": "function", "index": index,
        "function": {"name": name,
                     "arguments": json.dumps(arguments, sort_keys=True,
                                             ensure_ascii=False)},
    })]))


def finish_chunk(reason: str = "stop",
                 usage: Optional[Dict[str, int]] = None) -> FakeStreamChunk:
    """The terminal chunk: a finish reason, and usage when the provider has it."""
    return FakeStreamChunk(FakeDelta(), finish_reason=reason, usage=usage)


def sse_frame(event: str, data: Any) -> str:
    """One SSE frame, rendered the way a proxy on the wire renders one.

    `sort_keys` for the same reason `JsonTurn` sorts: a frame a test asserts on
    has to be the same bytes twice, and dict order is not a guarantee.
    """
    return (f"event: {event}\n"
            f"data: {json.dumps(data, sort_keys=True, ensure_ascii=False)}\n\n")


# ── provider errors ────────────────────────────────────────────────────────
# The messages are the ones providers actually send, because this repo
# classifies errors by string content (`src/core/providers/gemini/error.py`) and
# a fake saying "error 429" would classify differently from the real thing.

class FakeProviderError(RuntimeError):
    """An HTTP failure from a provider, carrying the status the SDK would."""

    def __init__(self, message: str, *, status_code: Optional[int] = None,
                 body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = message if body is None else body


class FakeProviderTimeout(TimeoutError):
    """A read timeout. `asyncio.TimeoutError` is this same class on 3.11+."""


class MalformedResponse:
    """What a provider returns when something in front of it answers with HTML.

    Not an exception: the request succeeded, the payload is unusable. `text` is
    None (a real decode failure gives no text), `choices` is None rather than
    missing, so `getattr(resp, "text", "") or ""` — the pattern used all over
    the router — degrades to empty instead of raising.
    """

    DEFAULT_BODY = "<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>"

    def __init__(self, body: Optional[str] = None) -> None:
        self.raw_body = self.DEFAULT_BODY if body is None else body
        self.text = None
        self.choices = None
        self.usage = None
        self.candidates = None

    def json(self) -> Any:
        raise ValueError(
            f"Expecting value: line 1 column 1 (char 0) — {self.raw_body[:60]}")

    def __repr__(self) -> str:                      # pragma: no cover - debug aid
        return f"MalformedResponse({self.raw_body[:32]!r})"


def rate_limit_error(message: Optional[str] = None) -> FakeProviderError:
    msg = message or (
        "429 RESOURCE_EXHAUSTED: Quota exceeded for quota metric "
        "'Generate Content requests per minute'")
    return FakeProviderError(msg, status_code=429, body={
        "error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": msg}})


def server_error(status: int = 503,
                 message: Optional[str] = None) -> FakeProviderError:
    msg = message or f"{status} UNAVAILABLE: The model is overloaded."
    return FakeProviderError(msg, status_code=status, body={
        "error": {"code": status, "status": "UNAVAILABLE", "message": msg}})


def timeout_error(message: Optional[str] = None) -> FakeProviderTimeout:
    # "deadline exceeded" is the phrase the classifier looks for, so a timeout
    # fake classifies the way a real timeout does instead of being a special case.
    return FakeProviderTimeout(
        message or "Read timeout: request timeout, deadline exceeded after 30s")


def malformed_error(body: Optional[str] = None) -> MalformedResponse:
    return MalformedResponse(body)


# ── turns ──────────────────────────────────────────────────────────────────
# A turn is one answer from the provider. They are explicit objects rather than
# duck-typed guesses because a bare `str` is a perfectly reasonable stream frame
# and a perfectly reasonable text turn, and guessing between them is how a fake
# starts passing for the wrong reason.

class TextTurn:
    """A non-stream turn whose assistant message is `text`."""

    def __init__(self, text: str = DEFAULT_TEXT) -> None:
        self.text = text


class JsonTurn:
    """A non-stream turn whose text is `payload`, serialised deterministically.

    `sort_keys` is the reason this is not just `json.dumps(payload)`: two runs
    must produce the same bytes, and dict order is not a guarantee.
    """

    def __init__(self, payload: Any) -> None:
        self.payload = payload

    @property
    def text(self) -> str:
        return json.dumps(self.payload, sort_keys=True, ensure_ascii=False)


class ToolCallTurn:
    """A non-stream turn that calls one function."""

    def __init__(self, name: str, arguments: Dict[str, Any],
                 call_id: str = DEFAULT_TOOL_CALL_ID,
                 finish_reason: str = "tool_calls") -> None:
        self.message = FakeMessage(content="", tool_calls=[dual({
            "id": call_id, "type": "function", "index": 0,
            "function": {"name": name,
                         "arguments": json.dumps(arguments, sort_keys=True,
                                                 ensure_ascii=False)},
        })])
        self.finish_reason = finish_reason
        self.text = ""


class StreamTurn:
    """A streamed turn: chunks in order, then whatever finish carries."""

    def __init__(self, chunks: Sequence[Any]) -> None:
        self.chunks = list(chunks)


class SSETurn:
    """Raw SSE frames, yielded verbatim by `call_stream`.

    For code that consumes frames rather than chunks — a custom endpoint
    forwarding an upstream SSE body, say. Rendering frames here would duplicate
    the proxy's own SSE writer and drift from it, which is the one thing a fake
    must not do.
    """

    def __init__(self, frames: Iterable[Any]) -> None:
        self.frames = [f if isinstance(f, str) else sse_frame(*f) for f in frames]


class FailureTurn:
    """Raises. `error` may be an exception or a zero-argument factory.

    A factory is accepted so a scripted retry does not re-raise one instance
    twice and grow its traceback chain across attempts.
    """

    def __init__(self, error: Any) -> None:
        self._error = error

    def build(self) -> BaseException:
        error = self._error() if callable(self._error) else self._error
        if isinstance(error, BaseException):
            return error
        return FakeProviderError(str(error))


def _coerce(turn: Any) -> Any:
    """Accept the shorthands (`str`, `dict`, exception) next to the turn types."""
    if isinstance(turn, str):
        return TextTurn(turn)
    if isinstance(turn, dict):
        return JsonTurn(turn)
    if isinstance(turn, BaseException):
        return FailureTurn(turn)
    return turn


# ── the provider ───────────────────────────────────────────────────────────

class FakeProvider:
    """A scripted provider that satisfies every call shape the router uses.

    Turn kinds are consumed by the entry point that matches them: a `StreamTurn`
    only has meaning to `call_stream`, a `TextTurn` to `call_nonstream`. Each
    entry point advances the same script and repeats the last turn when it runs
    out, so a test that makes an unplanned extra call still gets an answer.
    """

    def __init__(self, *turns: Any, api_key: str = DEFAULT_API_KEY,
                 model_id: str = DEFAULT_MODEL_ID,
                 model_alias: str = DEFAULT_MODEL_ALIAS,
                 input_tokens: int = DEFAULT_INPUT_TOKENS,
                 output_tokens: int = DEFAULT_OUTPUT_TOKENS) -> None:
        self.turns: List[Any] = [_coerce(t) for t in (turns or (TextTurn(),))]
        self.api_key = api_key
        self.model_id = model_id
        # The native Gemini stream envelope is keyed `model_alias`, not
        # `model_id` (manager.py:442), and `stream_gemini_native` reads it back
        # out to report which model actually answered. Distinct attributes so a
        # test can tell a routed alias from the concrete model id behind it.
        self.model_alias = model_alias
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.calls: List[Dict[str, Any]] = []          # kwargs, in order
        self.call_kinds: List[str] = []                 # entry point per call
        self.usage_log: List[Dict[str, Any]] = []       # every log_usage call

    # ── script access ──────────────────────────────────────────────────────

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def calls_of(self, kind: str) -> List[Dict[str, Any]]:
        return [kw for kw, k in zip(self.calls, self.call_kinds) if k == kind]

    def _take(self, kind: str, kwargs: Dict[str, Any]) -> Any:
        self.calls.append(dict(kwargs))
        self.call_kinds.append(kind)
        index = len(self.calls) - 1
        return self.turns[index] if index < len(self.turns) else self.turns[-1]

    async def _resolve(self, turn: Any, kwargs: Dict[str, Any]) -> Any:
        """A callable turn gets called, with the call's own kwargs."""
        if callable(turn) and not isinstance(turn, type):
            result = turn(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            return _coerce(result)
        return turn

    # ── pool_manager seams ─────────────────────────────────────────────────

    async def call_nonstream(self, **kwargs: Any) -> Dict[str, Any]:
        turn = await self._resolve(self._take("call_nonstream", kwargs), kwargs)
        return {
            "response": await self._response_for(turn),
            "api_key": self.api_key,
            "model_id": self.model_id,
            "input_tokens": self.input_tokens,
            "reservation": {},
        }

    async def acompletion(self, **kwargs: Any) -> Any:
        """The openai-compatible entry point: the response, not the envelope."""
        result = await self.call_nonstream(**kwargs)
        return result["response"]

    async def call_stream(self, **kwargs: Any):
        """An async iterator of the `{chunk, api_key, model_id, ...}` envelope."""
        turn = await self._resolve(self._take("call_stream", kwargs), kwargs)
        if isinstance(turn, FailureTurn):
            raise turn.build()
        if isinstance(turn, SSETurn):
            for frame in turn.frames:
                yield self._item(frame)
            return
        for chunk in self._chunks_for(turn):
            yield self._item(chunk)

    # ── api_manager seams ──────────────────────────────────────────────────

    async def call_gemini(self, **kwargs: Any) -> Dict[str, Any]:
        turn = await self._resolve(self._take("call_gemini", kwargs), kwargs)
        if isinstance(turn, FailureTurn):
            raise turn.build()
        response = await self._response_for(turn)
        text = getattr(turn, "text", None) or ""
        if isinstance(response, MalformedResponse):
            response = _GeminiResponse(None, None, self.input_tokens,
                                       self.output_tokens, self.model_id)
        else:
            fn = None
            raw = getattr(turn, "message", None)
            calls = getattr(raw, "tool_calls", None) or []
            if calls:
                tc = calls[0]
                f = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
                name = f.get("name") if isinstance(f, dict) else getattr(f, "name", "")
                raw_args = (f.get("arguments") if isinstance(f, dict)
                            else getattr(f, "arguments", "{}"))
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}
                if name:
                    fn = {"name": name, "args": args}
            response = _GeminiResponse(text, [], self.input_tokens,
                                       self.output_tokens, self.model_id,
                                       function_call=fn)
        return {
            "response": response,
            "api_key": self.api_key,
            "model_id": self.model_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }

    async def call_gemini_json(self, **kwargs: Any) -> Dict[str, Any]:
        turn = await self._resolve(self._take("call_gemini_json", kwargs), kwargs)
        if isinstance(turn, FailureTurn):
            raise turn.build()
        return {
            "text": getattr(turn, "text", "") or "",
            "api_key": self.api_key,
            "model_id": self.model_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }

    async def call_gemini_stream(self, **kwargs: Any):
        """Yield the envelope the *native* Gemini stream consumer expects.

        Deliberately not `call_stream`. `stream_gemini_native` reads
        `chunk_data["response_chunk"]` — a plain dict already serialised by
        `dump_generation_response` — and re-dumps it verbatim onto the wire.
        The pool_manager stream envelope is keyed `"chunk"` and holds an
        object, so forwarding one shape into the other consumer raises
        `KeyError` deep inside the SSE writer and the test would report a
        transport bug rather than a fixture bug.
        """
        turn = await self._resolve(self._take("call_gemini_stream", kwargs), kwargs)
        if isinstance(turn, FailureTurn):
            raise turn.build()
        if isinstance(turn, SSETurn):
            # Already wire-format; hand the frames back untouched.
            for frame in turn.frames:
                yield self._genai_item(frame)
            return
        for chunk in self._chunks_for(turn):
            yield self._genai_item(_genai_chunk_dict(chunk, self.output_tokens))

    # ── installation ───────────────────────────────────────────────────────

    def install(self, monkeypatch, *, gemini: bool = True,
                usage_log: bool = True) -> "FakeProvider":
        """Replace the provider seams for the duration of one test.

        Patches the *singletons*, not the importing modules: the proxies hold a
        reference to `pool_manager`/`api_manager`, so one patch reaches every
        caller, including the ones that import lazily inside a function.

        `usage_log` stubs `log_usage` too. Left on, a fake call would append a
        usage row to the developer's `usage.db` — which is a real cost and which
        the session guard in conftest would (correctly) fail the suite over.
        """
        from src.core.orchestration.pool_manager import pool_manager as _pool

        monkeypatch.setattr(_pool, "call_nonstream", self.call_nonstream,
                            raising=False)
        monkeypatch.setattr(_pool, "call_stream", self.call_stream,
                            raising=False)
        self._patch_acompletion(monkeypatch)

        if gemini:
            from src.core.providers.gemini.manager import api_manager

            monkeypatch.setattr(api_manager, "call_gemini", self.call_gemini,
                                raising=False)
            monkeypatch.setattr(api_manager, "call_gemini_json",
                                self.call_gemini_json, raising=False)
            monkeypatch.setattr(api_manager, "call_gemini_stream",
                                self.call_gemini_stream, raising=False)

        if usage_log:
            self._stub_usage_log(monkeypatch)
        return self

    def _patch_acompletion(self, monkeypatch) -> None:
        """Cover `acompletion` everywhere it is reachable from.

        `pool_manager` is a singleton, so patching it covers the proxies that
        hold a reference to it. `acompletion` is the exception: four modules
        import it *by name* at module scope, so they each bound their own
        reference and patching `pool_manager.acompletion` left every one of
        them still pointing at the real facade. `opencode_proxy`'s
        non-streaming path is the one that matters, and it is exactly the path
        that went unmocked.

        Both ends are patched: the `gemini_facade` attribute for the sites that
        import lazily inside a function, and each binding for the ones that
        bound it at import.
        """
        from src.core.providers.gemini import facade as gemini_facade

        monkeypatch.setattr(gemini_facade, "acompletion", self.acompletion,
                            raising=False)
        for module_path in _ACOMPLETION_IMPORTERS:
            with suppress(ImportError):
                monkeypatch.setattr(importlib.import_module(module_path),
                                    "acompletion", self.acompletion,
                                    raising=False)

    def _stub_usage_log(self, monkeypatch) -> None:
        async def _record(*args: Any, **kwargs: Any) -> None:
            self.usage_log.append({"args": args, "kwargs": kwargs})

        # The lazy sites read the attribute off the module at call time; the
        # proxy modules bind it at import. Both need the stub, and a module that
        # cannot be imported here is one this test does not exercise.
        #
        # `import_module` and not `__import__(parent, fromlist=[leaf])`: the
        # latter returns the *parent package*, so every one of these landed on
        # `src.core` and `src.api...handler` instead of the module named. The
        # stub was installed everywhere except where it was needed, and the
        # symptom was a fake call that still wrote a usage row.
        for module_path in _USAGE_LOG_MODULES:
            with suppress(ImportError):
                module = importlib.import_module(module_path)
                monkeypatch.setattr(module, "log_usage", _record, raising=False)

    # ── turn → payload ─────────────────────────────────────────────────────

    async def _response_for(self, turn: Any) -> Any:
        if isinstance(turn, FailureTurn):
            raise turn.build()
        if isinstance(turn, MalformedResponse):
            return turn
        if isinstance(turn, FakeResponse):
            return turn
        # The provider's own token counts, not the class defaults. They were
        # dropped here, so `text_scenario(output_tokens=512)` reported 42 and a
        # test asserting the proxy billed what the provider said was asserting
        # the opposite of what it meant: it passed only because both numbers
        # were the default.
        message = getattr(turn, "message", None)
        if message is not None:
            return FakeResponse(message,
                                prompt_tokens=self.input_tokens,
                                completion_tokens=self.output_tokens,
                                finish_reason=getattr(turn, "finish_reason", "stop"))
        return FakeResponse(FakeMessage(getattr(turn, "text", "") or ""),
                            prompt_tokens=self.input_tokens,
                            completion_tokens=self.output_tokens)

    def _chunks_for(self, turn: Any) -> List[Any]:
        """The chunk list a streamed turn means, whatever kind the turn is."""
        if isinstance(turn, StreamTurn):
            return list(turn.chunks)
        if isinstance(turn, FakeResponse):
            text = turn.choices[0].message.content or ""
        elif isinstance(turn, ToolCallTurn):
            return [tool_call_chunk(
                turn.message.tool_calls[0]["function"]["name"],
                json.loads(turn.message.tool_calls[0]["function"]["arguments"]),
                turn.message.tool_calls[0]["id"])]
        elif isinstance(turn, FakeStreamChunk):
            return [turn]
        else:
            text = getattr(turn, "text", "") or ""
        if not text:
            return [finish_chunk()]
        return [text_chunk(text)]

    def _item(self, chunk: Any) -> Dict[str, Any]:
        return {"chunk": chunk, "api_key": self.api_key,
                "model_id": self.model_id, "input_tokens": self.input_tokens,
                "reservation": {}}

    def _genai_item(self, response_chunk: Any) -> Dict[str, Any]:
        return {"response_chunk": response_chunk,
                "model_alias": self.model_alias,
                "model_id": self.model_id,
                "api_key": self.api_key}


class _GeminiResponse:
    """What `api_manager.call_gemini` returns under `"response"`.

    Carries the GenAI response body the native `:generateContent` handler reads.
    It has to serialise, because `_handle_gemini_native_inner` does not return
    the SDK object — it runs `dump_generation_response(response_obj)`, which
    calls `response.model_dump_json(by_alias=True, exclude_none=True,
    exclude={...})` and hands the client the resulting JSON. A fake without
    that method raised `AttributeError` inside the handler, which
    `_classify_native_failure` turned into a 500: the request "failed", for a
    reason that had nothing to do with the code under test.

    Router search reads `response.candidates[0].Router search_metadata.Router search_chunks`;
    a fake with no candidates must still *have* the attribute, so a consumer that
    probes for it gets `[]` instead of an AttributeError.
    """

    def __init__(self, text: Optional[str], candidates: Optional[List[Any]] = None,
                 input_tokens: int = DEFAULT_INPUT_TOKENS,
                 output_tokens: int = DEFAULT_OUTPUT_TOKENS,
                 model_id: str = DEFAULT_MODEL_ID,
                 function_call: Optional[Dict[str, Any]] = None) -> None:
        self.text = text
        self.candidates = candidates if candidates is not None else []
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.model_id = model_id
        self.function_call = function_call
        self.usage_metadata = _GenaiUsage(input_tokens, output_tokens)

    def model_dump(self, **kwargs: Any) -> Dict[str, Any]:
        return self.body()

    def model_dump_json(self, **kwargs: Any) -> str:
        """The serialiser `dump_generation_response` calls.

        `exclude` is honoured for the three SDK-local fields the real function
        strips. None of them appear in `body()`, so excluding them is a no-op —
        which is the point: the native body the fake produces must already be
        client-safe, so the filter has nothing left to do.
        """
        return json.dumps(self.body(), sort_keys=True, ensure_ascii=False)

    def body(self) -> Dict[str, Any]:
        """The GenAI wire body for a non-stream generation."""
        part: Dict[str, Any] = {}
        if self.text:
            part["text"] = self.text
        if self.function_call:
            part["functionCall"] = self.function_call
        candidates: List[Dict[str, Any]] = []
        if part:
            candidates.append({
                "content": {"role": "model", "parts": [part]},
                "finishReason": "STOP",
                "index": 0,
            })
        body: Dict[str, Any] = {
            "candidates": candidates,
            "usageMetadata": {
                "promptTokenCount": self.input_tokens,
                "candidatesTokenCount": self.output_tokens,
                "totalTokenCount": self.input_tokens + self.output_tokens,
            },
            "modelVersion": self.model_id,
        }
        return body


class _GenaiUsage:
    """`response.usage_metadata` — read by `extract_stream_usage`."""

    def __init__(self, prompt_token_count: int, candidates_token_count: int) -> None:
        self.prompt_token_count = prompt_token_count
        self.candidates_token_count = candidates_token_count
        self.total_token_count = prompt_token_count + candidates_token_count


def _genai_chunk_dict(chunk: Any, output_tokens: int) -> Dict[str, Any]:
    """One OpenAI-shaped chunk re-expressed as a GenAI wire chunk.

    The native stream consumer never sees `FakeStreamChunk`; it is handed a dict
    and `json.dumps` it straight onto the wire. So the conversion happens here,
    once, rather than in every test that drives `:streamGenerateContent`.
    """
    candidate: Dict[str, Any] = {"content": {"role": "model"}, "index": 0}
    if not chunk.choices:
        body: Dict[str, Any] = {"candidates": [candidate]}
        if chunk.usage:
            body["usageMetadata"] = {
                "promptTokenCount": chunk.usage.get("prompt_tokens", 0),
                "candidatesTokenCount": chunk.usage.get("completion_tokens", 0),
                "totalTokenCount": (chunk.usage.get("prompt_tokens", 0)
                                    + chunk.usage.get("completion_tokens", 0)),
            }
        return body

    choice = chunk.choices[0]
    delta = choice.delta
    parts: List[Dict[str, Any]] = []
    text = getattr(delta, "content", None)
    if text:
        parts.append({"text": text})
    for tc in (getattr(delta, "tool_calls", None) or []):
        fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
        name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", "")
        raw_args = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", "")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except json.JSONDecodeError:
            args = {}
        if name:
            parts.append({"functionCall": {"name": name, "args": args}})
    if parts:
        candidate["content"]["parts"] = parts
    finish = choice.finish_reason
    if finish:
        candidate["finishReason"] = "STOP" if finish == "stop" else str(finish).upper()

    body = {"candidates": [candidate]}
    if chunk.usage:
        body["usageMetadata"] = {
            "promptTokenCount": chunk.usage.get("prompt_tokens", 0),
            "candidatesTokenCount": chunk.usage.get("completion_tokens", 0),
            "totalTokenCount": (chunk.usage.get("prompt_tokens", 0)
                                + chunk.usage.get("completion_tokens", 0)),
        }
    return body


# ── scenarios ──────────────────────────────────────────────────────────────

def text_scenario(text: str = DEFAULT_TEXT, **kwargs: Any) -> FakeProvider:
    """One non-stream text answer. The baseline every other scenario extends."""
    return FakeProvider(TextTurn(text), **kwargs)


def json_scenario(payload: Any, **kwargs: Any) -> FakeProvider:
    """One non-stream answer whose text is `payload` as JSON."""
    return FakeProvider(JsonTurn(payload), **kwargs)


def stream_scenario(*pieces: Any, finish_reason: str = "stop",
                    usage: Optional[Dict[str, int]] = None,
                    **kwargs: Any) -> FakeProvider:
    """A streamed answer: one chunk per piece, then the terminal chunk.

    A piece may be a string (a text delta) or a ready-made chunk.
    `usage` is attached to the terminal chunk, which is where a provider reports
    it and where a router that charges for tokens has to look.

    Without an explicit `usage`, the terminal chunk carries the *provider's own*
    counts from `**kwargs` — not the module defaults. Hard-coding the defaults
    here meant `stream_scenario("hi", output_tokens=512)` reported 42, and a test
    asserting the proxy billed what the provider said was asserting the
    opposite of what it meant: it passed only because both numbers agreed.
    """
    provider = FakeProvider(*[], **kwargs)
    chunks = [p if isinstance(p, FakeStreamChunk) else text_chunk(str(p))
              for p in pieces]
    if usage is None:
        usage = {"prompt_tokens": provider.input_tokens,
                 "completion_tokens": provider.output_tokens}
    chunks.append(finish_chunk(finish_reason, usage))
    return FakeProvider(StreamTurn(chunks), **kwargs)


def sse_scenario(*frames: Any, **kwargs: Any) -> FakeProvider:
    """Raw SSE frames, yielded verbatim. Pair with `sse_frame(event, data)`."""
    return FakeProvider(SSETurn(frames), **kwargs)


def tool_call_scenario(name: str, arguments: Dict[str, Any], *,
                       call_id: str = DEFAULT_TOOL_CALL_ID,
                       then: Any = None, **kwargs: Any) -> FakeProvider:
    """A turn that calls a function, optionally followed by the answer turn.

    `then` defaults to a text turn, so a two-step conversation does not need a
    second provider: the same object scripts both turns.
    """
    first = ToolCallTurn(name, arguments, call_id=call_id)
    second = _coerce(then) if then is not None else TextTurn()
    return FakeProvider(first, second, **kwargs)


def _failure_scenario(error_factory, then: Any = None,
                      **kwargs: Any) -> FakeProvider:
    """A provider that fails every turn — unless `then` says what happens next."""
    if then is None:
        return FakeProvider(FailureTurn(error_factory), **kwargs)
    return FakeProvider(FailureTurn(error_factory), _coerce(then), **kwargs)


def rate_limit_scenario(message: Optional[str] = None, *, then: Any = None,
                        **kwargs: Any) -> FakeProvider:
    """HTTP 429. `then` turns it into the retry-then-success case."""
    return _failure_scenario(lambda: rate_limit_error(message), then, **kwargs)


def server_error_scenario(status: int = 503, message: Optional[str] = None, *,
                          then: Any = None, **kwargs: Any) -> FakeProvider:
    """HTTP 5xx. Classifies as `unavailable`, which retries with backoff."""
    return _failure_scenario(lambda: server_error(status, message), then, **kwargs)


def timeout_scenario(message: Optional[str] = None, *, then: Any = None,
                     **kwargs: Any) -> FakeProvider:
    """A read timeout — the failure a caller sees as a dropped connection."""
    return _failure_scenario(lambda: timeout_error(message), then, **kwargs)


def malformed_scenario(body: Optional[str] = None, *, then: Any = None,
                       **kwargs: Any) -> FakeProvider:
    """A 200 whose body is not the promised JSON.

    Not a failure turn: the request succeeded, so the router goes on to read a
    payload that is not there. `then` scripts what a retry would see next.
    """
    turns: List[Any] = [malformed_error(body)]
    if then is not None:
        turns.append(_coerce(then))
    return FakeProvider(*turns, **kwargs)


def retry_then_success_scenario(failures: int = 1, *, error: Any = None,
                                then: Any = None,
                                text: str = DEFAULT_TEXT,
                                **kwargs: Any) -> FakeProvider:
    """`failures` transient errors, then a good answer — the retry case.

    The default error is a 503 because that is the failure a router is *supposed*
    to retry: transient, and it says so in the message. `then` overrides the good
    turn (a tool call, another failure, whatever the test needs).
    """
    if error is None:
        factory: Any = server_error
    elif callable(error):
        factory = error
    else:
        factory = lambda: error                                  # noqa: E731
    good = _coerce(then) if then is not None else TextTurn(text)
    turns: List[Any] = [FailureTurn(factory) for _ in range(max(0, failures))]
    return FakeProvider(*turns, good, **kwargs)


# ── web search ─────────────────────────────────────────────────────────────

class FakeSearch:
    """A local stand-in for web search. No network, no Gemini Router search.

    Two seams, because they answer different questions:

    * `install` patches `src.tools.search.engine.manager.search_with_citations` — the HTTP
      scrape. `search_manager.execute_hybrid_search` still runs for real, so the
      test exercises the real engine dispatch and formatting.
    * `install_manager` patches `execute_hybrid_search` itself — for tests
      driving a route that only needs the finished `(text, citations)` pair.

    Both return the same `(text, citations)` tuple, so a test can move between
    them without its assertions changing.
    """

    def __init__(self, text: str = DEFAULT_TEXT,
                 citations: Optional[List[Dict[str, str]]] = None,
                 engine: str = "duckduckgo") -> None:
        self.text = text
        self.citations = [dict(c) for c in
                          (DEFAULT_CITATIONS if citations is None else citations)]
        self.engine = engine
        self.queries: List[List[str]] = []
        self.calls: List[Dict[str, Any]] = []

    async def __call__(self, queries: Sequence[str], search_engine: str = "auto",
                       auth_key_prefix: str = "",
                       account: Any = None, **kwargs: Any) -> Tuple[str, List[Dict[str, str]]]:
        self.queries.append([str(q) for q in queries])
        # `engine` is what this fixture stands in for. `search_engine` is what
        # the call site actually passed, which is the *default* and therefore
        # says nothing about the router — see the note on `install`. Recording
        # both keeps that difference visible instead of letting the second one
        # read as a verified fact.
        self.calls.append({"engine": self.engine,
                           "search_engine": search_engine,
                           "auth_key_prefix": auth_key_prefix,
                           "account": account, "kwargs": kwargs})
        return self.text, [dict(c) for c in self.citations]

    def install(self, monkeypatch) -> "FakeSearch":
        """Replace the DuckDuckGo scrape, leaving the search manager real.

        This seam sits *below* the engine decision. `run_single_query` branches
        on `search_engine` and then calls `search_with_citations(query)` with a
        single positional argument, so the engine that was chosen never reaches
        the scrape. A test cannot assert on it here. What it can assert — and
        what is worth asserting — is that the scrape was reached at all, since
        the `router_search` branch goes to Gemini instead and never arrives
        here. The charged/not-charged split is pinned one level up, by
        `test_search_endpoint.py::TestOnlyTheGroundedSearchSpendsQuota`.
        """
        with suppress(ImportError):
            import src.tools.search.engine.manager as ddg

            monkeypatch.setattr(ddg, "search_with_citations", self,
                                raising=False)
        return self

    def install_manager(self, monkeypatch) -> "FakeSearch":
        """Replace `execute_hybrid_search` — the seam the routes call."""
        from src.tools.search.orchestration import manager as search_manager

        monkeypatch.setattr(search_manager, "execute_hybrid_search", self,
                            raising=False)
        return self


def run(coro):
    """Run one coroutine to completion.

    The suite drives async code with `asyncio.run` rather than a running-loop
    fixture, so the kit does not impose a style on the tests that use it.
    """
    return asyncio.run(coro)


async def drain(stream) -> List[Dict[str, Any]]:
    """Collect a `call_stream` envelope into a list."""
    return [item async for item in stream]

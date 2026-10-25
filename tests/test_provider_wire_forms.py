"""One file, three wire forms: does the fake reach each real adapter?

The kit in `tests/fake_provider.py` was audited form by form, and the audit
found the seam was installed correctly everywhere it was *checked* while three
of its payloads did not match what the adapters actually read. Each of those
failed in the way that makes a fixture dangerous — the test passed:

  * `call_gemini_stream` yielded the pool_manager envelope, keyed `"chunk"`.
    The native consumer reads `chunk_data["response_chunk"]`, so every
    `:streamGenerateContent` test raised `KeyError` inside the SSE writer and
    blamed the transport.
  * `call_gemini` handed back an object with no `model_dump_json`. The native
    handler serialises through `dump_generation_response`, so the request died
    with an `AttributeError` that `_classify_native_failure` reported as a 500.
  * tool calls were plain dicts. The Anthropic proxy branches on
    `isinstance(tc, dict)` and tolerates that; `build_response` in the OpenAI
    proxy does `getattr(tc, "function", None)`, gets `None`, and emits
    `"name": "", "arguments": ""` — so a test asserting the tool call arrived
    would assert the empty string the fixture caused, and pass.

That is what this file exists to prevent: one test per form per behaviour, each
driving the *real* adapter rather than the fake's own methods. A fake that only
ever tests itself is not a fake; it is a tautology.

Three forms, and they are genuinely three:

  1. GenAI/Gemini native  — `:generateContent` / `:streamGenerateContent`, whose
     consumer is `api_manager.call_gemini*` and which serialises through
     `dump_generation_response`.
  2. OpenAI-compatible    — `/v1/chat/completions`, whose consumer is
     `pool_manager.call_nonstream` / `call_stream` plus `build_response`.
  3. Anthropic/Claude     — `/v1/messages`, whose consumer is the same pool
     seam but rendered through the Anthropic event dialect.

Nothing here reaches a network. `tests/network_guard.py` is autouse for the
whole run, so a seam that stopped covering would fail these tests rather than
quietly billing — the tripwire is active precisely because these are the tests
that would trip it.
"""

import asyncio
import json

import pytest

import fake_provider as fp

# ── shared fixtures ────────────────────────────────────────────────────────

CHAT_BODY = {"model": "gemini-flash",
             "messages": [{"role": "user", "content": "capital of France?"}]}

NATIVE_BODY = {"contents": [{"role": "user", "parts": [{"text": "capital of France?"}]}]}

# Provider wire tests use the fake upstream and must not consume the persisted
# account rate-limit budget from usage.db (or fail on a fresh CI database).
ADMIN = {"name": "anonymous", "tier": "admin", "enabled": True,
         "auth_key": "test-key-long-enough"}

# A tool the client declared, in the three dialects. The native one is the
# GenAI spelling, which `parse_gemini_tools` turns into a function declaration.
WEATHER_TOOL_OPENAI = {"type": "function",
                       "function": {"name": "get_weather", "description": "weather",
                                    "parameters": {"type": "object",
                                                   "properties": {"city": {"type": "string"}}}}}

WEATHER_TOOL_ANTHROPIC = {"name": "get_weather", "description": "weather",
                          "input_schema": {"type": "object",
                                           "properties": {"city": {"type": "string"}}}}

WEATHER_TOOL_NATIVE = {"functionDeclarations": [
    {"name": "get_weather", "description": "weather",
     "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}]}


def run(coro):
    """`fp.run`, named locally so the intent (drive a real adapter) is obvious."""
    return fp.run(coro)


def collect(stream):
    """Drain an async iterator into a list.

    Not a coroutine: `call_gemini_stream` is an async *generator*, so it is
    passed in already created and this only has to run the loop over it.
    """
    async def go():
        return [item async for item in stream]
    return fp.run(go())


def sse_payloads(raw_chunks):
    """The JSON body of each `data:` frame in an SSE response."""
    out = []
    for raw in raw_chunks:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        for line in text.splitlines():
            if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                out.append(json.loads(line[6:]))
    return out


def anthropic_events(raw_chunks):
    """(event_name, data) pairs from an Anthropic SSE stream."""
    events = []
    for name, data in _events(raw_chunks):
        events.append((name, data))
    return events


def _events(raw_chunks):
    for raw in raw_chunks:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        name = data = None
        for line in text.splitlines():
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            yield name, data


def anthropic_text(raw_chunks):
    return "".join(
        d["delta"].get("text", "") for n, d in _events(raw_chunks)
        if n == "content_block_delta" and d["delta"].get("type") == "text_delta")


# ── 1. GenAI / Gemini native ────────────────────────────────────────────────

class TestTheGenAINativeForm:
    """`:generateContent` — the one form with no OpenAI-shaped envelope at all.

    Its consumer is `api_manager.call_gemini`, and its response is *serialised*
    by `dump_generation_response` before it can reach a client. So a fake that
    only satisfies attribute reads is not enough: it has to be serialisable.
    """

    # ── the seam itself ──

    def test_the_seam_yields_the_key_the_native_consumer_reads(self):
        """`stream_gemini_native` reads `chunk_data["response_chunk"]`.

        Not `chunk`. That one key is the whole difference between the native
        stream working and raising KeyError inside the SSE writer.
        """
        fake = fp.stream_scenario("Hello")
        items = collect(fake.call_gemini_stream(model_alias="gemini-flash"))

        assert "response_chunk" in items[0], (
            "the native stream consumer reads chunk_data['response_chunk']; "
            f"the fake offered {sorted(items[0])}")
        assert "chunk" not in items[0], (
            "the pool_manager envelope leaked into the native stream")

    def test_the_response_chunk_is_a_dict_not_an_sdk_object(self):
        """It is `json.dumps`-ed straight onto the wire, so it must be a dict."""
        fake = fp.stream_scenario("Hello")
        items = collect(fake.call_gemini_stream(model_alias="gemini-flash"))

        assert isinstance(items[0]["response_chunk"], dict)

    def test_the_nonstream_response_serialises_the_way_the_handler_dumps_it(self):
        """`_handle_gemini_native_inner` runs `dump_generation_response` on it.

        Without `model_dump_json` the handler raises AttributeError and the
        failure is reported to the client as a 500 — a fixture bug presenting
        as a server fault.
        """
        from src.core.providers.gemini.response_dump import dump_generation_response

        fake = fp.text_scenario("Paris.")
        result = run(fake.call_gemini(model_alias="gemini-flash"))

        body = dump_generation_response(result["response"])
        assert body["candidates"][0]["content"]["parts"][0]["text"] == "Paris."

    def test_the_usage_metadata_survives_the_dump(self):
        from src.core.providers.gemini.response_dump import dump_generation_response

        fake = fp.text_scenario("hi", input_tokens=7000, output_tokens=120)
        result = run(fake.call_gemini(model_alias="gemini-flash"))

        usage = dump_generation_response(result["response"])["usageMetadata"]
        assert usage["promptTokenCount"] == 7000
        assert usage["candidatesTokenCount"] == 120

    # ── non-stream through the route ──

    def test_generate_content_returns_the_fixture_text(self, monkeypatch):
        fp.text_scenario("Paris is the capital of France.").install(monkeypatch)

        body = _post_native(monkeypatch, "generateContent")

        text = body["candidates"][0]["content"]["parts"][0]["text"]
        assert text == "Paris is the capital of France."

    def test_generate_content_body_is_the_genai_dialect(self, monkeypatch):
        """Not a chat-completion body wearing a GenAI URL."""
        fp.text_scenario("Paris.").install(monkeypatch)

        body = _post_native(monkeypatch, "generateContent")

        assert "candidates" in body
        assert "object" not in body, "an OpenAI chat.completion body leaked"
        assert body["candidates"][0]["finishReason"] == "STOP"

    def test_generate_content_reports_the_providers_usage(self, monkeypatch):
        fp.text_scenario("hi", input_tokens=8000, output_tokens=512).install(monkeypatch)

        body = _post_native(monkeypatch, "generateContent")

        assert body["usageMetadata"]["candidatesTokenCount"] == 512

    def test_generate_content_reaches_the_fake_with_its_arguments(self, monkeypatch):
        fake = fp.text_scenario().install(monkeypatch)

        _post_native(monkeypatch, "generateContent")

        assert fake.call_count == 1
        assert fake.calls[0]["model_alias"] == "gemini-flash"
        assert fake.call_kinds == ["call_gemini"]

    def test_a_function_call_lands_in_the_genai_parts(self, monkeypatch):
        """`functionCall` is the GenAI spelling of a tool call."""
        fp.tool_call_scenario("get_weather", {"city": "Paris"}).install(monkeypatch)

        body = _post_native(monkeypatch, "generateContent",
                            extra={"tools": [WEATHER_TOOL_NATIVE]})

        parts = body["candidates"][0]["content"]["parts"]
        assert parts[0]["functionCall"]["name"] == "get_weather"
        assert parts[0]["functionCall"]["args"] == {"city": "Paris"}

    # ── streaming through the route ──

    def test_stream_generate_content_streams_the_fixture(self, monkeypatch):
        fp.stream_scenario("Hello", " world").install(monkeypatch)

        frames = _post_native_stream(monkeypatch, "streamGenerateContent")

        streamed = "".join(
            p.get("text", "")
            for f in frames for c in f.get("candidates", [])
            for p in c.get("content", {}).get("parts", []))
        assert streamed == "Hello world"

    def test_the_stream_terminates_with_a_finish_reason(self, monkeypatch):
        fp.stream_scenario("Hello").install(monkeypatch)

        frames = _post_native_stream(monkeypatch, "streamGenerateContent")

        assert frames[-1]["candidates"][0]["finishReason"] == "STOP"

    def test_the_terminal_stream_frame_carries_usage(self, monkeypatch):
        fp.stream_scenario("done", input_tokens=3000).install(monkeypatch)

        frames = _post_native_stream(monkeypatch, "streamGenerateContent")

        assert frames[-1]["usageMetadata"]["promptTokenCount"] == 3000

    def test_the_stream_uses_the_genai_candidate_dialect(self, monkeypatch):
        fp.stream_scenario("Hello").install(monkeypatch)

        frames = _post_native_stream(monkeypatch, "streamGenerateContent")

        assert all("choices" not in f for f in frames), (
            "an OpenAI chunk leaked into the native stream")
        assert all(f["candidates"][0]["content"]["role"] == "model" for f in frames)

    # ── failures ──

    @pytest.mark.parametrize("scenario", [
        fp.rate_limit_scenario, fp.server_error_scenario, fp.timeout_scenario])
    def test_a_provider_failure_becomes_a_google_error_envelope(
            self, monkeypatch, scenario):
        """The envelope, not a stack trace.

        A native Gemini client cannot parse a `text/plain` 500, so what the
        route owes it is JSON carrying the three fields the SDK reads. The
        status code is a separate question, below.
        """
        scenario().install(monkeypatch)

        response = _post_native_raw(monkeypatch, "generateContent")

        assert set(response.json()["error"]) == {"code", "message", "status"}

    def test_a_timeout_is_reported_as_a_gateway_timeout(self, monkeypatch):
        fp.timeout_scenario().install(monkeypatch)

        response = _post_native_raw(monkeypatch, "generateContent")

        assert response.status_code == 504
        assert response.json()["error"]["status"] == "DEADLINE_EXCEEDED"

    def test_a_provider_quota_error_is_reported_as_an_internal_error(
            self, monkeypatch):
        """A finding, pinned rather than papered over.

        `_classify_native_failure` maps to 429 only on the *internal* sentinel
        strings `no_available_key` / `quota_exhausted`. A 429 that arrives from
        the provider itself matches neither, so it falls through to the default
        branch and the client is told 500 for what was in fact a quota error.

        That is the current behaviour and this test records it, so a change is a
        deliberate one. It is not fixed here: the fix belongs in the classifier,
        and changing it is a production decision about whether a provider 429
        should be reported as retryable — not a test-kit concern.
        """
        fp.rate_limit_scenario().install(monkeypatch)

        response = _post_native_raw(monkeypatch, "generateContent")

        assert response.status_code == 500
        assert response.json()["error"]["status"] == "INTERNAL"

    def test_the_classifier_maps_the_internal_sentinel_to_429(self, monkeypatch):
        """The branch that *does* produce a 429, so the gap above is bounded."""
        from src.server.pass_through_server.routes.gemini_handlers import (
            _classify_native_failure,
        )

        status, message = _classify_native_failure(RuntimeError("quota_exhausted"))

        assert status == 429
        assert "retry later" in message

    def test_a_stream_failure_is_reported_inside_the_stream(self, monkeypatch):
        """A stream has already sent 200, so the error rides as a frame."""
        fp.server_error_scenario().install(monkeypatch)

        response = _post_native_raw(monkeypatch, "streamGenerateContent")

        assert response.status_code == 200
        assert "error" in response.text

    def test_a_transient_failure_classifies_as_worth_retrying(self, monkeypatch):
        """The precondition for the retry loop that lives inside the seam.

        The fake replaces `api_manager.call_gemini`, so the manager's own retry
        loop is replaced along with it and cannot be observed from out here.
        What a caller *can* pin is the classification that loop branches on —
        that is the router's contract with itself, and it is adapter-independent.
        """
        from src.core.providers.gemini import error as gerror

        for scenario in (fp.rate_limit_scenario, fp.server_error_scenario,
                         fp.timeout_scenario):
            fake = scenario()
            with pytest.raises((fp.FakeProviderError, TimeoutError)) as exc:
                run(fake.call_gemini(model_alias="gemini-flash"))
            assert gerror.classify(str(exc.value)) in ("rate_limit", "unavailable"), (
                f"{scenario.__name__} is not classified as retryable")

    def test_the_script_recovers_when_the_caller_retries(self, monkeypatch):
        """The retry-then-success shape, driven at the seam the retry loop uses.

        `pool_manager` retries by calling the provider again, so a script whose
        first turn fails and whose second answers is exactly what a real retry
        sees. Both turns are asserted, so a fake that only ever answered — and
        hid a permanently broken first attempt — would fail here.
        """
        from src.core.providers.gemini import error as gerror

        fake = fp.retry_then_success_scenario(failures=1, text="recovered.")

        with pytest.raises(fp.FakeProviderError) as first:
            run(fake.call_gemini(model_alias="gemini-flash"))
        assert gerror.classify(str(first.value)) == "unavailable"

        retried = run(fake.call_gemini(model_alias="gemini-flash"))
        from src.core.providers.gemini.response_dump import dump_generation_response
        body = dump_generation_response(retried["response"])
        assert body["candidates"][0]["content"]["parts"][0]["text"] == "recovered."

    def test_the_json_seam_serves_a_search_manager_query(self, monkeypatch):
        """`call_gemini_json` is a third native entry point, used by search."""
        fake = fp.json_scenario({"queries": ["capital of France"]}).install(monkeypatch)

        result = run(fake.call_gemini_json(model_alias="gemini-flash-lite",
                                           system_instruction="s", prompt_text="p"))

        assert json.loads(result["text"]) == {"queries": ["capital of France"]}
        assert result["input_tokens"] == fp.DEFAULT_INPUT_TOKENS


# ── 2. OpenAI-compatible ───────────────────────────────────────────────────

class TestTheOpenAICompatibleForm:
    """`/v1/chat/completions` — the OpenAI dialect, via `build_response`."""

    def test_nonstream_returns_the_fixture_text(self, monkeypatch):
        fp.text_scenario("Paris is the capital of France.").install(monkeypatch)

        from src.api.opencode_proxy import opencode_proxy

        out = run(opencode_proxy.chat_completion(dict(CHAT_BODY)))

        assert out["choices"][0]["message"]["content"] == "Paris is the capital of France."

    def test_the_body_is_a_chat_completion(self, monkeypatch):
        fp.text_scenario("Paris.").install(monkeypatch)

        from src.api.opencode_proxy import opencode_proxy

        out = run(opencode_proxy.chat_completion(dict(CHAT_BODY)))

        assert out["object"] == "chat.completion"
        assert "candidates" not in out, "a GenAI body leaked into the OpenAI form"

    def test_usage_is_the_providers_numbers(self, monkeypatch):
        fp.text_scenario("hi", input_tokens=8000, output_tokens=512).install(monkeypatch)

        from src.api.opencode_proxy import opencode_proxy

        out = run(opencode_proxy.chat_completion(dict(CHAT_BODY)))

        assert out["usage"]["completion_tokens"] == 512
        assert out["usage"]["prompt_tokens"] == 8000

    def test_the_call_reaches_the_fake_with_its_arguments(self, monkeypatch):
        fake = fp.text_scenario().install(monkeypatch)

        from src.api.opencode_proxy import opencode_proxy

        run(opencode_proxy.chat_completion(dict(CHAT_BODY)))

        assert fake.call_kinds == ["call_nonstream"]
        assert fake.calls[0]["model_alias"] == "gemini-flash"
        assert fake.calls[0]["messages"][0]["content"] == "capital of France?"

    def test_a_json_answer_round_trips(self, monkeypatch):
        fp.json_scenario({"answer": "Paris", "confidence": 0.9}).install(monkeypatch)

        from src.api.opencode_proxy import opencode_proxy

        out = run(opencode_proxy.chat_completion(dict(CHAT_BODY)))

        content = out["choices"][0]["message"]["content"]
        assert json.loads(content) == {"answer": "Paris", "confidence": 0.9}

    def test_a_tool_call_keeps_its_name_and_arguments(self, monkeypatch):
        """The one the dict-shaped fixture got wrong, silently.

        `build_response` reads `getattr(tc, "function", None)`. A plain dict
        yields None, so the old fixture produced `name: ""` — and a test
        asserting the tool call arrived would assert that empty string and
        pass.
        """
        fp.tool_call_scenario("get_weather", {"city": "Paris"}).install(monkeypatch)

        from src.api.opencode_proxy import opencode_proxy

        out = run(opencode_proxy.chat_completion(
            dict(CHAT_BODY, tools=[WEATHER_TOOL_OPENAI])))

        call = out["choices"][0]["message"]["tool_calls"][0]
        assert call["function"]["name"] == "get_weather"
        assert json.loads(call["function"]["arguments"]) == {"city": "Paris"}
        assert out["choices"][0]["finish_reason"] == "tool_calls"

    # ── streaming ──

    def test_stream_emits_openai_chunks(self, monkeypatch):
        fp.stream_scenario("Hello", " world").install(monkeypatch)

        from src.api.opencode_proxy import opencode_proxy

        chunks = collect(opencode_proxy.stream_chat_completion(dict(CHAT_BODY)))

        streamed = "".join(
            p["choices"][0]["delta"].get("content", "")
            for p in sse_payloads(chunks))
        assert streamed == "Hello world"

    def test_the_stream_ends_with_done(self, monkeypatch):
        fp.stream_scenario("Hello").install(monkeypatch)

        from src.api.opencode_proxy import opencode_proxy

        chunks = collect(opencode_proxy.stream_chat_completion(dict(CHAT_BODY)))

        assert chunks[-1] == b"data: [DONE]\n\n"

    def test_every_stream_frame_is_an_openai_chunk(self, monkeypatch):
        fp.stream_scenario("Hello").install(monkeypatch)

        from src.api.opencode_proxy import opencode_proxy

        chunks = collect(opencode_proxy.stream_chat_completion(dict(CHAT_BODY)))

        for payload in sse_payloads(chunks):
            assert payload["object"] == "chat.completion.chunk"
            assert "candidates" not in payload, "a GenAI frame leaked into the OpenAI stream"

    def test_a_streamed_tool_call_keeps_its_name_and_arguments(self, monkeypatch):
        """Same fixture defect, on the streaming side of the same form."""
        fake = fp.FakeProvider(fp.StreamTurn([
            fp.tool_call_chunk("get_weather", {"city": "Paris"}),
            fp.finish_chunk("tool_calls")]))
        fake.install(monkeypatch)

        from src.api.opencode_proxy import opencode_proxy

        chunks = collect(opencode_proxy.stream_chat_completion(
            dict(CHAT_BODY, tools=[WEATHER_TOOL_OPENAI])))

        calls = [tc for p in sse_payloads(chunks)
                 for tc in p["choices"][0]["delta"].get("tool_calls", [])]
        assert calls, "no tool_call frame was emitted"
        assert calls[0]["function"]["name"] == "get_weather"
        assert json.loads(calls[0]["function"]["arguments"]) == {"city": "Paris"}

    # ── failures ──

    @pytest.mark.parametrize("scenario", [
        fp.rate_limit_scenario, fp.server_error_scenario, fp.timeout_scenario])
    def test_a_provider_failure_propagates_to_the_caller(self, monkeypatch, scenario):
        """The router does not paper over a provider outage with a fake answer."""
        scenario().install(monkeypatch)

        from src.api.opencode_proxy import opencode_proxy

        with pytest.raises((fp.FakeProviderError, TimeoutError)):
            run(opencode_proxy.chat_completion(dict(CHAT_BODY)))

    def test_the_error_classifies_the_way_the_router_classifies_it(self, monkeypatch):
        """The wording matters: the router classifies by string content."""
        from src.core.providers.gemini import error as gerror

        fp.server_error_scenario().install(monkeypatch)

        from src.api.opencode_proxy import opencode_proxy

        with pytest.raises(fp.FakeProviderError) as exc:
            run(opencode_proxy.chat_completion(dict(CHAT_BODY)))

        assert gerror.classify(str(exc.value)) == "unavailable"

    def test_a_stream_failure_propagates_rather_than_faking_a_stream(self, monkeypatch):
        fp.server_error_scenario().install(monkeypatch)

        from src.api.opencode_proxy import opencode_proxy

        with pytest.raises((fp.FakeProviderError, TimeoutError)):
            collect(opencode_proxy.stream_chat_completion(dict(CHAT_BODY)))

    def test_retry_then_success_answers_on_the_second_call(self, monkeypatch):
        """The retry-then-success shape, driven at the seam retry loops call.

        The fake replaces `pool_manager.call_nonstream`, so the manager's own
        retry loop goes with it. A script that fails once and then answers is
        what a real retry sees, and both turns are asserted — a fake that only
        ever answered would hide a permanently broken first attempt.
        """
        from src.core.providers.gemini import error as gerror

        fake = fp.retry_then_success_scenario(failures=1, text="recovered.")
        # Installed *before* the direct call. Without it the first turn is not
        # the fake's at all: `pool_manager` reaches the real facade, the network
        # guard blocks the connection, and the test hangs 115s in the pool's
        # acquire loop before reporting a pool timeout. The guard caught it; a
        # suite without one would have billed the request.
        fake.install(monkeypatch)

        with pytest.raises(fp.FakeProviderError) as first:
            run(fake.call_nonstream(model_alias="gemini-flash"))
        assert gerror.classify(str(first.value)) == "unavailable"

        from src.api.opencode_proxy import opencode_proxy
        out = run(opencode_proxy.chat_completion(dict(CHAT_BODY)))
        assert out["choices"][0]["message"]["content"] == "recovered."


# ── 3. Anthropic / Claude ───────────────────────────────────────────────────

class TestTheAnthropicForm:
    """`/v1/messages` — the Anthropic event dialect, same pool seam underneath.

    Covered here rather than in `test_fake_provider.py` because the point is the
    *form*: the same pool envelope rendered as Anthropic blocks and events. The
    Anthropic proxy tolerates a dict-shaped tool call, so the fixture defect
    above was invisible here — which is exactly why the OpenAI form needed its
    own coverage.
    """

    def test_nonstream_returns_anthropic_blocks(self, monkeypatch):
        fp.text_scenario("Paris is the capital of France.").install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        message = run(claude_proxy.create_message(dict(CHAT_BODY)))

        assert message["content"] == [
            {"type": "text", "text": "Paris is the capital of France."}]
        assert message["stop_reason"] == "end_turn"

    def test_compaction_summary_in_reasoning_is_returned_as_text(self, monkeypatch):
        fp.reasoning_scenario("Summary of the conversation.").install(monkeypatch)
        from src.api.claude_proxy import claude_proxy

        body = {
            "model": "gemini-flash",
            "messages": [{"role": "user", "content": "Write a summary of this conversation."}],
        }
        message = run(claude_proxy.create_message(body))
        assert message["content"] == [{"type": "text", "text": "Summary of the conversation."}]

    def test_compaction_summary_in_dict_message_is_returned_as_text(self, monkeypatch):
        """Provider adapters may return dict-shaped messages instead of SDK objects."""
        from types import SimpleNamespace

        from src.api.claude_proxy import claude_proxy

        async def call_nonstream(**kwargs):
            return {
                "response": SimpleNamespace(
                    choices=[SimpleNamespace(
                        message={
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "Summary from reasoning.",
                            "tool_calls": [],
                        },
                        finish_reason="stop",
                    )],
                    usage={"completion_tokens": 10},
                ),
                "api_key": "fake",
                "model_id": "gemini-flash",
            }

        monkeypatch.setattr("src.api.claude_proxy.handler.proxy_nonstream.pool_manager.call_nonstream", call_nonstream)
        body = {
            "model": "gemini-flash",
            "messages": [{"role": "user", "content": "Write a summary of this conversation."}],
        }
        message = run(claude_proxy.create_message(body))
        assert message["content"] == [{"type": "text", "text": "Summary from reasoning."}]

    def test_compaction_summary_in_anthropic_text_block_is_returned_as_text(self, monkeypatch):
        from types import SimpleNamespace

        from src.api.claude_proxy import claude_proxy

        async def call_nonstream(**kwargs):
            return {
                "response": SimpleNamespace(
                    choices=[SimpleNamespace(
                        message={
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Compacted summary block."}],
                            "tool_calls": [],
                        },
                        finish_reason="stop",
                    )],
                    usage={"completion_tokens": 8},
                ),
                "api_key": "fake",
                "model_id": "gemini-flash",
            }

        monkeypatch.setattr("src.api.claude_proxy.handler.proxy_nonstream.pool_manager.call_nonstream", call_nonstream)
        body = {
            "model": "gemini-flash",
            "messages": [{"role": "user", "content": "Write a summary of this conversation."}],
        }
        message = run(claude_proxy.create_message(body))
        assert message["content"] == [{"type": "text", "text": "Compacted summary block."}]

    def test_compaction_summary_in_reasoning_alias_is_returned_as_text(self, monkeypatch):
        from types import SimpleNamespace

        from src.api.claude_proxy import claude_proxy

        async def call_nonstream(**kwargs):
            return {
                "response": SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content="", reasoning="Compacted via reasoning alias."),
                        finish_reason="stop",
                    )],
                    usage={"completion_tokens": 8},
                ),
                "api_key": "fake",
                "model_id": "gemini-flash",
            }

        monkeypatch.setattr("src.api.claude_proxy.handler.proxy_nonstream.pool_manager.call_nonstream", call_nonstream)
        body = {
            "model": "gemini-flash",
            "messages": [{"role": "user", "content": "Write a summary of this conversation."}],
        }
        message = run(claude_proxy.create_message(body))
        assert message["content"] == [{"type": "text", "text": "Compacted via reasoning alias."}]

    def test_compaction_variants_strip_tools(self):
        from src.core.runtime.compaction_detect import is_compaction_request, strip_tools_for_compaction
        for prompt in (
            "Summarize this conversation for the next turn.",
            "Conversation compaction: preserve decisions and facts.",
            "Compact the conversation now.",
        ):
            messages = [{"role": "user", "content": prompt}]
            assert is_compaction_request(messages)
            body = {"tool_choice": "auto"}
            tools = [{"type": "function", "function": {"name": "Write"}}]
            strip_tools_for_compaction(body, messages, tools, is_claude_or_opencode=True)
            assert tools == []
            assert "tool_choice" not in body
            assert body["_router_compaction_request"] is True

    def test_anthropic_thinking_block_survives_nonstream_bridge(self):
        from src.core.providers.anthropic_wire.response import anthropic_to_openai_message

        converted = anthropic_to_openai_message({
            "id": "msg_compact",
            "model": "gemini-flash",
            "stop_reason": "end_turn",
            "content": [{"type": "thinking", "thinking": "Compacted summary."}],
        })
        assert converted["choices"][0]["message"]["content"] == "Compacted summary."

    def test_the_body_is_the_anthropic_dialect(self, monkeypatch):
        fp.text_scenario("Paris.").install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        message = run(claude_proxy.create_message(dict(CHAT_BODY)))

        assert message["type"] == "message"
        assert "choices" not in message, "an OpenAI body leaked into the Anthropic form"
        assert "usage" in message

    def test_usage_is_the_providers_numbers(self, monkeypatch):
        fp.text_scenario("hi", input_tokens=8000, output_tokens=512).install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        message = run(claude_proxy.create_message(dict(CHAT_BODY)))

        assert message["usage"]["output_tokens"] == 512

    def test_a_tool_call_becomes_a_tool_use_block(self, monkeypatch):
        fp.tool_call_scenario("get_weather", {"city": "Paris"}).install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        message = run(claude_proxy.create_message(
            dict(CHAT_BODY, tools=[WEATHER_TOOL_ANTHROPIC])))

        call = next(b for b in message["content"] if b["type"] == "tool_use")
        assert call["name"] == "get_weather"
        assert call["input"] == {"city": "Paris"}
        assert message["stop_reason"] == "tool_use"

    def test_a_json_answer_round_trips(self, monkeypatch):
        fp.json_scenario({"answer": "Paris"}).install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        message = run(claude_proxy.create_message(dict(CHAT_BODY)))

        text = "".join(b["text"] for b in message["content"] if b["type"] == "text")
        assert json.loads(text) == {"answer": "Paris"}

    def test_the_call_reaches_the_fake(self, monkeypatch):
        fake = fp.text_scenario().install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        run(claude_proxy.create_message(dict(CHAT_BODY)))

        assert fake.call_kinds == ["call_nonstream"]

    # ── streaming ──

    def test_stream_emits_anthropic_events(self, monkeypatch):
        fp.stream_scenario("Hello", " world").install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        chunks = collect(claude_proxy.stream_message(dict(CHAT_BODY)))

        assert anthropic_text(chunks) == "Hello world"

    def test_the_stream_opens_and_closes_a_message_block(self, monkeypatch):
        fp.stream_scenario("Hello").install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        names = [n for n, _ in anthropic_events(
            collect(claude_proxy.stream_message(dict(CHAT_BODY))))]

        assert "message_start" in names
        assert "content_block_start" in names
        assert "message_delta" in names

    def test_every_stream_frame_is_an_anthropic_event(self, monkeypatch):
        fp.stream_scenario("Hello").install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        chunks = collect(claude_proxy.stream_message(dict(CHAT_BODY)))

        for name, data in anthropic_events(chunks):
            assert data["type"] == name, (
                "an OpenAI chunk leaked into the Anthropic stream")
            assert "choices" not in data

    def test_a_streamed_tool_call_becomes_a_tool_use_block(self, monkeypatch):
        fake = fp.FakeProvider(fp.StreamTurn([
            fp.tool_call_chunk("get_weather", {"city": "Paris"}),
            fp.finish_chunk("tool_calls")]))
        fake.install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        chunks = collect(claude_proxy.stream_message(
            dict(CHAT_BODY, tools=[WEATHER_TOOL_ANTHROPIC])))

        blocks = [d for n, d in anthropic_events(chunks) if n == "content_block_start"]
        tool = next(b for b in blocks if b["content_block"]["type"] == "tool_use")
        assert tool["content_block"]["name"] == "get_weather"

    # ── failures ──

    @pytest.mark.parametrize("scenario", [
        fp.rate_limit_scenario, fp.server_error_scenario, fp.timeout_scenario])
    def test_a_provider_failure_propagates_on_the_nonstream_path(
            self, monkeypatch, scenario):
        scenario().install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        with pytest.raises((fp.FakeProviderError, TimeoutError)):
            run(claude_proxy.create_message(dict(CHAT_BODY)))

    def test_a_stream_failure_becomes_an_anthropic_error_event(self, monkeypatch):
        """The Anthropic dialect has no channel for a mid-stream failure.

        The stream has already committed a 200, so the error has to ride inside
        it as an `error` event — a client that only checked the status code
        would otherwise read a truncated answer as a complete one.
        """
        fp.server_error_scenario().install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        chunks = collect(claude_proxy.stream_message(dict(CHAT_BODY)))

        names = [n for n, _ in anthropic_events(chunks)]
        assert "error" in names

    def test_retry_then_success_answers_on_the_second_call(self, monkeypatch):
        """The retry-then-success shape, driven at the seam retry loops call.

        The fake replaces `pool_manager.call_nonstream`, so the manager's own
        retry loop goes with it. Both turns are asserted, so a fake that only
        ever answered would hide a permanently broken first attempt.
        """
        from src.core.providers.gemini import error as gerror

        fake = fp.retry_then_success_scenario(failures=1, text="recovered.")
        # Installed before the direct call — see the note in the OpenAI class.
        # Without it this reaches the real facade and hangs in pool acquire.
        fake.install(monkeypatch)

        with pytest.raises(fp.FakeProviderError) as first:
            run(fake.call_nonstream(model_alias="gemini-flash"))
        assert gerror.classify(str(first.value)) == "unavailable"

        from src.api.claude_proxy import claude_proxy
        message = run(claude_proxy.create_message(dict(CHAT_BODY)))
        text = "".join(b.get("text", "") for b in message["content"])
        assert text == "recovered."


# ── the fake cannot be pointed at a paid host ───────────────────────────────

class TestNoProviderCallIsPossible:
    """The guarantee that makes the assertions above mean anything.

    Each of these three forms has exactly one seam a test has to hit. If the
    fixture ever missed one, the test would still pass — it would just have
    made a real, billed request. So the tripwire is checked explicitly rather
    than assumed.
    """

    def test_installing_the_fake_leaves_no_paid_host_reachable(self, monkeypatch):
        from src.core.orchestration.pool_manager import pool_manager
        from src.core.providers.gemini.manager import api_manager

        fake = fp.text_scenario().install(monkeypatch)

        assert pool_manager.call_nonstream == fake.call_nonstream
        assert pool_manager.call_stream == fake.call_stream
        assert api_manager.call_gemini == fake.call_gemini
        assert api_manager.call_gemini_json == fake.call_gemini_json
        assert api_manager.call_gemini_stream == fake.call_gemini_stream

    def test_a_connection_to_a_provider_host_is_blocked(self):
        """The guard raises on the connect, before any DNS or TLS work.

        `expected_block` marks the attempt as the thing under test, so the
        teardown check does not then fail the test for doing its job. The host
        never resolves, so nothing leaves the machine.
        """
        import socket

        import network_guard

        with network_guard.expected_block():
            with pytest.raises(network_guard.LiveProviderBlocked):
                socket.create_connection(("api.anthropic.com", 443), timeout=1)

    @pytest.mark.parametrize("host", [
        "api.anthropic.com", "api.openai.com",
        "generativelanguage.googleapis.com", "aiplatform.googleapis.com",
    ])
    def test_every_paid_provider_host_is_blocked(self, host):
        import network_guard

        assert network_guard.is_blocked_host(host)

    @pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost", "example.org"])
    def test_a_local_fixture_host_is_not_blocked(self, host):
        """Blocking is not "no network" — a local HTTP fixture must still work."""
        import network_guard

        assert not network_guard.is_blocked_host(host.split(":")[0])

    def test_the_search_fixture_is_local(self, monkeypatch):
        """Web search must not reach Gemini Router search either."""
        fake = fp.text_scenario().install(monkeypatch)
        search = fp.FakeSearch(text="Paris.").install(monkeypatch)

        from src.tools.search.orchestration.manager import execute_hybrid_search

        text, _ = run(execute_hybrid_search(
            ["capital of France"], search_engine="duckduckgo",
            auth_key_prefix="g-enough", account=ADMIN))

        assert "Paris" in text
        assert fake.call_count == 0, "the DuckDuckGo path called a paid model"
        assert len(search.queries) == 1


# ── route helpers ───────────────────────────────────────────────────────────

def _post_native(monkeypatch, action, extra=None):
    body = dict(NATIVE_BODY, **(extra or {}))
    response = _post_native_raw(monkeypatch, action, body)
    assert response.status_code == 200, response.text
    return response.json()


def _post_native_stream(monkeypatch, action, extra=None):
    body = dict(NATIVE_BODY, **(extra or {}))
    response = _post_native_raw(monkeypatch, action, body)
    assert response.status_code == 200, response.text
    return sse_payloads(response.text.split("\n\n"))


def _post_native_raw(monkeypatch, action, body=None):
    from unittest.mock import patch
    from fastapi.testclient import TestClient

    from src.server.openai_server.routes.app_init import app
    import src.server.pass_through_server.routes.gemini_routes  # noqa: F401  (registers)
    from src.server.pass_through_server.routes import gemini_handlers

    with patch.object(gemini_handlers, "_check_auth") as auth:
        auth.return_value = dict(ADMIN)
        client = TestClient(app)
        return client.post(
            f"/v1beta/models/gemini-flash:{action}",
            json=body if body is not None else NATIVE_BODY,
            headers={"x-goog-api-key": ADMIN["auth_key"]})

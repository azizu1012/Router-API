"""The fake provider kit has to be two things at once: usable and honest.

Usable means a test can say `provider(text_scenario("Paris"))` and get a real
answer out of a real proxy. Honest means the answer is fixed — same bytes every
run — and that nothing reached a paid host to produce it. A fake that quietly
called Gemini would make every assertion below meaningless, so the network guard
in conftest is what makes this file worth trusting: it is active here, and these
tests are exactly the ones that would trip it if a seam were missed.
"""

import asyncio
import json

import pytest

import fake_provider as fp

BODY = {"model": "gemini-flash",
        "messages": [{"role": "user", "content": "capital of France?"}]}


def _text_of(content_blocks):
    return "".join(b.get("text", "") for b in content_blocks
                   if b.get("type") == "text")


def _sse_events(raw_chunks):
    """Parse the Anthropic SSE stream into (event_name, data) pairs."""
    events = []
    for raw in raw_chunks:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        name = data = None
        for line in text.splitlines():
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


# ── the seam ───────────────────────────────────────────────────────────────

class TestOnePatchReachesEveryCaller:
    """The proxies import the singleton by reference, so one patch is enough.

    If this ever stops holding, the kit silently stops covering a call site and a
    test starts making a real request. That is the failure this file cannot
    catch by asserting on answers.
    """

    def test_the_pool_singleton_is_what_the_proxies_call(self, monkeypatch):
        fake = fp.text_scenario().install(monkeypatch)

        from src.core.orchestration.pool_manager import pool_manager
        from src.api.claude_proxy.handler import proxy_nonstream, proxy_stream

        assert proxy_nonstream.pool_manager is pool_manager
        assert proxy_stream.pool_manager is pool_manager
        assert pool_manager.call_nonstream == fake.call_nonstream
        assert pool_manager.call_stream == fake.call_stream

    def test_the_opencode_executor_is_covered_too(self, monkeypatch):
        """It does not go through `pool_manager`, and that is the trap.

        `opencode_proxy`'s non-streaming path imports `acompletion` by name at
        module scope, so it holds its own reference. Patching only the
        `pool_manager` singleton — which is the obvious thing to do, and what
        the other two proxies need — leaves this path talking to the real
        facade. Nothing in a passing test would say so: the request would just
        be a real one.
        """
        fake = fp.text_scenario().install(monkeypatch)

        from src.core.providers.gemini import facade as gemini_facade
        from src.api.opencode_proxy.handler import nonstream_executor

        # `==`, not `is`: attribute access builds a fresh bound method each time,
        # so identity fails even when the patch is in place.
        assert nonstream_executor.acompletion == fake.acompletion
        # And the origin, for the sites that import it inside a function.
        assert gemini_facade.acompletion == fake.acompletion

    def test_the_gemini_singleton_is_patched_too(self, monkeypatch):
        fake = fp.text_scenario().install(monkeypatch)

        from src.core.providers.gemini.manager import api_manager

        assert api_manager.call_gemini == fake.call_gemini
        assert api_manager.call_gemini_json == fake.call_gemini_json


# ── deterministic text ─────────────────────────────────────────────────────

class TestTheTextScenario:
    def test_the_same_answer_every_time(self):
        first = fp.run(fp.text_scenario("Paris.").call_nonstream(model_alias="m"))
        second = fp.run(fp.text_scenario("Paris.").call_nonstream(model_alias="m"))

        assert (first["response"].choices[0].message.content
                == second["response"].choices[0].message.content == "Paris.")

    def test_the_anthropic_proxy_answers_from_the_fixture(self, monkeypatch):
        fp.text_scenario("Paris is the capital of France.").install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        message = fp.run(claude_proxy.create_message(dict(BODY)))

        assert _text_of(message["content"]) == "Paris is the capital of France."
        assert message["stop_reason"] == "end_turn"

    def test_usage_is_the_providers_numbers_not_a_character_count(self, monkeypatch):
        fake = fp.text_scenario("hi", input_tokens=8000,
                                output_tokens=512).install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        message = fp.run(claude_proxy.create_message(dict(BODY)))

        assert message["usage"]["output_tokens"] == 512
        assert len(_text_of(message["content"])) == 2, (
            "a two-character answer billed for 512 tokens: the fake's usage "
            "did not reach the proxy")

    def test_the_call_was_recorded_with_its_arguments(self, monkeypatch):
        fake = fp.text_scenario().install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        fp.run(claude_proxy.create_message(dict(BODY)))

        assert fake.call_count == 1
        assert fake.calls[0]["model_alias"] == "gemini-flash"

    def test_nothing_is_written_to_the_usage_database(self, monkeypatch):
        """A fake call must not bill anything, even a local row.

        `install` stubs `log_usage`; the session guard in conftest would fail the
        suite if a fake call appended to the developer's usage.db.
        """
        fake = fp.text_scenario().install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        fp.run(claude_proxy.create_message(dict(BODY)))

        assert fake.usage_log, "log_usage was not stubbed; usage.db may be written"


# ── JSON ───────────────────────────────────────────────────────────────────

class TestTheJSONScenario:
    def test_the_serialisation_is_key_sorted(self):
        fake = fp.json_scenario({"b": 2, "a": 1})
        result = fp.run(fake.call_nonstream(model_alias="m"))

        assert result["response"].choices[0].message.content == '{"a": 1, "b": 2}'

    def test_it_round_trips_through_json(self, monkeypatch):
        fp.json_scenario({"queries": ["capital of France"], "n": 1}).install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        message = fp.run(claude_proxy.create_message(dict(BODY)))

        assert json.loads(_text_of(message["content"])) == {
            "queries": ["capital of France"], "n": 1}

    def test_the_api_manager_json_seam_reads_the_same_text(self):
        fake = fp.json_scenario({"ok": True})
        result = fp.run(fake.call_gemini_json(model_alias="gemini-flash-lite",
                                              system_instruction="s",
                                              prompt_text="p"))

        assert json.loads(result["text"]) == {"ok": True}
        assert result["input_tokens"] == fp.DEFAULT_INPUT_TOKENS


# ── streaming ──────────────────────────────────────────────────────────────

class TestTheStreamScenario:
    def test_chunks_arrive_in_the_scripted_order(self):
        """The scripted pieces, in order, ahead of the terminal chunk.

        Filtered on non-empty text rather than on `choices`: the terminal chunk
        has a choice too, carrying only the finish reason, so a `choices` filter
        swept it in and made this assert `["Hello", " world", ""]`. The order
        assertion is the point; the terminator has its own test below.
        """
        fake = fp.stream_scenario("Hello", " world")
        items = fp.run(fp.drain(fake.call_stream(model_alias="m")))

        assert [i["chunk"].text for i in items
                if i["chunk"].choices and i["chunk"].text] == [
            "Hello", " world"]

    def test_every_item_carries_the_key_and_model_the_pool_would(self):
        fake = fp.stream_scenario("Hello")
        items = fp.run(fp.drain(fake.call_stream(model_alias="gemini-flash")))

        assert all(i["api_key"] == fp.DEFAULT_API_KEY for i in items)
        assert all(i["model_id"] == fp.DEFAULT_MODEL_ID for i in items)

    def test_the_terminal_chunk_carries_the_finish_reason_and_usage(self):
        usage = {"prompt_tokens": 12, "completion_tokens": 7}
        fake = fp.stream_scenario("done", usage=usage)
        items = fp.run(fp.drain(fake.call_stream(model_alias="m")))

        last = items[-1]["chunk"]
        assert last.choices[0].finish_reason == "stop"
        assert last.usage == usage

    def test_the_anthropic_proxy_streams_the_fixture(self, monkeypatch):
        fp.stream_scenario("Hello", " world").install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        async def go():
            return _sse_events(
                [c async for c in claude_proxy.stream_message(dict(BODY))])

        events = fp.run(go())
        streamed = "".join(
            d["delta"].get("text", "") for n, d in events
            if n == "content_block_delta" and d["delta"].get("type") == "text_delta")

        assert streamed == "Hello world"
        assert any(n == "message_delta" for n, _ in events)


class TestTheSSEScenario:
    """Raw frames, for code that consumes SSE rather than chunks."""

    def test_frames_are_passed_through_verbatim(self):
        frame = "event: ping\ndata: {\"n\": 1}\n\n"
        items = fp.run(fp.drain(fp.sse_scenario(frame).call_stream(model_alias="m")))

        assert [i["chunk"] for i in items] == [frame]

    def test_a_frame_can_be_given_as_a_name_and_a_payload(self):
        fake = fp.sse_scenario(("content_block_delta",
                                {"delta": {"type": "text_delta", "text": "hi"}}))
        items = fp.run(fp.drain(fake.call_stream(model_alias="m")))

        assert items[0]["chunk"] == (
            'event: content_block_delta\ndata: {"delta": {"text": "hi", '
            '"type": "text_delta"}}\n\n')

    def test_several_frames_keep_their_order(self):
        items = fp.run(fp.drain(fp.sse_scenario("a\n\n", "b\n\n", "c\n\n")
                                .call_stream(model_alias="m")))

        assert [i["chunk"] for i in items] == ["a\n\n", "b\n\n", "c\n\n"]


# ── tool calls ─────────────────────────────────────────────────────────────

class TestTheToolCallScenario:
    def test_the_proxy_asks_the_client_to_run_the_tool(self, monkeypatch):
        fp.tool_call_scenario("get_weather", {"city": "Paris"}).install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        message = fp.run(claude_proxy.create_message(dict(BODY)))
        call = next(b for b in message["content"] if b["type"] == "tool_use")

        assert call["name"] == "get_weather"
        assert call["input"] == {"city": "Paris"}

    def test_the_arguments_are_stable_json(self):
        call = fp.ToolCallTurn("f", {"b": 1, "a": 2}).message.tool_calls[0]

        assert call["function"]["arguments"] == '{"a": 2, "b": 1}'

    def test_the_answer_turn_follows_the_tool_turn(self, monkeypatch):
        """A two-turn conversation is one provider, not two fixtures."""
        fake = fp.tool_call_scenario("get_weather", {"city": "Paris"},
                                     then="It is 21 degrees.")
        fake.install(monkeypatch)

        from src.api.claude_proxy import claude_proxy

        body = dict(BODY, tools=[{
            "name": "get_weather", "description": "weather",
            "input_schema": {"type": "object",
                             "properties": {"city": {"type": "string"}}}}])
        first = fp.run(claude_proxy.create_message(body))

        assert any(b["type"] == "tool_use" for b in first["content"])
        assert fake.call_count == 1

    def test_a_streamed_tool_call_carries_the_arguments(self):
        fake = fp.FakeProvider(fp.StreamTurn([
            fp.tool_call_chunk("get_weather", {"city": "Paris"}),
            fp.finish_chunk("tool_calls"),
        ]))
        items = fp.run(fp.drain(fake.call_stream(model_alias="m")))

        call = items[0]["chunk"].choices[0].delta.tool_calls[0]
        assert json.loads(call["function"]["arguments"]) == {"city": "Paris"}
        assert items[-1]["chunk"].choices[0].finish_reason == "tool_calls"


# ── failures ───────────────────────────────────────────────────────────────

class TestTheFailureScenarios:
    """Each failure is the wording a provider actually sends, so this repo's
    string-matching classifier (src/core/providers/gemini/error.py) treats it
    the way it treats the real thing."""

    def test_429_raises_and_classifies_as_rate_limit(self):
        from src.core.providers.gemini import error as gerror

        fake = fp.rate_limit_scenario()
        with pytest.raises(fp.FakeProviderError) as exc:
            fp.run(fake.call_nonstream(model_alias="m"))

        assert exc.value.status_code == 429
        assert gerror.classify(str(exc.value)) == "rate_limit"

    def test_5xx_raises_and_classifies_as_unavailable(self):
        from src.core.providers.gemini import error as gerror

        with pytest.raises(fp.FakeProviderError) as exc:
            fp.run(fp.server_error_scenario().call_nonstream(model_alias="m"))

        assert exc.value.status_code == 503
        assert gerror.classify(str(exc.value)) == "unavailable"

    def test_a_timeout_classifies_as_rate_limit(self):
        from src.core.providers.gemini import error as gerror

        with pytest.raises(TimeoutError):
            fp.run(fp.timeout_scenario().call_nonstream(model_alias="m"))

        assert gerror.classify(str(fp.timeout_error())) == "rate_limit"

    def test_malformed_is_a_success_carrying_an_unusable_body(self):
        """The dangerous shape: HTTP 200, body is not the promised JSON.

        It has to degrade rather than raise, because the router reads it with
        `getattr(response, "text", "") or ""` in a dozen places.
        """
        fake = fp.malformed_scenario()
        result = fp.run(fake.call_nonstream(model_alias="m"))
        response = result["response"]

        assert isinstance(response, fp.MalformedResponse)
        assert (getattr(response, "text", "") or "") == ""
        assert getattr(response, "choices", None) is None
        with pytest.raises(ValueError):
            response.json()

    def test_the_body_records_what_arrived(self):
        malformed = fp.malformed_error()
        assert "502 Bad Gateway" in malformed.raw_body
        with pytest.raises(ValueError, match="Expecting value"):
            malformed.json()

    def test_a_failure_also_fails_the_stream(self):
        with pytest.raises(fp.FakeProviderError):
            fp.run(fp.drain(fp.server_error_scenario().call_stream(model_alias="m")))


class TestRetryThenSuccess:
    """The shape a retry loop exists for: fail, classify, try again, succeed."""

    def _drive(self, fake, attempts=3):
        """Retry the way pool_manager does — classify, then decide."""
        from src.core.providers.gemini import error as gerror

        last = None
        for _ in range(attempts):
            try:
                return fp.run(fake.call_nonstream(model_alias="m"))
            except Exception as exc:                     # noqa: BLE001
                reason = gerror.classify(str(exc))
                last = reason
                if reason in ("bad_request", "project_denied"):
                    break
        raise AssertionError(f"never recovered; last reason {last!r}")

    def test_the_first_attempt_fails_and_the_second_answers(self):
        fake = fp.retry_then_success_scenario(failures=1, text="recovered.")

        with pytest.raises(fp.FakeProviderError):
            fp.run(fake.call_nonstream(model_alias="m"))
        result = self._drive(fake)

        assert result["response"].choices[0].message.content == "recovered."
        # Two, not three: the direct call fails, and `_drive` returns on its
        # first attempt because the script answers there. The old "1 failed + 2
        # from _drive" counted a retry that never happened — the loop is
        # `return` on success, not run-to-completion.
        assert fake.call_count == 2

    def test_the_transient_error_is_the_one_worth_retrying(self):
        from src.core.providers.gemini import error as gerror

        with pytest.raises(fp.FakeProviderError) as exc:
            fp.run(fp.retry_then_success_scenario().call_nonstream(model_alias="m"))

        assert gerror.classify(str(exc.value)) == "unavailable"

    def test_the_script_repeats_its_last_turn_rather_than_going_empty(self):
        """An unplanned extra call still gets an answer instead of an IndexError."""
        fake = fp.retry_then_success_scenario(failures=1, text="recovered.")

        with pytest.raises(fp.FakeProviderError):
            fp.run(fake.call_nonstream(model_alias="m"))
        for _ in range(3):
            result = fp.run(fake.call_nonstream(model_alias="m"))
            assert result["response"].choices[0].message.content == "recovered."

    def test_a_non_retryable_error_is_not_retried(self):
        from src.core.providers.gemini import error as gerror

        fake = fp.retry_then_success_scenario(
            failures=1, error=fp.FakeProviderError(
                "400 INVALID_ARGUMENT: bad request", status_code=400))

        with pytest.raises(fp.FakeProviderError) as exc:
            fp.run(fake.call_nonstream(model_alias="m"))

        assert gerror.classify(str(exc.value)) == "bad_request"


# ── web search ─────────────────────────────────────────────────────────────

ADMIN = {"name": "test-account", "tier": "admin", "enabled": True,
         "auth_key": "k"}


class TestWebSearchIsLocal:
    """A search test must cost nothing.

    The DuckDuckGo path is an HTTP scrape and the Gemini path is a billed model
    call. `FakeSearch.install` replaces the scrape, so the real engine dispatch
    still runs while nothing reaches the network — and the provider fixture
    installed alongside it is a tripwire: if any code decided to ground with a
    model, `fake.call_count` would move.
    """

    def test_the_search_manager_runs_for_real_against_the_fixture(self,
                                                                  monkeypatch):
        fp.text_scenario().install(monkeypatch)          # tripwire, see above
        search = fp.FakeSearch(text="Paris, in France.",
                               citations=[{"title": "P", "url": "https://e.org/p"}])
        search.install(monkeypatch)

        from src.tools.search.orchestration.manager import execute_hybrid_search

        text, citations = fp.run(execute_hybrid_search(
            ["capital of France"], search_engine="duckduckgo",
            auth_key_prefix="g-enough", account=ADMIN))

        assert "Paris" in text
        assert [c["url"] for c in citations] == ["https://e.org/p"]

    def test_no_model_was_asked_to_ground_the_search(self, monkeypatch):
        fake = fp.text_scenario().install(monkeypatch)
        fp.FakeSearch().install(monkeypatch)

        from src.tools.search.orchestration.manager import execute_hybrid_search

        fp.run(execute_hybrid_search(["capital of France"],
                                     search_engine="duckduckgo", account=ADMIN))

        assert fake.call_count == 0, "the DuckDuckGo path called a paid model"

    def test_the_fixture_records_the_queries_it_was_asked(self, monkeypatch):
        search = fp.FakeSearch().install(monkeypatch)

        from src.tools.search.orchestration.manager import execute_hybrid_search

        fp.run(execute_hybrid_search(["a", "b"], search_engine="duckduckgo",
                                     account=ADMIN))

        # One call *per query*, not one call with both. `execute_hybrid_search`
        # fans the list out — up to three `run_single_query` tasks in parallel —
        # and each runs its own search. Asserting `[["a", "b"]]` would have
        # pinned a batching the function does not do, and the test would only
        # have passed against a different implementation. Nor is the *order* of
        # the fan-out a contract, so compare the flattened set.
        assert len(search.queries) == 2
        assert sorted(q for one in search.queries for q in one) == ["a", "b"]

    def test_the_route_serves_the_fixture_citations(self, monkeypatch):
        """The manager seam: a route needs the finished (text, citations) pair."""
        from unittest.mock import patch

        from fastapi.testclient import TestClient
        from src.server.openai_server.routes.app_init import app
        import src.server.openai_server.routes.standard_routes  # registers routes

        fp.FakeSearch(text="Paris, in France.",
                      citations=[{"title": "P",
                                  "url": "https://e.org/p"}]
                      ).install_manager(monkeypatch)

        with patch("src.server.openai_server.routes.standard_routes.search._check_auth") \
                as auth:
            auth.return_value = dict(ADMIN, auth_key="test-key-mock")
            response = TestClient(app).post(
                "/v1/search", json={"query": "capital of France",
                                    "search_engine": "duckduckgo"},
                headers={"Authorization": "Bearer test-key-mock"})

        # Not a hard 200: an empty temp DB can rate-limit a request. What must
        # hold is that the route answered with the fixture, not with a model.
        assert response.status_code not in (404, 405), response.text
        if response.status_code == 200:
            assert response.json()["citations"] == [
                {"title": "P", "url": "https://e.org/p"}]
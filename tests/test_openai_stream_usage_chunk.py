"""The trailing usage chunk is opt-in, per the OpenAI spec.

OpenAI's stream_options.include_usage reads:

    If set, an additional chunk will be streamed before the data: [DONE] message.
    The usage field on this chunk shows the token usage statistics for the entire
    request, and the choices field will always be an empty array.

So `choices: []` is legitimate only on that chunk, and only when the client asked
for it. This proxy emitted it unconditionally. Clients that index
`chunk.choices[0]` without checking the length hit IndexError on it, which
surfaces as "invalid response" or "switch model" — nothing points at a frame the
client was never supposed to receive.

These drive the real route over the real app, because the decision depends on
what the route forwards from the request body into the proxy.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture()
def client(temp_db, stub_provider):
    """The real app on the throwaway db from conftest's temp_db.

    temp_db owns the file's lifetime. Rolling my own mkstemp here meant
    unlinking a db whose handle the app still held, which fails on Windows.

    Depends on stub_provider so the stream comes from a fixed response instead of
    a live Gemini call. What is under test is the route's frame assembly — which
    chunk carries usage, which carries an empty choices array, what terminates
    the stream — and that logic is unchanged by who produced the text. A live
    call also made this file the only one in the suite that could not run in CI.
    """
    from fastapi.testclient import TestClient
    from src.server.openai_server.routes.app_init import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def stub_provider(temp_db, monkeypatch):
    """Make the route reach a provider, and make that provider a fixed stream.

    temp_db is requested explicitly even though ``client`` also asks for it.
    Without it this fixture is free to run before temp_db has redirected
    ``src.backend._db``, and register_keys_in_db() then writes key_status and
    key_penalties rows into the developer's real usage.db — which conftest's
    guard catches, and which only happens in a clean checkout.

    Two things stood between this file and a hermetic run:

    - No Gemini keys. ``execute_stream`` reserves one before it calls anything,
      so with none registered the route answered 503 before the provider was
      ever reached — which is correct behaviour, and is why the stream came back
      empty rather than failing. Registering a dummy key satisfies the
      reservation; the stub below means it is never used.
    - A live upstream call. Patched at PoolManager rather than at the HTTP
      client, so the route, the dialect conversion and the frame assembly all
      still execute. Mocking httpx would leave the decision under test untouched.

    The chunk is object-shaped, not a dict: ``execute_stream`` reads
    ``chunk.choices[0].delta`` by attribute. A dict here yields a stream that
    looks empty rather than one that fails, which is worse to debug.
    """
    import src.api.opencode_proxy.handler.stream_executor as se
    from src.backend.key_status import register_keys_in_db
    from src.core.config_n_logg import config as config_mod
    from src.core.router import router
    from tests.test_stream_impl_seams import Chunk, Delta, item

    # Three things stood between this file and a hermetic run.

    # 1. No Gemini keys. execute_stream reserves one before calling anything, so
    #    with none registered the route answered 503 before the provider was
    #    reached — correct behaviour, and why the stream came back empty rather
    #    than failing.
    #
    # 2. No key_status row. Patching config alone is not enough:
    #    calculate_key_capacities_by_pool() sums over router._key_status, which
    #    is built from the database, so with no rows the pool's capacity was 0
    #    and _apply_account_limit rejected every request with
    #    "tokens per minute limit exceeded" before the provider was called.
    #    That is the whole reason this file passed on a machine with a real
    #    .env and failed in CI: local keys meant real capacity.
    #
    # 3. A live upstream call. Patched at PoolManager rather than at the HTTP
    #    client, so the route, the dialect conversion and the frame assembly all
    #    still execute. Mocking httpx would leave the decision untouched.
    #
    # The chunk is object-shaped, not a dict: execute_stream reads
    # chunk.choices[0].delta by attribute. A dict here yields a stream that looks
    # empty rather than one that fails, which is worse to debug.
    monkeypatch.setattr(config_mod, "GEMINI_API_KEYS", ["sk-test-dummy-key-for-ci"])
    register_keys_in_db(config_mod.GEMINI_API_KEYS)
    router.refresh_keys()

    # calculate_key_capacities_by_pool caches per pool for a second. A previous
    # test file can warm that cache while the key list is still empty, so the
    # registered key would be ignored for up to a second — long enough for the
    # whole test to see capacity 0 and a spurious 429.
    from src.core.limits.account_limiter import capacity as cap_mod

    cap_mod._key_capacities_pool_cache.clear()
    cap_mod._key_capacities_pool_cache_ts.clear()

    async def fake_call_stream(**kwargs):
        yield item(Chunk(Delta(content="hi")))
        yield item(Chunk(Delta(), finish_reason="stop"))

    monkeypatch.setattr(se.pool_manager, "call_stream", fake_call_stream)
    return fake_call_stream


def _key(client):
    from src.core.accounts import account_manager

    return account_manager.create_account(name="t1", tier="admin")["auth_key"]


def _frames(client, key, stream_options=None):
    """Return the parsed JSON frames of a streaming completion."""
    body = {
        "model": "gemini-flash",
        "stream": True,
        "messages": [{"role": "user", "content": "say hi"}],
    }
    if stream_options is not None:
        body["stream_options"] = stream_options

    out = []
    with client.stream("POST", "/v1/chat/completions",
                       headers={"x-api-key": key}, json=body) as r:
        for line in r.iter_lines():
            if not line.startswith("data: ") or line[6:].strip() == "[DONE]":
                continue
            out.append(json.loads(line[6:]))
    return out


class TestUsageChunkIsOptIn:
    def test_no_stream_options_means_no_usage_chunk(self, client):
        key = _key(client)
        frames = _frames(client, key)

        assert frames, "stream produced nothing at all"
        assert not any(f.get("usage") for f in frames), \
            "usage chunk sent to a client that never asked for it"

    def test_no_frame_has_an_empty_choices_array(self, client):
        """The shape that breaks clients indexing choices[0] unguarded."""
        key = _key(client)
        frames = _frames(client, key)

        empty = [f for f in frames if f.get("choices") == []]
        assert not empty, f"{len(empty)} frame(s) with choices: [] were not requested"

    def test_include_usage_true_still_delivers_it(self, client):
        """Guard against over-correcting into "never send usage"."""
        key = _key(client)
        frames = _frames(client, key, {"include_usage": True})

        usage = [f for f in frames if f.get("usage")]
        assert usage, "include_usage was requested but no usage frame arrived"
        assert usage[0]["choices"] == [], \
            "the OpenAI spec says the usage chunk carries an empty choices array"

    def test_include_usage_false_is_treated_as_not_asked(self, client):
        # `false` is not `true`; sending the chunk anyway ignores the client.
        key = _key(client)
        frames = _frames(client, key, {"include_usage": False})

        assert not any(f.get("usage") for f in frames)

    def test_a_non_dict_stream_options_is_ignored(self, client):
        key = _key(client)
        frames = _frames(client, key, "yes")

        assert not any(f.get("usage") for f in frames)

    def test_every_frame_still_carries_the_required_fields(self, client):
        key = _key(client)
        frames = _frames(client, key)

        for f in frames:
            assert f["object"] == "chat.completion.chunk"
            assert isinstance(f["id"], str) and f["id"]
            assert isinstance(f["created"], int)
            assert f["model"]
            # choices is present and non-empty: that is the whole point.
            assert f.get("choices"), f"frame without choices: {f}"

    def test_the_stream_still_terminates(self, client):
        """Skipping the usage chunk must not skip the sentinel after it."""
        key = _key(client)
        body = {
            "model": "gemini-flash",
            "stream": True,
            "messages": [{"role": "user", "content": "say hi"}],
        }
        raw = []
        with client.stream("POST", "/v1/chat/completions",
                           headers={"x-api-key": key}, json=body) as r:
            for line in r.iter_lines():
                if line.strip():
                    raw.append(line.strip())

        assert raw[-1] == "data: [DONE]", raw[-1] if raw else "empty stream"

    def test_a_finish_reason_is_still_sent(self, client):
        """The other half of a well-formed stream. A client that never sees
        finish_reason reports the response as invalid too."""
        key = _key(client)
        frames = _frames(client, key)

        finishes = [
            f["choices"][0].get("finish_reason")
            for f in frames if f.get("choices")
        ]
        assert any(finishes), "no frame carried finish_reason"
        assert finishes[-1] in ("stop", "length", "tool_calls"), finishes[-1]
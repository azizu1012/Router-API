"""response_format was read nowhere in the router.

A client asking for structured JSON got prose back and nothing in any log said
why -- the field was simply not on any path. It is now carried from the edge and
translated per provider, which matters because the three dialects disagree:

  OpenAI    {"type": "json_object"} | {"type":"json_schema","json_schema":{...}}
  Gemini    response_mime_type + response_schema (two separate fields)
  Anthropic no equivalent at all

The Anthropic case is the one with teeth: an undeclared field is a 400, not
something ignored, so it has to be dropped rather than passed through.
"""
import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.opencode_proxy.handler.proxy import _client_sampling_params
from src.core.providers.custom_endpoint_client import call_custom_nonstream
from src.core.providers.gemini_facade import _gemini_response_format

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"]}
JSON_SCHEMA = {"type": "json_schema",
               "json_schema": {"name": "Out", "strict": True, "schema": SCHEMA}}


class TestTheEdgeCarriesIt:
    def test_an_openai_client_passes_it_through(self):
        got = _client_sampling_params({"response_format": JSON_SCHEMA})
        assert got["response_format"] == JSON_SCHEMA

    def test_json_object_also_travels(self):
        got = _client_sampling_params({"response_format": {"type": "json_object"}})
        assert got["response_format"]["type"] == "json_object"

    @pytest.mark.parametrize("value", [None, {}, "text", 42, {"name": "x"}])
    def test_nothing_to_carry_leaves_the_field_absent(self, value):
        """A half-formed value must not reach a provider that would reject it."""
        assert "response_format" not in _client_sampling_params(
            {"response_format": value})


class TestGeminiGetsMimeTypeAndSchema:
    def test_json_object_is_just_a_mime_type(self):
        assert _gemini_response_format({"type": "json_object"}) == {
            "response_mime_type": "application/json"}

    def test_a_schema_becomes_both_fields(self):
        out = _gemini_response_format(JSON_SCHEMA)
        assert out["response_mime_type"] == "application/json"
        assert out["response_schema"] == SCHEMA

    def test_an_inlined_schema_is_accepted_too(self):
        """Some clients flatten json_schema's schema up a level."""
        out = _gemini_response_format({"type": "json_schema", "json_schema": SCHEMA})
        assert out["response_schema"] == SCHEMA

    def test_the_schema_is_sanitised_like_a_tool_schema(self):
        """Gemini's responseSchema is the restricted Schema subset, so an
        items:{} or anyOf in a client schema is the same 400 as in a tool."""
        out = _gemini_response_format({"type": "json_schema", "json_schema": {
            "schema": {"type": "object", "properties": {
                "tags": {"type": "array", "items": {}},
                "n": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}}}})
        props = out["response_schema"]["properties"]
        assert props["tags"]["items"] == {"type": "string"}
        assert props["n"]["type"] == "integer"

    def test_the_sdk_accepts_what_we_build(self):
        from google.genai import types

        cfg = types.GenerateContentConfig(**_gemini_response_format(JSON_SCHEMA))
        assert cfg.response_mime_type == "application/json"
        assert cfg.response_schema is not None

    @pytest.mark.parametrize("value", [None, "json", {}, {"type": "text"}, 7])
    def test_anything_else_produces_nothing(self, value):
        assert _gemini_response_format(value) is None


OPENAI_REPLY = {"id": "c1", "object": "chat.completion", "created": 1, "model": "m",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "{}"},
                             "finish_reason": "stop"}]}
ANTHROPIC_REPLY = {"id": "m1", "model": "m", "stop_reason": "end_turn",
                   "content": [{"type": "text", "text": "{}"}]}


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.status = 200
        self.headers = {"Content-Type": "application/json"}
        self.content = self

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)

    def __aiter__(self):
        async def gen():
            if False:
                yield b""
        return gen()

    def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Post:
    def __init__(self, r):
        self._r = r

    def __await__(self):
        async def go():
            return self._r
        return go().__await__()

    async def __aenter__(self):
        return self._r

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, r, seen):
        self._r, self._seen = r, seen

    def post(self, url, json=None, timeout=None):
        self._seen["url"], self._seen["body"] = url, json
        return _Post(self._r)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def close(self):
        pass


def _patch(payload):
    seen = {}
    return patch("aiohttp.ClientSession",
                 lambda headers=None: _Session(_Resp(payload), seen)), seen


TOOLS = [{"type": "function", "function": {"name": "f",
                                           "parameters": {"type": "object", "properties": {}}}}]


@pytest.mark.anyio
class TestTheWire:
    async def test_an_openai_endpoint_gets_it_verbatim(self):
        patcher, seen = _patch(OPENAI_REPLY)
        with patcher:
            await call_custom_nonstream(
                "https://ep.example/v1", "k", "m",
                [{"role": "user", "content": "x"}], tools=TOOLS,
                api_format="openai", response_format=JSON_SCHEMA)
        assert seen["body"]["response_format"] == JSON_SCHEMA

    async def test_an_anthropic_endpoint_never_sees_the_field(self):
        """Anthropic declares no response_format, and an undeclared field fails
        the request rather than being ignored."""
        patcher, seen = _patch(ANTHROPIC_REPLY)
        with patcher:
            await call_custom_nonstream(
                "https://ep.example", "k", "m",
                [{"role": "user", "content": "x"}], tools=TOOLS,
                api_format="anthropic",
                response_format=JSON_SCHEMA,
                extra_body={"response_format": JSON_SCHEMA, "foo": 1})
        assert "response_format" not in seen["body"]
        assert seen["body"].get("foo") == 1

    async def test_no_format_means_no_field_on_either_endpoint(self):
        for fmt, base in (("openai", "https://ep.example/v1"),
                          ("anthropic", "https://ep.example")):
            patcher, seen = _patch(
                OPENAI_REPLY if fmt == "openai" else ANTHROPIC_REPLY)
            with patcher:
                await call_custom_nonstream(
                    base, "k", "m", [{"role": "user", "content": "x"}],
                    tools=TOOLS, api_format=fmt)
            assert "response_format" not in seen["body"]
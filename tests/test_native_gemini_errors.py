"""The Gemini-native pass-through has to speak Google's error envelope.

`google-genai` reads `error.code`, `error.message` and `error.status` out of the
body. Three separate things meant it never saw them:

1. Anything raised in the native handler reached Starlette unhandled and came
   back `500 Internal Server Error` with a `text/plain` body — unparseable by
   any native client, so the caller saw a decode failure instead of the reason.
2. The streaming path emitted the *chat-completions* shape,
   `{"error": {"message", "type"}}`. Worse than nothing: it looked like an error
   and carried none of the fields the SDK reads.
3. The immediate cause of the 500 was a missing optional dependency.
   `google-adk` is only needed for the ADK web-search path, and the account-level
   search default switched every plain `:generateContent` request onto it. The
   import sat inside the retry loop, so `except Exception` counted it as a model
   failure and retried every key and every pool member before reporting
   `quota_exhausted` — pointing at quota when the cause was a package that was
   never installed.

The response chunks also shipped the SDK's own `sdk_http_response`, a live httpx
response whose header block went out in every chunk.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestErrorEnvelope:
    def test_the_envelope_carries_the_three_fields_the_sdk_reads(self):
        from src.server.pass_through_server.routes.gemini_error import (
            gemini_error_body,
        )

        body = gemini_error_body(429, "slow down")

        assert body == {"error": {"code": 429, "message": "slow down",
                                  "status": "RESOURCE_EXHAUSTED"}}

    @pytest.mark.parametrize("code,status", [
        (400, "INVALID_ARGUMENT"), (401, "UNAUTHENTICATED"),
        (403, "PERMISSION_DENIED"), (404, "NOT_FOUND"),
        (429, "RESOURCE_EXHAUSTED"), (500, "INTERNAL"),
        (503, "UNAVAILABLE"), (504, "DEADLINE_EXCEEDED"),
    ])
    def test_codes_map_to_the_matching_grpc_status(self, code, status):
        from src.server.pass_through_server.routes.gemini_error import (
            gemini_error_body,
        )

        assert gemini_error_body(code, "x")["error"]["status"] == status

    def test_the_sdk_parses_our_envelope(self):
        pytest.importorskip("google.genai.errors")
        from google.genai.errors import APIError
        from src.server.pass_through_server.routes.gemini_error import (
            gemini_error_body,
        )

        exc = APIError(429, gemini_error_body(429, "slow down"))

        assert exc.code == 429
        assert exc.status == "RESOURCE_EXHAUSTED"
        assert exc.message == "slow down"

    def test_the_stream_error_frame_is_the_same_envelope(self):
        from src.server.pass_through_server.routes.gemini_error import (
            gemini_error_chunk,
        )

        frame = gemini_error_chunk(500, "boom").decode("utf-8")

        assert frame.startswith("data: ")
        payload = json.loads(frame[len("data: "):])
        assert payload["error"]["status"] == "INTERNAL"
        assert payload["error"]["message"] == "boom"


class TestFailureClassification:
    """A missing dependency is not a quota problem, and must not read as one."""

    def test_a_missing_adk_is_a_server_error_naming_the_package(self):
        from src.server.pass_through_server.routes.gemini_handlers import (
            _classify_native_failure,
        )

        status, message = _classify_native_failure(
            RuntimeError("google-adk is required for the ADK web-search path"))

        assert status == 500
        assert "google-adk" in message

    def test_key_exhaustion_becomes_429(self):
        from src.server.pass_through_server.routes.gemini_handlers import (
            _classify_native_failure,
        )

        assert _classify_native_failure(RuntimeError("quota_exhausted"))[0] == 429

    def test_an_http_exception_keeps_its_status(self):
        from fastapi import HTTPException
        from src.server.pass_through_server.routes.gemini_handlers import (
            _classify_native_failure,
        )

        status, message = _classify_native_failure(
            HTTPException(status_code=429, detail={"error": {"message": "limit"}}))

        assert status == 429
        assert message == "limit"


class TestAdkIsNotSwallowed:
    """`ADKUnavailableError` exists to surface before the retry loop.

    Its own docstring says so. The call sat inside the loop, so the loop's
    `except Exception` turned it into a per-model failure and the call ended as
    `quota_exhausted` — after burning every key and every pool member.
    """

    def test_a_missing_adk_raises_before_any_key_is_tried(self):
        import inspect

        from src.core.providers.gemini import manager as m

        src = inspect.getsource(m.GeminiAPIManager.call_gemini)
        load_line = src.index("_load_adk_runner()")
        loop_line = src.index("for attempt in range")
        assert load_line < loop_line, (
            "the ADK import is back inside the retry loop")

    def test_the_stream_path_is_the_same(self):
        import inspect

        from src.core.providers.gemini import manager as m

        src = inspect.getsource(m.GeminiAPIManager.call_gemini_stream)
        assert src.index("_load_adk_runner()") < src.index("for attempt in range")


class TestSdkFieldsAreNotShipped:
    """`GenerateContentResponse.sdk_http_response` is the SDK's own live httpx
    response. Dumping with by_alias=True put its header block into every chunk
    the client received."""

    def test_the_sdk_local_fields_are_named_and_excluded(self):
        from src.core.providers.gemini.response_dump import (
            SDK_LOCAL_RESPONSE_FIELDS,
        )

        assert "sdk_http_response" in SDK_LOCAL_RESPONSE_FIELDS
        assert "parsed" in SDK_LOCAL_RESPONSE_FIELDS

    def test_a_dumped_response_carries_no_sdk_internals(self):
        from google.genai import types
        from src.core.providers.gemini.response_dump import (
            dump_generation_response,
        )

        resp = types.GenerateContentResponse(
            candidates=[types.Candidate(
                content=types.Content(role="model",
                                      parts=[types.Part(text="pong")]))],
            model_version="gemini-3.6-flash",
        )

        out = dump_generation_response(resp)

        assert "sdkHttpResponse" not in out
        assert out["candidates"][0]["content"]["parts"][0]["text"] == "pong"

    def test_a_bytes_thought_signature_stays_base64_not_a_python_repr(self):
        """The trap: a plain model_dump hands back raw bytes, so json.dumps
        either refuses it or, with default=str, writes `"b'\\x12...'"` into a
        field the client treats as a signature."""
        from google.genai import types
        from src.core.providers.gemini.response_dump import (
            dump_generation_response,
        )

        resp = types.GenerateContentResponse(
            candidates=[types.Candidate(
                content=types.Content(role="model", parts=[types.Part(
                    text="pong", thought_signature=b"\x12sig")]))],
        )

        sig = dump_generation_response(resp)["candidates"][0]["content"][
            "parts"][0]["thoughtSignature"]

        assert not sig.startswith("b'"), sig
        import base64
        assert base64.b64decode(sig) == b"\x12sig", sig

    def test_the_dumped_body_validates_as_a_generation_response(self):
        from google.genai import types
        from src.core.providers.gemini.response_dump import (
            dump_generation_response,
        )

        resp = types.GenerateContentResponse(
            candidates=[types.Candidate(
                content=types.Content(role="model",
                                      parts=[types.Part(text="pong")]))],
            model_version="gemini-3.6-flash",
        )

        types.GenerateContentResponse.model_validate(dump_generation_response(resp))
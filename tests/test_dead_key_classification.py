"""Google reports a dead key with HTTP 400, not 401.

The Gemini classifier checks the HTTP code before it looks at the text, so the
400 rule owned this error and returned bad_request. That distinction is not
cosmetic: bad_request retries the same key, invalid_key freezes it for an hour.
A live request walked one rejected key twelve times in sixteen seconds and the
user saw a spinner with nothing behind it.

These tests pin the wording Google actually uses, and pin that the real 400
cases still land on bad_request -- widening the pattern is only safe while the
narrow cases stay put.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.providers.gemini.error import classify, is_invalid_key_simple

DEAD_KEY_BODY = (
    '{\n  "error": {\n    "code": 400,\n'
    '    "message": "API key not valid. Please pass a valid API key.",\n'
    '    "status": "INVALID_ARGUMENT",\n    "details": [\n      {\n'
    '        "@type": "type.googleapis.com/google.rpc.ErrorInfo",\n'
    '        "reason": "API_KEY_INVALID",\n'
    '        "domain": "googleapis.com"\n      }\n    ]\n  }\n}'
)


class TestADeadKeyIsFrozenRatherThanRetried:
    def test_the_verbatim_google_body_classifies_as_invalid_key(self):
        assert classify(Exception(DEAD_KEY_BODY)) == "invalid_key"

    @pytest.mark.parametrize("message", [
        "API key not valid. Please pass a valid API key.",
        "API_KEY_INVALID",
        "API key invalid",
        "Invalid API key provided",
        "API key not found",
    ])
    def test_each_wording_is_recognised(self, message):
        assert is_invalid_key_simple(message) is True


class TestTheRealFourHundredsStillClassifyAsBadRequest:
    """Guards against over-correcting into freezing healthy keys."""

    @pytest.mark.parametrize("body,expected", [
        (DEAD_KEY_BODY, "invalid_key"),
        ('{"code": 400, "status": "INVALID_ARGUMENT",'
         ' "message": "Function declaration schema is invalid"}', "bad_request"),
        ("INVALID_ARGUMENT [400]", "bad_request"),
        ("INVALID_ARGUMENT: Parameter is missing", "bad_request"),
        ('{"code": 400, "message": "Invalid JSON payload received."}',
         "bad_request"),
    ])
    def test_classification(self, body, expected):
        assert classify(Exception(body)) == expected

    def test_grpc_permission_denied_is_not_mistaken_for_a_bad_key(self):
        assert classify(Exception("PERMISSION_DENIED [403]")) == "permission_denied"

    def test_quota_is_still_rate_limit(self):
        assert classify(Exception(
            "Resource has been exhausted (e.g. check quota). [429]")) == "rate_limit"
"""Serialise a GenAI response for a client without its SDK bookkeeping.

`GenerateContentResponse` carries fields the GenAI SDK keeps for itself that
Google never puts on the wire. Dumping with `by_alias=True` shipped all of
them to clients:

  * `sdk_http_response` holds the live httpx response — its header block ended
    up inside every streamed chunk, and on one chunk its contents were `bytes`,
    so `json.dumps` raised "Object of type bytes is not JSON serializable" from
    inside the serializer. The message named neither the model, nor the field,
    nor the chunk, so the chunk that broke was a surprise.
  * `parsed` and `automatic_function_calling_history` are SDK bookkeeping of the
    same kind.

Official response body is candidates, promptFeedback, usageMetadata,
modelVersion, responseId and createTime — nothing else.

One trap worth naming: `Part.thought_signature` is `bytes` in the SDK, and the
SDK's own pydantic config encodes it as base64 on the way to JSON. A plain
`model_dump()` hands back raw bytes, so `json.dumps` either refuses it or, with
`default=str`, quietly writes the Python repr — `"b'\\x12i...'"` — into a field
the client then treats as a signature. This module therefore serialises
through `model_dump_json`, which is the only path that encodes it correctly.

This lives beside the SDK calls because that is where the knowledge of which
fields are SDK-local belongs; the HTTP layer only formats the result.
"""

import json
from typing import Any

SDK_LOCAL_RESPONSE_FIELDS = frozenset({
    "sdk_http_response",
    "parsed",
    "automatic_function_calling_history",
})


def dump_generation_response(response: Any) -> dict:
    """The chunk or body a client should see."""
    excluded = set(SDK_LOCAL_RESPONSE_FIELDS)
    # model_dump_json is the SDK's own serialiser: it knows a bytes
    # thought_signature must go out base64-encoded, which neither a plain
    # model_dump nor json.dumps does.
    return json.loads(response.model_dump_json(
        by_alias=True, exclude_none=True, exclude=excluded,
    ))
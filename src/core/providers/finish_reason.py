"""One place that decides what `finish_reason` a client is told.

The OpenAI enum is closed — `stop`, `length`, `tool_calls`, `content_filter`,
`function_call` — and it is a `Literal` in the SDK, so anything else in the field
makes the response fail to parse for a strict client.

Upstream does not speak that vocabulary. Gemini's FinishReason includes
`SAFETY`, `RECITATION`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `SPII`,
`IMAGE_SAFETY`, `MALFORMED_FUNCTION_CALL`, `UNEXPECTED_TOOL_CALL` and more, and
the router was relaying them verbatim. A client asking a tool-enabled question
got back `finish_reason: "malformed_function_call"` with `tool_calls: null` — a
value outside the enum, and the one string that most invites a client to report
"invalid response".

The streaming path had its own, worse problem: it reduced every reason to
`length` or `stop`, so a turn that did call a function finished as `stop` and a
streaming client never learned there was anything to execute.

Both call this instead.
"""

from typing import Any, Optional

STOP = "stop"
LENGTH = "length"
TOOL_CALLS = "tool_calls"
CONTENT_FILTER = "content_filter"

# Gemini reasons that mean the answer was withheld rather than completed.
_FILTER_REASONS = frozenset({
    "safety",
    "recitation",
    "blocklist",
    "prohibited_content",
    "spii",
    "image_safety",
    "image_prohibited_content",
    "no_image",
    "image_other",
})


def normalize_finish_reason(
    reason: Optional[Any],
    has_tool_calls: bool = False,
) -> str:
    """Map an upstream finish reason onto the OpenAI enum.

    `has_tool_calls` wins over the reported reason: if a function call came back
    in the payload, the useful thing to tell the client is that there is
    something to run, whatever upstream called the turn.
    """
    if has_tool_calls:
        return TOOL_CALLS

    text = str(reason or "").strip()
    low = text.lower()

    if low in _FILTER_REASONS:
        return CONTENT_FILTER
    if not low or low == "stop":
        return STOP
    # MAX_TOKENS, and the wording some endpoints use for the same thing.
    if "max" in low or "length" in low or "token" in low:
        return LENGTH
    # MALFORMED_FUNCTION_CALL and UNEXPECTED_TOOL_CALL arrive with no tool call
    # in the payload, so there is nothing to execute and nothing was withheld:
    # from the client's point of view the turn ended.
    if low in ("malformed_function_call", "unexpected_tool_call"):
        return STOP
    # Anything else is a value the enum cannot carry. Passing it through is what
    # produced the invalid responses in the first place.
    return STOP
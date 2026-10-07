"""Google's error envelope, shared by the native streaming and non-stream paths.

A native Gemini client — `google-genai` included — reads `error.code`,
`error.message` and `error.status` out of the body. The chat-completions shape
(`{message, type}`) gives it nothing it recognises, so the caller sees a decode
failure instead of the reason the request failed. Getting this wrong is silent:
the request failed either way, but the reason becomes unrecoverable.

This lives in its own module because both the streaming and the non-stream
handler need it and they must not drift apart.
"""

from typing import Any

STATUS_BY_CODE = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    429: "RESOURCE_EXHAUSTED",
    499: "CANCELLED",
    500: "INTERNAL",
    503: "UNAVAILABLE",
    504: "DEADLINE_EXCEEDED",
}


def detail_message(detail: Any) -> str:
    """Pull the human-readable text out of an HTTPException detail."""
    if isinstance(detail, dict):
        err = detail.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or detail)
        if "message" in detail:
            return str(detail["message"])
    return str(detail)


def gemini_error_body(code: int, message: str) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "status": STATUS_BY_CODE.get(code, "UNKNOWN"),
        }
    }


def gemini_error_chunk(code: int, message: str) -> bytes:
    """One SSE frame carrying the error envelope.

    The HTTP status is already committed as 200 by the time a stream fails, so
    the body is the only place left to tell the client what went wrong.
    """
    import json

    return f"data: {json.dumps(gemini_error_body(code, message), ensure_ascii=False)}\n\n".encode("utf-8")
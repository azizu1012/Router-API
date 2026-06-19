"""Gemini API error classification — pure functions.

Each function returns a structured verdict; the retry loop
orchestrator applies penalties / freeze decisions.
"""

import re
from typing import Optional, Dict, Any


def parse_project(text: str) -> Optional[str]:
    """Extract GCP project number from error message."""
    m = re.search(r"project_number[=_:]\s*(\d+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"projects[=/:]\s*(\d+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def get_error_info(exc: Exception) -> Dict[str, Any]:
    """Extract structured info from any Exception (e.g., google-genai APIError or GeminiAPIError)."""
    info = {
        "code": None,
        "status": None,
        "message": str(exc),
    }

    # 1. Check if it's a google-genai APIError
    try:
        from google.genai import errors as gerrors
        if isinstance(exc, gerrors.APIError):
            info["code"] = getattr(exc, "code", None)
            info["status"] = getattr(exc, "status", None)
            info["message"] = getattr(exc, "message", str(exc))
            return info
    except ImportError:
        pass

    # 2. Check if it's a native exception (which might have status_code)
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        try:
            info["code"] = int(status_code)
        except ValueError:
            pass

    # 3. Fallback: Parse code and status from string representation
    text = str(exc)
    match_code = re.search(r"\b(400|401|403|404|429|499|500|503|504)\b", text)
    if match_code:
        info["code"] = int(match_code.group(1))

    for status_str in ["RESOURCE_EXHAUSTED", "PERMISSION_DENIED", "INVALID_ARGUMENT", "UNAVAILABLE", "INTERNAL"]:
        if status_str in text:
            info["status"] = status_str
            break

    return info


def classify(exc: Any) -> str:
    """Classify an exception (Exception object or text string) into a machine-readable reason code.

    Returns one of:
      ``bad_request``, ``project_denied``, ``permission_denied``,
      ``unavailable``, ``project_quota_429``, ``rate_limit``,
      ``grounding_fallback``, ``unknown``.
    """
    if isinstance(exc, Exception):
        info = get_error_info(exc)
        code = info["code"]
        status = info["status"]
        message = info["message"]
    else:
        text = str(exc)
        # Parse from string
        info = get_error_info(Exception(text))
        code = info["code"]
        status = info["status"]
        message = info["message"]

    lowered = message.lower()

    # 1. Check HTTP Status Code first (extremely precise)
    if code == 400:
        if "failed_precondition" not in lowered and status != "FAILED_PRECONDITION":
            return "bad_request"
    elif code == 401:
        return "invalid_key"
    elif code == 403:
        if "denied access" in lowered or status == "PERMISSION_DENIED" and "denied access" in lowered:
            return "project_denied"
        return "permission_denied"
    elif code == 404:
        return "unavailable"
    elif code == 429:
        if "rate_limit_exceeded" in lowered or ("quota exceeded" in lowered and ("day" in lowered or "daily" in lowered)) or status == "RESOURCE_EXHAUSTED" and ("day" in lowered or "daily" in lowered):
            return "project_quota_429"
        return "rate_limit"
    elif code in (500, 503, 504):
        return "unavailable"

    # 2. Check Status String if code is None
    if status == "RESOURCE_EXHAUSTED":
        if "day" in lowered or "daily" in lowered:
            return "project_quota_429"
        return "rate_limit"
    elif status == "PERMISSION_DENIED":
        if "denied access" in lowered:
            return "project_denied"
        return "permission_denied"
    elif status == "INVALID_ARGUMENT":
        return "bad_request"
    elif status == "UNAVAILABLE":
        return "unavailable"

    # 3. Fallback string-matching for non-structured errors
    if "400" in message and "failed_precondition" not in lowered:
        if "invalid_argument" in lowered or "bad_request" in lowered:
            return "bad_request"

    if "403" in message and "permission_denied" in lowered:
        if "denied access" in lowered:
            return "project_denied"
        return "permission_denied"

    if "404" in lowered or "not_found" in lowered or "not found" in lowered:
        return "unavailable"

    if "midstreamfallbackerror" in lowered or "serviceunavailableerror" in lowered:
        return "unavailable"

    if "503" in lowered or "unavailable" in lowered or "overloaded" in lowered:
        return "unavailable"

    if "deadline exceeded" in lowered or "timeout" in lowered:
        return "rate_limit"

    if "429" in lowered or "quota" in lowered or "resource exhausted" in lowered:
        if "rate_limit_exceeded" in lowered or ("quota exceeded" in lowered and ("day" in lowered or "daily" in lowered)):
            return "project_quota_429"
        return "rate_limit"

    if any(kw in lowered for kw in [
        "grounding", "google_search", "google-search",
        "search tool", "tool is not allowed", "tool not supported",
    ]) or ("403" in message and "permission" in lowered) or ("400" in message and "invalid" in lowered):
        return "grounding_fallback"

    if is_bad_request_simple(message):
        return "bad_request"

    if is_invalid_key_simple(message):
        return "invalid_key"

    return "unknown"


def is_bad_request_simple(text: str) -> bool:
    lowered = (text or "").lower()
    return "400" in text or "invalid_argument" in lowered or "parameter" in lowered


def is_invalid_key_simple(text: str) -> bool:
    lowered = (text or "").lower()
    return any(t in lowered for t in [
        "api key invalid", "api_key_invalid", "invalid api key",
        "401", "unauthorized", "api key not found",
    ])


def is_grounding_suppression(error_text: str) -> bool:
    """Check if the error is specifically a grounding-tool rejection."""
    return any(kw in error_text.lower() for kw in [
        "grounding", "google_search", "google-search",
        "search tool", "tool is not allowed", "tool not supported",
    ]) or ("403" in error_text and "permission" in error_text) or (
        "400" in error_text and "invalid" in error_text
    )


def needs_backoff(reason: str) -> bool:
    """True if a retry should include a sleep backoff."""
    return reason in ("rate_limit", "project_quota_429", "unavailable", "unknown")


def is_fatal(reason: str) -> bool:
    """True if this error should terminate the request immediately."""
    return reason in ("bad_request", "project_denied")

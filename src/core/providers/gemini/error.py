"""Gemini API error classification — pure functions.

Each function returns a structured verdict; the retry loop
orchestrator applies penalties / freeze decisions.
"""

import re
from typing import Optional


def parse_project(text: str) -> Optional[str]:
    """Extract GCP project number from error message."""
    m = re.search(r"project_number[=_:]\s*(\d+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"projects[=/:]\s*(\d+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def classify(text: str) -> str:
    """Classify an error text into a machine-readable reason code.

    Returns one of:
      ``bad_request``, ``project_denied``, ``permission_denied``,
      ``unavailable``, ``project_quota_429``, ``rate_limit``,
      ``grounding_fallback``, ``unknown``.
    """
    lowered = text.lower()

    if "400" in text and "failed_precondition" not in lowered:
        if "invalid_argument" in lowered or "bad_request" in lowered:
            return "bad_request"

    if "403" in text and "permission_denied" in lowered:
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
    ]) or ("403" in text and "permission" in lowered) or ("400" in text and "invalid" in lowered):
        return "grounding_fallback"

    if is_bad_request_simple(text):
        return "bad_request"

    if is_invalid_key_simple(text):
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

"""Anthropic wire format package.

The package keeps request construction and response/event conversion separate
while exposing the stable provider-facing symbols from one domain namespace.
"""

from .common import (
    API_FORMAT_ANTHROPIC,
    API_FORMAT_OPENAI,
    SUPPORTED_API_FORMATS,
    is_anthropic_format,
    normalize_api_format,
)
from .request import openai_to_anthropic_body
from .response import (
    anthropic_event_to_openai_delta,
    anthropic_to_openai_message,
    anthropic_to_openai_usage,
)

__all__ = [
    "API_FORMAT_ANTHROPIC",
    "API_FORMAT_OPENAI",
    "SUPPORTED_API_FORMATS",
    "is_anthropic_format",
    "normalize_api_format",
    "openai_to_anthropic_body",
    "anthropic_event_to_openai_delta",
    "anthropic_to_openai_message",
    "anthropic_to_openai_usage",
]


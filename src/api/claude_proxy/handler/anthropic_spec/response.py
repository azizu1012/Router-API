"""Anthropic response and stream protocol helpers."""

from typing import Any, Dict, List, Optional, Tuple

from src.core.config_n_logg.logger import logger_proxy as logger



_REFUSAL_FINISH_REASONS = {"content_filter", "safety", "recitation", "blocklist", "prohibited_content"}

def map_stop_reason(
    finish_reason: Optional[str],
    has_tool_calls: bool = False,
    stop_sequences: Optional[List[str]] = None,
    emitted_text: str = "",
    incomplete_tool_call: bool = False,
) -> Tuple[str, Optional[str]]:
    """Map a provider finish_reason to the Anthropic `stop_reason` enum.

    Anthropic's full enum is:
      end_turn | max_tokens | stop_sequence | tool_use | pause_turn | refusal | model_context_window_exceeded

    Returns (stop_reason, stop_sequence).
    """
    fr = str(finish_reason or "").strip().lower()

    if fr in _REFUSAL_FINISH_REASONS:
        return "refusal", None

    # Pending tool calls outrank every other reason: the client only executes them
    # when we say `tool_use`, so reporting anything else would strand the calls and
    # deadlock the conversation.
    if has_tool_calls:
        return "tool_use", None

    # A tool call whose arguments never finished parsing was cut off mid-call.
    # Anthropic's guidance for that case is to report `max_tokens`, since the
    # prescribed remedy is "raise max_tokens and retry". A client told
    # `end_turn` has no signal that anything went wrong and just carries on as
    # if the model chose to stop. The provider frequently reports a plain
    # `stop` in this situation rather than `length`, so it cannot be inferred
    # from the finish reason.
    if incomplete_tool_call:
        return "max_tokens", None

    if "max" in fr or "length" in fr:
        return "max_tokens", None

    # Anthropic reports stop_sequence (not end_turn) when a custom stop fired.
    matched = _match_stop_sequence(stop_sequences, emitted_text)
    if matched is not None:
        return "stop_sequence", matched

    return "end_turn", None

def _match_stop_sequence(stop_sequences: Optional[List[str]], text: str) -> Optional[str]:
    if not stop_sequences or not text:
        return None
    for seq in stop_sequences:
        if seq and seq in text:
            return seq
    return None

def build_error_event(err: Exception, error_type: str = "api_error") -> Dict[str, Any]:
    """Build an Anthropic-shaped error body for the SSE `error` event."""
    return {
        "type": "error",
        "error": {
            "type": error_type,
            "message": str(err)[:500] or "Internal error",
        },
    }

def log_stream_failure(model_alias: str, err: Exception) -> None:
    logger.error("[Claude Spec] stream failure model=%s: %s", model_alias, err)


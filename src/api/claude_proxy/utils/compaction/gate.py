"""Compaction gate — decides whether to trigger dynamic compaction.

Checks system prompt for Claude Code markers and applies
client-specific token thresholds.
"""

from typing import Any, Dict, List

from src.core.config_n_logg import config


def should_compact(messages: List[Dict[str, Any]], input_tokens: int, retry_attempt: int = 0) -> bool:
    """Return True if ``input_tokens`` exceed the compaction threshold.

    Threshold depends on whether the client is Claude Code (lower threshold
    to stay within Vertex TPM limits) or a generic OpenAI-compatible client.

    On high retry attempts the threshold is progressively lowered.
    """
    is_claude_code = _detect_claude_code(messages)
    threshold = config.CLAUDE_CODE_COMPACTION_THRESHOLD if is_claude_code else config.COMPACTION_TOKEN_THRESHOLD

    if retry_attempt >= 10:
        divisor = max(3, retry_attempt - 7)
        threshold = max(5000, threshold // divisor)

    return input_tokens > threshold


def _detect_claude_code(messages: List[Dict[str, Any]]) -> bool:
    """Heuristic: check system prompt for Claude Code markers."""
    system_prompt = ""
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(str(c) for c in content)
            else:
                content = str(content or "")
            system_prompt += content

    lower = system_prompt.lower()
    return (
        "you are claude code" in lower
        or "cc_version=" in lower
        or "claude-code" in lower
    )

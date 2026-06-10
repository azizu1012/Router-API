"""Thin backward-compatible facade — delegates to ``utils.compaction`` package."""
from src.api.claude_proxy.utils.compaction import (
    should_compact,
    emergency_truncate_to_limit as _emergency_truncate_to_limit,
    find_workspace_roots,
    compact_conversation as _compact_conversation,
)

__all__ = [
    "should_compact",
    "_compact_conversation",
    "_emergency_truncate_to_limit",
    "find_workspace_roots",
]

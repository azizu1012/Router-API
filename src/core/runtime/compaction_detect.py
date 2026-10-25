import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def is_compaction_request(messages: List[Dict[str, Any]]) -> bool:
    """Return whether the last user turn asks for a conversation summary."""
    if not messages or messages[-1].get("role") != "user":
        return False
    content = messages[-1].get("content", "")
    text = content if isinstance(content, str) else " ".join(
        str(part.get("text", "")) for part in content
        if isinstance(part, dict)
    ) if isinstance(content, list) else str(content)
    lowered = text.lower()
    return (
        "write a summary of this conversation" in lowered
        or ("write a summary" in lowered and "conversation" in lowered)
        or "compacting conversation" in lowered
        or "summary of the conversation" in lowered
        or "summarize this conversation" in lowered
        or "summarise this conversation" in lowered
        or "summarize the conversation" in lowered
        or "summarise the conversation" in lowered
        or "conversation compaction" in lowered
        or "compact the conversation" in lowered
        or ("summar" in lowered and "conversation" in lowered)
        or ("summar" in lowered and "context" in lowered and "preserve" in lowered)
    )

def strip_tools_for_compaction(body: Dict[str, Any], messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], is_claude_or_opencode: bool = False) -> None:
    """
    Detects if the request is a summarization/compaction request (like OpenCode or Claude Code's /compact).
    If it is, it clears the tools array and removes tool_choice from the body.
    This prevents the model from getting distracted by tools and returning a tool_call instead of the expected summary text.
    """
    if not messages:
        return
        
    last_msg = messages[-1]
    if last_msg.get("role") == "user":
        content = last_msg.get("content", "")
        content_str = str(content) if not isinstance(content, list) else " ".join(
            str(c.get("text", "")) for c in content if isinstance(c, dict)
        )
        
        is_explicit_compaction = is_compaction_request(messages)
        
        # Claude Code and OpenCode usually make non-streaming requests for compaction.
        # However, stripping tools purely based on non-streaming breaks valid non-stream tool calls.
        # So we only strip if the prompt explicitly asks for a summary/compaction.
        if is_explicit_compaction:
            tools.clear()
            # Internal metadata used by provider adapters; never forwarded to
            # the client. It keeps explicit compact turns text-only end to end.
            body["_router_compaction_request"] = True
            if "tool_choice" in body:
                del body["tool_choice"]
            logger.debug("Stripped tools for /compact summarization request")


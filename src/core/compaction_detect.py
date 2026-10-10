import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

def strip_tools_for_compaction(body: Dict[str, Any], messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], is_claude_or_opencode: bool = False) -> None:
    """
    Detects if the request is a summarization/compaction request (like OpenCode or Claude Code's /compact).
    If it is, it clears the tools array and removes tool_choice from the body.
    This prevents the model from getting distracted by tools and returning a tool_call instead of the expected summary text.
    """
    if not tools or not messages:
        return
        
    last_msg = messages[-1]
    if last_msg.get("role") == "user":
        content = last_msg.get("content", "")
        content_str = str(content) if not isinstance(content, list) else " ".join(
            str(c.get("text", "")) for c in content if isinstance(c, dict)
        )
        
        is_explicit_compaction = (
            "Write a summary of this conversation" in content_str or
            ("Write a summary" in content_str and "conversation" in content_str) or
            "Compacting conversation" in content_str or
            "summary of the conversation" in content_str
        )
        
        # Claude Code and OpenCode usually make non-streaming requests for compaction.
        # If it's non-streaming, has tools, and the client is claude/opencode, it's highly likely a compaction.
        is_non_stream_compaction = is_claude_or_opencode and not body.get("stream")
        
        if is_explicit_compaction or is_non_stream_compaction:
            tools.clear()
            if "tool_choice" in body:
                del body["tool_choice"]
            logger.debug("Stripped tools for /compact summarization request")

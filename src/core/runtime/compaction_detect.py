import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def is_compaction_request(messages: List[Dict[str, Any]]) -> bool:
    """Return whether the latest user turn asks for a conversation summary.

    Claude Code can include trailing assistant metadata/thinking messages after
    the compact instruction. Search backwards for the latest user turn instead
    of requiring it to be the final list element.
    """
    if not messages:
        return False
    user_message = next(
        (message for message in reversed(messages) if message.get("role") == "user"),
        None,
    )
    if user_message is None:
        return False
    content = user_message.get("content", "")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            str(part.get("text") or part.get("thinking") or "")
            for part in content if isinstance(part, dict)
        )
    else:
        text = str(content or "")
    lowered = text.lower()
    matches = (
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
    if not matches and lowered:
        # Claude Code's current compact prompt is a structured instruction with
        # both a summary task and an explicit prohibition on tool use. This
        # catches wording/version variants without classifying ordinary chat
        # requests that merely mention summaries.
        matches = (
            "summary" in lowered
            and ("conversation" in lowered or "messages" in lowered)
            and ("do not call any tools" in lowered or "do not use any tools" in lowered)
        )
    return matches

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


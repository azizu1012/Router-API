"""Sub-agent request detection.

Two detectors, both pure functions over a raw request body. They live in core/
because the request path reaches them from everywhere: middleware decides
whether to hand back a simulated 200 instead of propagating a 429, and the
proxies decide whether to enable web search.

They used to be reachable only through src.api.opencode_proxy and
src.logical_HQ_translator.sse_cache_agent, which meant auth middleware imported
the proxy layer to ask it a question about a request it had already parsed.
Both of those were function-body imports, so nothing looked wrong and nothing
was wrong at runtime — the layering was simply invisible. Keep these imports at
module level; if a future change appears to need a lazy one, confirm there is a
real cycle first. See docs/bug-logs.md Bug #6.

Precedence, both detectors, same order:

    1. MAIN_SESSION_INDICATORS in the system prompt -> False. A prompt that
       states it is the interactive/main session is the client talking to a
       human. This short-circuits before any keyword heuristic, because every
       other signal here is a heuristic and the main-session boilerplate is
       self-declared. See docs/bug-logs.md Bug #39.
    2. Sub-agent keywords in the system prompt -> True.
    3. An explicit [SUB-AGENT] marker in a user message -> True.

There is deliberately no signal derived from how many tools the request carries:
tool count says nothing about who is calling, and a main agent with MCP servers
installed legitimately sends 16-25 tools.
"""
import re
from typing import Any, Dict

# Phrases a client uses to introduce the session it is actually driving. Checked
# first, and shared by both detectors so a main session can never be classified
# differently depending on which proxy handled it.
MAIN_SESSION_INDICATORS = (
    "you are an interactive agent",
    "you are claude code",
    "you are the claude code cli",
    "interactive cli",
    "interactive agent",
    "main agent",
    "primary agent",
    "lead agent",
    "you are the main",
    "you are the primary",
)

# Explicit self-descriptions of a spawned worker agent.
SUB_AGENT_KEYWORDS = (
    "general-purpose agent",
    "general-purpose assistant",
    "explore agent",
    "file search specialist",
    "exploration task",
    "read-only exploration",
    "claude-code-guide",
    "statusline-setup",
    "specialized agent",
    "subagent",
    "sub-agent",  # also covers the "[sub-agent]" tag form
    "security monitor",
    "you are the claude-code-guide",
    "you are the explore",
    "you are the general-purpose",
    "you are the statusline-setup",
)

# Opencode ships its agent prompts with a much vaguer vocabulary, so the same
# keyword list cannot tell a worker from the main session on its own.
OPENCODE_SUB_AGENT_KEYWORDS = (
    "explore",
    "read file",
    "search",
    "find",
    "glob",
    "grep",
    "task agent",
    "subagent",
    "sub-agent",
    "read files",
    "browse",
)

_SUB_AGENT_TAG = "[SUB-AGENT]"
_RE_YOU_ARE_SUB_AGENT = re.compile(r"you are (a|an|the)[\s\w\-]*sub.?agent")


def _flatten_system(value: Any) -> str:
    """Flatten a system-prompt field: a plain string, a content-block list, or one block."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("text", ""))
    if isinstance(value, list):
        return "\n".join([str(item.get("text", "")) for item in value if isinstance(item, dict)])
    return ""


def _system_prompt(body: Dict[str, Any]) -> str:
    """System prompt from the top-level `system` field (Anthropic and OpenAI shapes)."""
    return _flatten_system(body.get("system", ""))


def _system_prompt_with_messages(body: Dict[str, Any]) -> str:
    """Top-level `system`, falling back to the first system/developer message.

    OpenAI-compatible clients (and opencode itself) usually put the prompt in
    messages[0] instead of a `system` field. Both detectors read through this
    helper so a main session is recognised the same way on either wire format.
    """
    system_prompt = _system_prompt(body)
    if system_prompt:
        return system_prompt
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict) or msg.get("role") not in ("system", "developer"):
            continue
        return _flatten_system(msg.get("content", ""))
    return ""


def _is_main_session(system_prompt_lower: str) -> bool:
    return any(indicator in system_prompt_lower for indicator in MAIN_SESSION_INDICATORS)


def _has_sub_agent_marker(body: Dict[str, Any]) -> bool:
    """Explicit [SUB-AGENT] tag on a user message, string or content-block form."""
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if _SUB_AGENT_TAG in content:
                return True
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    if _SUB_AGENT_TAG in str(block.get("text", "")):
                        return True
    return False


def is_sub_agent_body(body: Dict[str, Any]) -> bool:
    if not body:
        return False

    system_prompt_lower = _system_prompt_with_messages(body).lower()

    if system_prompt_lower:
        # Main session first: self-declared, so it outranks every heuristic below.
        if _is_main_session(system_prompt_lower):
            return False
        if any(kw in system_prompt_lower for kw in SUB_AGENT_KEYWORDS):
            return True
        if _RE_YOU_ARE_SUB_AGENT.search(system_prompt_lower):
            return True

    return _has_sub_agent_marker(body)


def is_sub_agent_request_openai(body: Dict[str, Any]) -> bool:
    """Detect if request is from an opencode sub-agent."""
    if not body:
        return False

    system_prompt_lower = _system_prompt_with_messages(body).lower()

    # Same precedence as is_sub_agent_body: an opencode main session mentions
    # "search", "find" and "explore" all over its own prompt, so the keyword
    # list below is unusable unless the main-session check runs first.
    if _is_main_session(system_prompt_lower):
        return False

    if is_sub_agent_body(body):
        return True

    if "opencode" not in system_prompt_lower:
        return False
    return any(kw in system_prompt_lower for kw in OPENCODE_SUB_AGENT_KEYWORDS)
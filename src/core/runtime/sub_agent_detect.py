"""Sub-agent request detection.

Two detectors, both pure functions over a raw request body. They live in core/
because the request path reaches them from everywhere: middleware decides
whether to hand back a simulated 200 instead of propagating a 429, and the
proxies decide whether to enable web search.

They used to be reachable only through src.api.opencode_proxy and
src.logical_HQ_translator.streaming.cache_agent, which meant auth middleware imported
the proxy layer to ask it a question about a request it had already parsed.
Both of those were function-body imports, so nothing looked wrong and nothing
was wrong at runtime — the layering was simply invisible. Keep these imports at
module level; if a future change appears to need a lazy one, confirm there is a
real cycle first. See docs/bug-logs.md Bug #6.

Precedence, both detectors, same order:

    1. "you are a/an/the ... sub-agent" in the system prompt -> True. This is
       the only signal where the *caller states its own role* in the first
       person, so it outranks everything below. It used to sit third, behind
       MAIN_SESSION_INDICATORS, which meant a worker's closing instruction —
       "..., then report back to the main agent" — flipped its verdict. See
       docs/bug-logs.md Bug #40.
    2. MAIN_SESSION_INDICATORS in the system prompt -> False. A prompt that
       states it is the interactive/main session is the client talking to a
       human. See docs/bug-logs.md Bug #39.
    3. Sub-agent keywords in the system prompt -> True.
    4. An explicit [SUB-AGENT] marker on the user turn being answered -> True.

The dangerous direction here is asymmetric on purpose: a main session
misclassified as a sub-agent loses thinking, loses web search, and has its
errors answered with a fabricated HTTP 200, while a sub-agent misclassified as
main only keeps thinking it did not need. Every rule below therefore prefers the
main-session verdict unless the evidence is a self-declaration.

There is deliberately no signal derived from how many tools the request carries:
tool count says nothing about who is calling, and a main agent with MCP servers
installed legitimately sends 16-25 tools.
"""
import re
from typing import Any, Dict

# Phrases a client uses to introduce the session it is actually driving. Checked
# first, and shared by both detectors so a main session can never be classified
# differently depending on which proxy handled it.
#
# Every entry carries a subject. The bare noun phrases that used to be here —
# "main agent", "primary agent", "lead agent", "you are the main" — matched a
# *mention* of a parent rather than a declaration of self, and a worker prompt
# ends with "report back to the main agent" more often than not, so those four
# strings turned real sub-agents into main sessions. See docs/bug-logs.md Bug #40.
MAIN_SESSION_INDICATORS = (
    "you are an interactive agent",
    "you are claude code",
    "you are the claude code cli",
    # Claude Code versions that omit the first-person sentence still identify
    # themselves with this stable marker in the system prompt.
    "cc_version=",
    "claude-code",
    "interactive cli",
    "interactive agent",
    # Not "interactive agent": opencode ships "an interactive coding assistant",
    # which the phrase above misses, and its main prompt then falls through to
    # the keyword list. See docs/bug-logs.md Bug #40 item B.
    "interactive coding assistant",
    "you are opencode",
    "you are the main agent",
    "you are the primary agent",
    "you are the lead agent",
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
#
# These used to be plain English words — "explore", "search", "find", "glob",
# "grep", "browse", "read file" — which any coding assistant's prompt contains
# several times over. That made the whole list a coin flip on ordinary main
# sessions, and the ten boilerplate phrases above the only thing holding the
# line. What is left are phrases that name a *role*, not an activity. See
# docs/bug-logs.md Bug #40 item B.
OPENCODE_SUB_AGENT_KEYWORDS = (
    "explore agent",
    "general-purpose agent",
    "read-only exploration",
    "task agent",
    "subagent",
    "sub-agent",
)

_SUB_AGENT_TAG = "[SUB-AGENT]"

# "you are a sub-agent", "you are the research sub-agent", "you are an explore
# sub-agent". The gap is capped at two words and cannot cross a sentence or line
# boundary, which is what keeps the outranking rule in the module docstring
# honest: with an unbounded gap, "you are the main agent that coordinates
# sub-agents" also matched, and the main session that owns workers would have
# declared itself one.
_RE_YOU_ARE_SUB_AGENT = re.compile(r"you are (?:a|an|the)(?:\s+\w+){0,2}?\s+sub[\s-]?agent")


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


def _declares_sub_agent(system_prompt_lower: str) -> bool:
    """The prompt introduces itself, in the first person, as a worker."""
    return bool(_RE_YOU_ARE_SUB_AGENT.search(system_prompt_lower))


def _last_user_message(body: Dict[str, Any]) -> Dict[str, Any]:
    """The user turn being answered — the last one in the array.

    Not the last message of any role: a tool-result turn can be the final entry
    without changing whose turn it is.
    """
    last_user = None
    for msg in body.get("messages") or []:
        if isinstance(msg, dict) and msg.get("role") == "user":
            last_user = msg
    return last_user


def _opens_with_tag(text: str) -> bool:
    """`text` opens with the tag and nothing else on that line."""
    stripped = text.lstrip()
    if not stripped.startswith(_SUB_AGENT_TAG):
        return False
    rest = stripped[len(_SUB_AGENT_TAG):].lstrip(" \t")
    return rest == "" or rest[:1] in ("\r", "\n")


def _has_sub_agent_marker(body: Dict[str, Any]) -> bool:
    """Explicit [SUB-AGENT] tag on the turn being answered.

    Three constraints, each closing a way a main session used to get pinned.
    Detection is stateless and recomputed per request, so anything looser
    applies for the rest of the conversation, not just for one turn:

    * only the last user message. Scanning the history meant a single quoted
      worker transcript anywhere above locked the whole session;
    * the tag must open the message. A user asking what "[SUB-AGENT]" means is
      asking a question, not dispatching a worker;
    * the tag must be alone on its line. "[SUB-AGENT] finished the migration" is
      a main agent reporting a worker's result, and every character after the
      tag is that report rather than a prompt for one.
    """
    last_user = _last_user_message(body)
    if last_user is None:
        return False

    content = last_user.get("content")
    if isinstance(content, str):
        return _opens_with_tag(content)
    if isinstance(content, list):
        # First text block only — the blocks after it are quoted material by
        # construction.
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return _opens_with_tag(str(block.get("text", "")))
    return False


def is_sub_agent_body(body: Dict[str, Any]) -> bool:
    if not body:
        return False

    system_prompt_lower = _system_prompt_with_messages(body).lower()

    if system_prompt_lower:
        # These are identity markers from the interactive Claude Code client,
        # not mentions of a worker role. They win over quoted worker keywords
        # embedded in the large Claude system prompt.
        if any(marker in system_prompt_lower for marker in (
            "you are claude code",
            "you are the claude code cli",
            "cc_version=",
            "claude-code",
        )):
            return False
        # A first-person self-declaration outranks the generic main-session
        # indicators when no client identity marker is present.
        if _declares_sub_agent(system_prompt_lower):
            return True
        if _is_main_session(system_prompt_lower):
            return False
        if any(kw in system_prompt_lower for kw in SUB_AGENT_KEYWORDS):
            return True

    return _has_sub_agent_marker(body)


def is_sub_agent_request_openai(body: Dict[str, Any]) -> bool:
    """Detect if request is from an opencode sub-agent."""
    if not body:
        return False

    system_prompt_lower = _system_prompt_with_messages(body).lower()

    # Main session first: an opencode main session mentions its tools all over
    # its own prompt, so the opencode keyword list is only meaningful once the
    # self-declared main session has been taken out of the running.
    if _is_main_session(system_prompt_lower):
        return False

    if is_sub_agent_body(body):
        return True

    if "opencode" not in system_prompt_lower:
        return False
    return any(kw in system_prompt_lower for kw in OPENCODE_SUB_AGENT_KEYWORDS)

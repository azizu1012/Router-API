"""Anthropic usage and thinking accounting helpers."""

import hashlib
from typing import Any, Dict, List

from .response import _REFUSAL_FINISH_REASONS



MIN_CACHEABLE_TOKENS = 1024

def estimate_tokens(text: str) -> int:
    """Rough token estimate. Matches the len/4 heuristic used elsewhere in the repo."""
    if not text:
        return 0
    return max(1, len(text) // 4)

def count_cache_breakpoints(body: Dict[str, Any]) -> int:
    """Count how many `cache_control` markers the client sent.

    Claude Code places these on the system prompt, on tool definitions, and on the
    last few message blocks. Each marker is a place the client expects to be able
    to reuse a cached prefix.
    """
    count = 0

    system = body.get("system")
    if isinstance(system, list):
        count += sum(1 for b in system if isinstance(b, dict) and b.get("cache_control"))

    for tool in body.get("tools") or []:
        if isinstance(tool, dict) and tool.get("cache_control"):
            count += 1

    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            count += sum(1 for b in content if isinstance(b, dict) and b.get("cache_control"))

    return count

def compute_usage(
    body: Dict[str, Any],
    total_input_tokens: int,
    output_tokens: int,
) -> Dict[str, Any]:
    """Build an Anthropic-shaped usage block.

    Gemini has no prompt cache, so we cannot report real cache hits. What we *can*
    do — and what Claude Code needs in order to render /context at all — is report
    the four required fields consistently. When the client sent cache_control
    breakpoints we attribute the stable prefix to cache_read and the remainder to
    fresh input, which is the shape a real Anthropic response has.

    Callers add `server_tool_use`, a nested object rather than a counter, so the
    value type is not `int` alone.
    """
    total = max(0, int(total_input_tokens))
    usage = {
        "input_tokens": total,
        "output_tokens": max(0, int(output_tokens)),
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }

    # Anthropic only caches prefixes above a minimum size, so a short request with
    # cache_control markers is never actually cached. Reporting cache hits there
    # would make /context claim a discount the client cannot rely on.
    if count_cache_breakpoints(body) > 0 and total >= MIN_CACHEABLE_TOKENS:
        cached = int(total * 0.75)
        usage["cache_read_input_tokens"] = cached
        usage["input_tokens"] = total - cached

    return usage

def thinking_signature(thinking_text: str) -> str:
    """Deterministic signature for a thinking block.

    Anthropic requires a non-empty signature on every thinking block. We have no
    real signature to echo back, so derive a stable one from the content.
    """
    return "gmni_" + hashlib.sha256((thinking_text or "").encode()).hexdigest()[:60]

def estimate_input_tokens(body: Dict[str, Any]) -> int:
    """Estimate the *client-visible* input tokens for a request body.

    Counts system + message text + tool schemas. This is what the client sees in
    its own context window, so it must NOT be derived from backend key accounting.
    """
    parts: List[str] = []

    system = body.get("system")
    if isinstance(system, str):
        parts.append(system)
    elif isinstance(system, list):
        parts.extend(str(b.get("text", "")) for b in system if isinstance(b, dict))

    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    parts.append(str(block.get("text", "")))
                elif btype in ("image", "image_url"):
                    parts.append(" " * 6400)
                elif btype == "tool_use":
                    parts.append(str(block.get("input", "")))
                elif btype == "tool_result":
                    c = block.get("content", "")
                    if isinstance(c, str):
                        parts.append(c)
                    elif isinstance(c, list):
                        parts.extend(str(x.get("text", "")) for x in c if isinstance(x, dict))

    for tool in body.get("tools") or []:
        if isinstance(tool, dict):
            parts.append(str(tool.get("name", "")))
            parts.append(str(tool.get("description", "")))

    return max(1, estimate_tokens("\n".join(p for p in parts if p)))


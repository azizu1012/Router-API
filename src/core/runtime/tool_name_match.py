"""Match a model-emitted tool name back to a name the client declared.

Gemini does not always echo a tool name verbatim. It rewrites the namespace
sometimes — ``mcp__codegraph__codegraph_explore`` comes back as
``codegraph__codegraph_explore`` — and the qualified form ``default_api:...``
shows up in provider errors. A client that receives a name it never declared
rejects the call (``No such tool available``), and the turn is lost even though
the model picked a tool the client does have.

Every tier must produce exactly one match. An ambiguous name is returned
unchanged, because picking one of several candidates would hand the client a
call it never asked for. An unknown name is also returned unchanged: this
module never invents a tool, so a genuine failure stays visible instead of
turning into a different one.

The sanitizer is mirrored locally rather than imported from
``src/core/providers/gemini/format`` so this module stays on the neutral floor
that every layer may import. ``tests/test_tool_name_match.py`` pins the copy to
the original by comparing them on the same inputs.
"""

import re
from typing import Any, Iterable, List, Optional, Sequence, Set


# Prefixes providers add around a tool name on the way out. They are transport
# qualifiers, not part of the name the client declared, so they are stripped
# before comparing. Ordered only for readability; stripping repeats until no
# prefix applies, because a name can carry more than one.
TRANSPORT_PREFIXES = (
    "default_api:",
    "functions.",
    "function.",
    "tools.",
    "tools__",
    "mcp__",
)


def declared_tool_names(tools: Any) -> List[str]:
    """Collect the tool names from an OpenAI ``tools`` list.

    Two nesting shapes are accepted: ``{"type": "function", "function":
    {"name": ...}}`` (what this router builds) and the flattened
    ``{"type": "function", "name": ...}`` some endpoints use. Plain strings are
    accepted too, so a caller that already holds names can pass them directly.
    Order is preserved and duplicates are dropped.
    """
    if not tools:
        return []
    if isinstance(tools, (str, dict)):
        tools = [tools]

    names: List[str] = []
    seen: Set[str] = set()
    for tool in tools:
        name = ""
        if isinstance(tool, str):
            name = tool
        elif isinstance(tool, dict):
            function = tool.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                name = function["name"]
            elif isinstance(tool.get("name"), str):
                name = tool["name"]
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def resolve_declared_tool_name(emitted: Any, declared: Iterable[str]) -> str:
    """Return the declared name that ``emitted`` refers to.

    The emitted name is returned unchanged when it already matches exactly,
    when no tier finds a candidate, when more than one candidate matches, or
    when the input is not a non-empty string.
    """
    if not isinstance(emitted, str) or not emitted:
        return emitted

    if isinstance(declared, str):
        declared = [declared]
    names = [name for name in (declared or ()) if isinstance(name, str) and name]
    if not names or emitted in names:
        return emitted

    for matcher in (
        _match_transport_prefix,
        _match_segment_suffix,
        _match_sanitized,
    ):
        match = matcher(emitted, names)
        if match is not None:
            return match
    return emitted


def _match_transport_prefix(emitted: str, names: Sequence[str]) -> Optional[str]:
    """Compare after removing provider-added prefixes from both sides."""
    target = _strip_transport_prefixes(emitted)
    matched = [name for name in names if _strip_transport_prefixes(name) == target]
    return matched[0] if len(matched) == 1 else None


def _match_segment_suffix(emitted: str, names: Sequence[str]) -> Optional[str]:
    """Match a name that lost (or gained) leading ``__``-separated segments.

    ``codegraph_explore`` versus ``mcp__codegraph__codegraph_explore`` is the
    case that started this: the transport prefix is gone, but the middle
    namespace can also be missing. Splitting on ``__`` keeps the comparison
    from matching ``explore`` against ``reexplore``.
    """
    target = _strip_transport_prefixes(emitted)
    matched = []
    for name in names:
        candidate = _strip_transport_prefixes(name)
        if candidate.endswith("__" + target) or target.endswith("__" + candidate):
            matched.append(name)
    return matched[0] if len(matched) == 1 else None


def _match_sanitized(emitted: str, names: Sequence[str]) -> Optional[str]:
    """Compare after applying the same sanitization used for declarations.

    A client tool named ``my tool`` reaches Gemini as ``my_tool`` and comes
    back that way, so the declared spelling would otherwise never match.
    """
    target_forms = _sanitized_forms(emitted)
    matched = [name for name in names if target_forms & _sanitized_forms(name)]
    return matched[0] if len(matched) == 1 else None


def _sanitized_forms(name: str) -> Set[str]:
    """Sanitized candidates for a name, before and after prefix stripping."""
    stripped = _strip_transport_prefixes(name)
    forms = {_sanitize_like_gemini(name)}
    if stripped != name:
        forms.add(_sanitize_like_gemini(stripped))
    return forms


def _strip_transport_prefixes(name: str) -> str:
    """Remove every known transport prefix, repeatedly.

    Repeats because a name can carry more than one, and never reduces a name to
    an empty string: a tool literally called ``mcp__`` is left alone.
    """
    changed = True
    while changed:
        changed = False
        for prefix in TRANSPORT_PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix):
                name = name[len(prefix):]
                changed = True
    return name


def _sanitize_like_gemini(name: str) -> str:
    """Mirror of ``providers.gemini.format.sanitize_function_name``.

    Kept byte-for-byte equivalent on purpose; the agreement test in
    ``tests/test_tool_name_match.py`` fails if either copy drifts.
    """
    if not name:
        return "_unknown"
    sanitized = re.sub(r"[^a-zA-Z0-9_.:\-]", "_", name)
    if not re.match(r"^[a-zA-Z_]", sanitized):
        sanitized = "_" + sanitized
    return sanitized[:64]

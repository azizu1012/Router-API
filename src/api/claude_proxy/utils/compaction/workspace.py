"""Workspace root detection from conversation messages.

Scans tool-call arguments and message content for file-system paths,
then resolves the most likely project root directories.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger


# Marker files that identify a project root directory.
PROJECT_MARKERS = [".git", "package.json", "requirements.txt", ".env", "tsconfig.json"]


def find_workspace_roots(messages: List[Dict[str, Any]]) -> List[Path]:
    """Extract and rank candidate project-root directories from conversation.

    Scans tool-call arguments and message text for absolute file paths,
    validates them against the filesystem, and returns a deduplicated
    list of ``Path`` objects — roots with project markers first.
    """
    paths_found = _extract_paths(messages)
    if not paths_found:
        return _add_fallback_roots([])

    candidate_dirs = _resolve_candidate_dirs(paths_found)
    return _rank_roots(list(candidate_dirs))


# ── Internal helpers ────────────────────────────────────────────

def _extract_paths(messages: List[Dict[str, Any]]) -> List[str]:
    """Pull paths from tool-call arguments and text content."""
    found: List[str] = []

    for m in messages:
        _extract_from_tool_calls(m, found)
        _extract_from_content(m, found)

    return found


def _extract_from_tool_calls(msg: Dict[str, Any], found: List[str]) -> None:
    tc = msg.get("tool_calls")
    if not tc:
        return
    for t in tc:
        fn = t.get("function", {})
        args_str = fn.get("arguments", "")
        if not args_str:
            continue
        try:
            args = json.loads(args_str)
            for val in args.values():
                if isinstance(val, str) and (":" in val or "/" in val or "\\" in val):
                    found.append(val)
        except Exception:
            pass


def _extract_from_content(msg: Dict[str, Any], found: List[str]) -> None:
    content = msg.get("content", "")
    if isinstance(content, str) and content:
        found.extend(re.findall(r'[a-zA-Z]:\\[\\\w\s\.\-\(\)_]+', content))
        found.extend(re.findall(r'/[/\w\s\.\-\(\)_]+', content))
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                txt = block.get("text", "")
                found.extend(re.findall(r'[a-zA-Z]:\\[\\\w\s\.\-\(\)_]+', txt))
                found.extend(re.findall(r'/[/\w\s\.\-\(\)_]+', txt))


def _resolve_candidate_dirs(paths: List[str]) -> set:
    dirs: set = set()
    for p_str in paths:
        try:
            p = Path(p_str.strip().rstrip("\\/"))
            if not str(p).strip():
                continue
            candidate = p.parent if p.is_file() else p
            if candidate.is_absolute():
                dirs.add(candidate)
                for parent in candidate.parents:
                    dirs.add(parent)
        except Exception:
            pass
    return dirs


def _rank_roots(candidates: List[Path]) -> List[Path]:
    """Separate roots with project markers from others, then deduplicate."""
    marked_roots: List[Path] = []
    other_roots: List[Path] = []

    for d in candidates:
        try:
            if d.exists() and d.is_dir():
                (marked_roots if _has_project_marker(d) else other_roots).append(d)
        except Exception:
            pass

    seen: set = set()
    result: List[Path] = []
    for r in marked_roots + other_roots:
        try:
            resolved = str(r.resolve())
            if resolved not in seen:
                seen.add(resolved)
                result.append(r)
        except Exception:
            pass

    return _add_fallback_roots(result)


def _has_project_marker(d: Path) -> bool:
    return any((d / marker).exists() for marker in PROJECT_MARKERS)


def _add_fallback_roots(roots: List[Path]) -> List[Path]:
    """Ensure cwd and PROJECT_ROOT are included."""
    seen = set(str(r.resolve()) for r in roots if r.exists())

    for candidate in [Path(os.getcwd()).resolve(), Path(config.PROJECT_ROOT).resolve()]:
        try:
            if str(candidate) not in seen:
                roots.append(candidate)
                seen.add(str(candidate))
        except Exception:
            pass

    return roots

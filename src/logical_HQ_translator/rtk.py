import re
from typing import Any, Dict, List, Optional, Tuple, Callable

RAW_CAP = 10 * 1024 * 1024       # 10 MiB
MIN_COMPRESS_SIZE = 500           # bytes
DETECT_WINDOW = 1024              # characters
GIT_DIFF_HUNK_MAX_LINES = 100
STATUS_MAX_FILES = 10
STATUS_MAX_UNTRACKED = 10
GREP_PER_FILE_MAX = 10
FIND_PER_DIR_MAX = 10
FIND_TOTAL_DIR_MAX = 20
SMART_TRUNCATE_HEAD = 120
SMART_TRUNCATE_TAIL = 60
SMART_TRUNCATE_MIN_LINES = 250
READ_NUMBERED_MIN_HIT_RATIO = 0.7

LS_NOISE_DIRS = {
    "node_modules", ".git", "target", "__pycache__",
    ".next", "dist", "build", ".cache", ".turbo",
    ".vercel", ".pytest_cache", ".mypy_cache", ".tox",
    ".venv", "venv", "env", "coverage", ".nyc_output",
    ".DS_Store", "Thumbs.db", ".idea", ".vscode", ".vs",
    "*.egg-info", ".eggs"
}

# Regex patterns
RE_GIT_DIFF = re.compile(r'^diff --git ', re.MULTILINE)
RE_GIT_DIFF_HUNK = re.compile(r'^@@ ', re.MULTILINE)
RE_GIT_STATUS = re.compile(r'^On branch |^nothing to commit|^Changes (not |to be )|^Untracked files:', re.MULTILINE)
RE_PORCELAIN = re.compile(r'^[ MADRCU?!][ MADRCU?!] \S', re.MULTILINE)
RE_BUILD_OUTPUT = re.compile(
    r'^(npm (warn|error|ERR!)|yarn (warn|error)|\s*Compiling\s+\S+|\s*Downloading\s+\S+|added \d+ package|\[ERROR\]|BUILD (SUCCESS|FAILED)|\s*Finished\s+|Successfully (installed|built)|ERROR:)',
    re.IGNORECASE | re.MULTILINE
)
RE_TREE_GLYPH = re.compile(r'[├└]──|│  ')
RE_LS_ROW = re.compile(r'^[-dlbcps][rwx-]{9}', re.MULTILINE)
RE_LS_TOTAL = re.compile(r'^total \d+$', re.MULTILINE)
READ_NUMBERED_LINE_RE = re.compile(r'^\s*\d+\|')
SEARCH_LIST_HEADER_RE = re.compile(r'^Result of search in \'.*\' \(total \d+ files\):')

LS_DATE_RE = re.compile(r'\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+(\d{4}|\d{2}:\d{2})\s+')
RE_CARGO_ERR_CONT = re.compile(r'^\s*(-->|\||\d+\s*\||=)')


def filter_git_diff(diff: str, max_lines=500) -> str:
    result = []
    current_file = ""
    added = 0
    removed = 0
    in_hunk = False
    hunk_shown = 0
    hunk_skipped = 0
    was_truncated = False
    max_hunk_lines = GIT_DIFF_HUNK_MAX_LINES

    lines = diff.split("\n")

    for line in lines:
        if line.startswith("diff --git"):
            if hunk_skipped > 0:
                result.append(f"  ... ({hunk_skipped} lines truncated)")
                was_truncated = True
                hunk_skipped = 0
            if current_file and (added > 0 or removed > 0):
                result.append(f"  +{added} -{removed}")
            parts = line.split(" b/")
            current_file = parts[1] if len(parts) > 1 else "unknown"
            result.append(f"\n{current_file}")
            added = 0
            removed = 0
            in_hunk = False
            hunk_shown = 0
        elif line.startswith("@@"):
            if hunk_skipped > 0:
                result.append(f"  ... ({hunk_skipped} lines truncated)")
                was_truncated = True
                hunk_skipped = 0
            in_hunk = True
            hunk_shown = 0
            result.append(f"  {line}")
        elif in_hunk:
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
                if hunk_shown < max_hunk_lines:
                    result.append(f"  {line}")
                    hunk_shown += 1
                else:
                    hunk_skipped += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
                if hunk_shown < max_hunk_lines:
                    result.append(f"  {line}")
                    hunk_shown += 1
                else:
                    hunk_skipped += 1
            elif hunk_shown < max_hunk_lines and not line.startswith("\\"):
                if hunk_shown > 0:
                    result.append(f"  {line}")
                    hunk_shown += 1

        if len(result) >= max_lines:
            result.append("\n... (more changes truncated)")
            was_truncated = True
            break

    if hunk_skipped > 0:
        result.append(f"  ... ({hunk_skipped} lines truncated)")
        was_truncated = True

    if current_file and (added > 0 or removed > 0):
        result.append(f"  +{added} -{removed}")

    if was_truncated:
        result.append("[full diff: rtk git diff --no-compact]")

    return "\n".join(result)


def filter_git_status(input_str: str) -> str:
    lines = input_str.split("\n")
    if not lines or (len(lines) == 1 and not lines[0].strip()):
        return "Clean working tree"

    branch = ""
    staged_files = []
    modified_files = []
    untracked_files = []
    staged = 0
    modified = 0
    untracked = 0
    conflicts = 0

    for raw in lines:
        if not raw.strip():
            continue

        long_branch = re.match(r'^On branch (\S+)', raw)
        if long_branch:
            branch = long_branch.group(1)
            continue

        if raw.startswith("##"):
            branch = re.sub(r'^##\s*', '', raw)
            continue

        if len(raw) >= 3 and RE_PORCELAIN.match(raw):
            x = raw[0]
            y = raw[1]
            file = raw[3:]

            if raw.startswith("??"):
                untracked += 1
                untracked_files.append(file)
                continue

            if x in "MADRC":
                staged += 1
                staged_files.append(file)
            elif x == "U":
                conflicts += 1

            if y in ("M", "D"):
                modified += 1
                modified_files.append(file)
            continue

        long_match = re.match(r'^\s*(modified|new file|deleted|renamed|both modified):\s+(.+)$', raw)
        if long_match:
            kind = long_match.group(1)
            path = long_match.group(2).strip()
            if kind == "both modified":
                conflicts += 1
            elif kind in ("modified", "deleted"):
                modified += 1
                modified_files.append(path)
            elif kind in ("new file", "renamed"):
                staged += 1
                staged_files.append(path)
            continue

    out = []
    if branch:
        out.append(f"* {branch}")

    if staged > 0:
        out.append(f"+ Staged: {staged} files")
        for f in staged_files[:STATUS_MAX_FILES]:
            out.append(f"   {f}")
        if len(staged_files) > STATUS_MAX_FILES:
            out.append(f"   ... +{len(staged_files) - STATUS_MAX_FILES} more")

    if modified > 0:
        out.append(f"~ Modified: {modified} files")
        for f in modified_files[:STATUS_MAX_FILES]:
            out.append(f"   {f}")
        if len(modified_files) > STATUS_MAX_FILES:
            out.append(f"   ... +{len(modified_files) - STATUS_MAX_FILES} more")

    if untracked > 0:
        out.append(f"? Untracked: {untracked} files")
        for f in untracked_files[:STATUS_MAX_UNTRACKED]:
            out.append(f"   {f}")
        if len(untracked_files) > STATUS_MAX_UNTRACKED:
            out.append(f"   ... +{len(untracked_files) - STATUS_MAX_UNTRACKED} more")

    if conflicts > 0:
        out.append(f"conflicts: {conflicts} files")

    if staged == 0 and modified == 0 and untracked == 0 and conflicts == 0:
        out.append("clean — nothing to commit")

    return "\n".join(out).strip()


def filter_grep(input_str: str) -> str:
    by_file = {}
    total = 0

    for line in input_str.split("\n"):
        first = line.find(":")
        if first == -1:
            continue
        second = line.find(":", first + 1)
        if second == -1:
            continue
        file = line[:first]
        line_num_str = line[first + 1:second]
        content = line[second + 1:]
        if not line_num_str.isdigit():
            continue
        total += 1
        if file not in by_file:
            by_file[file] = []
        by_file[file].append((line_num_str, content))

    if total == 0:
        return input_str

    files = sorted(by_file.keys())
    out = [f"{total} matches in {len(files)}F:\n"]

    for file in files:
        matches = by_file[file]
        out.append(f"[file] {file} ({len(matches)}):")
        show = matches[:GREP_PER_FILE_MAX]
        for line_num, content in show:
            out.append(f"  {line_num.rjust(4)}: {content.strip()}")
        if len(matches) > GREP_PER_FILE_MAX:
            out.append(f"  +{len(matches) - GREP_PER_FILE_MAX}")
        out.append("")

    return "\n".join(out)


def filter_find(input_str: str) -> str:
    lines = [l.strip() for l in input_str.split("\n") if l.strip()]
    if not lines:
        return input_str

    by_dir = {}
    for path in lines:
        last_slash = path.rfind("/")
        if last_slash == -1:
            directory = "."
            basename = path
        else:
            directory = path[:last_slash] or "/"
            basename = path[last_slash + 1:]
        if directory not in by_dir:
            by_dir[directory] = []
        by_dir[directory].append(basename)

    dirs = sorted(by_dir.keys())
    out = [f"{len(lines)} files in {len(dirs)} dirs:\n"]

    show_dirs = dirs[:FIND_TOTAL_DIR_MAX]
    for directory in show_dirs:
        files = by_dir[directory]
        out.append(f"{directory}/  ({len(files)})")
        show_files = files[:FIND_PER_DIR_MAX]
        for f in show_files:
            out.append(f"  {f}")
        if len(files) > FIND_PER_DIR_MAX:
            out.append(f"  +{len(files) - FIND_PER_DIR_MAX}")

    if len(dirs) > FIND_TOTAL_DIR_MAX:
        out.append(f"\n+{len(dirs) - FIND_TOTAL_DIR_MAX} more dirs")

    return "\n".join(out)


def filter_smart_truncate(input_str: str) -> str:
    lines = input_str.split("\n")
    if len(lines) < SMART_TRUNCATE_MIN_LINES:
        return input_str

    head = lines[:SMART_TRUNCATE_HEAD]
    tail = lines[-SMART_TRUNCATE_TAIL:]
    cut = len(lines) - len(head) - len(tail)
    return "\n".join(head + [f"... +{cut} lines truncated"] + tail)


def filter_read_numbered(input_str: str) -> str:
    lines = input_str.split("\n")
    if len(lines) < SMART_TRUNCATE_MIN_LINES:
        return input_str

    head = lines[:SMART_TRUNCATE_HEAD]
    tail = lines[-SMART_TRUNCATE_TAIL:]
    cut = len(lines) - len(head) - len(tail)
    return "\n".join(head + [f"... +{cut} lines truncated (file continues)"] + tail)


def filter_build_output(input_str: str) -> str:
    lines = input_str.split("\n")
    if not lines:
        return input_str

    errors = []
    warnings = []
    deprecations = []
    summary_parts = []
    compiling_count = 0
    downloading_count = 0
    in_cargo_error = False

    for line in lines:
        trimmed = line.strip()

        if in_cargo_error:
            if not trimmed:
                in_cargo_error = False
                continue
            if RE_CARGO_ERR_CONT.match(line):
                errors.append(line)
                continue
            in_cargo_error = False

        if not trimmed:
            continue

        if re.match(r'^npm (ERR!|error)', trimmed, re.IGNORECASE) or re.match(r'^yarn error', trimmed, re.IGNORECASE):
            errors.append(line)
            continue

        if re.match(r'^npm warn deprecated', trimmed, re.IGNORECASE):
            deprecations.append(line)
            continue
        if re.match(r'^npm warn', trimmed, re.IGNORECASE) or re.match(r'^yarn warn', trimmed, re.IGNORECASE):
            warnings.append(line)
            continue

        if re.match(r'^error(\[|:)', trimmed, re.IGNORECASE) or trimmed.startswith("error -->"):
            errors.append(line)
            in_cargo_error = True
            continue

        if re.match(r'^warning(\[|:)', trimmed, re.IGNORECASE) or trimmed.startswith("warning -->"):
            warnings.append(line)
            in_cargo_error = True
            continue

        if trimmed.upper().startswith("ERROR:"):
            errors.append(line)
            continue

        if trimmed.upper().startswith("[ERROR]") or trimmed.upper().startswith("BUILD FAILED"):
            errors.append(line)
            continue

        if trimmed.upper().startswith("[WARNING]"):
            warnings.append(line)
            continue

        if re.match(r'^\s*Compiling\s+\S+', trimmed, re.IGNORECASE):
            compiling_count += 1
            continue
        if re.match(r'^\s*Downloading\s+\S+', trimmed, re.IGNORECASE) or re.match(r'^Fetching\s+', trimmed, re.IGNORECASE):
            downloading_count += 1
            continue

        if (
            re.match(r'^(added|removed|changed|audited|installed)\s+\d+\s+package', trimmed, re.IGNORECASE) or
            re.match(r'^\s*Finished\s+', trimmed, re.IGNORECASE) or
            trimmed.upper().startswith("BUILD SUCCESS") or
            re.match(r'^\d+\s+(vulnerabilities|packages?|warnings?|errors?)', trimmed, re.IGNORECASE) or
            re.match(r'^Successfully (installed|built)', trimmed, re.IGNORECASE) or
            trimmed.startswith("To address ") or
            trimmed.startswith("Run `npm ") or
            "packages are looking for funding" in trimmed
        ):
            summary_parts.append(line)
            continue

    out = []
    keep_dep = deprecations[:3]
    for d in keep_dep:
        out.append(d)
    if len(deprecations) > 3:
        out.append(f"... +{len(deprecations) - 3} more deprecated packages")

    if compiling_count > 0:
        out.append(f"Compiled {compiling_count} packages")
    if downloading_count > 0:
        out.append(f"Downloaded {downloading_count} packages")

    for e in errors:
        out.append(e)

    keep_warnings = warnings[:5]
    for w in keep_warnings:
        out.append(w)
    if len(warnings) > 5:
        out.append(f"... +{len(warnings) - 5} more warnings")

    if summary_parts:
        out.extend(summary_parts)

    res = "\n".join(out).strip()
    return res if res else input_str


def is_mostly_porcelain(head: str) -> bool:
    lines = [l.strip() for l in head.split("\n") if l.strip()]
    if len(lines) < 3:
        return False
    hits = sum(1 for l in lines if RE_PORCELAIN.match(l))
    return (hits / len(lines)) >= 0.6


def is_grep_line(line: str) -> bool:
    first = line.find(":")
    if first == -1:
        return False
    second = line.find(":", first + 1)
    if second == -1:
        return False
    lineno = line[first + 1:second]
    return lineno.isdigit()


def is_path_like(line: str) -> bool:
    t = line.strip()
    if not t or ":" in t:
        return False
    return t.startswith(".") or t.startswith("/") or "/" in t


def is_line_numbered(lines: List[str]) -> bool:
    hits = 0
    non_empty = 0
    sample = lines[:100]
    for l in sample:
        if not l:
            continue
        non_empty += 1
        if READ_NUMBERED_LINE_RE.match(l):
            hits += 1
    if non_empty < 5:
        return False
    return (hits / non_empty) >= READ_NUMBERED_MIN_HIT_RATIO


def auto_detect_filter(text: str) -> Optional[Tuple[Callable[[str], str], str]]:
    head = text[:DETECT_WINDOW]

    if RE_GIT_DIFF.search(head) or RE_GIT_DIFF_HUNK.search(head):
        return filter_git_diff, "git-diff"
    if RE_GIT_STATUS.search(head):
        return filter_git_status, "git-status"
    if RE_BUILD_OUTPUT.search(head):
        return filter_build_output, "build-output"
    if is_mostly_porcelain(head):
        return filter_git_status, "git-status"

    lines = head.split("\n")
    non_empty = [l for l in lines if l.strip()]

    first5 = non_empty[:5]
    if any(is_grep_line(l) for l in first5):
        return filter_grep, "grep"

    if len(non_empty) >= 3 and all(is_path_like(l) for l in non_empty):
        return filter_find, "find"

    if RE_TREE_GLYPH.search(head):
        return filter_smart_truncate, "tree"

    if RE_LS_TOTAL.search(head) or len(RE_LS_ROW.findall(head)) >= 3:
        return filter_smart_truncate, "ls"

    if SEARCH_LIST_HEADER_RE.search(head):
        return filter_smart_truncate, "search-list"

    if len(lines) >= SMART_TRUNCATE_MIN_LINES and is_line_numbered(lines):
        return filter_read_numbered, "read-numbered"

    if len(non_empty) >= 5:
        return filter_smart_truncate, "dedup-log"

    if len(text.split("\n")) >= SMART_TRUNCATE_MIN_LINES:
        return filter_smart_truncate, "smart-truncate"

    return None


def compress_text(text: str, stats: Dict[str, Any]) -> str:
    bytes_in = len(text)
    stats["bytes_before"] += bytes_in

    if bytes_in < MIN_COMPRESS_SIZE or bytes_in > RAW_CAP:
        stats["bytes_after"] += bytes_in
        return text

    detected = auto_detect_filter(text)
    if not detected:
        stats["bytes_after"] += bytes_in
        return text

    fn, filter_name = detected
    try:
        out = fn(text)
    except Exception:
        stats["bytes_after"] += bytes_in
        return text

    if not out or len(out) == 0 or len(out) >= bytes_in:
        stats["bytes_after"] += bytes_in
        return text

    stats["bytes_after"] += len(out)
    stats["hits"].append({
        "filter": filter_name,
        "saved": bytes_in - len(out)
    })
    return out


def compress_messages(body: Dict[str, Any], enabled: bool = True) -> Optional[Dict[str, Any]]:
    if not enabled or not body:
        return None

    messages = body.get("messages")
    if not messages or not isinstance(messages, list):
        return None

    stats = {"bytes_before": 0, "bytes_after": 0, "hits": []}

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        if role == "user" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                b_type = block.get("type")
                if b_type == "tool_result" and block.get("is_error") is not True:
                    text_content = block.get("content", "")
                    if isinstance(text_content, str):
                        block["content"] = compress_text(text_content, stats)
                    elif isinstance(text_content, list):
                        for part in text_content:
                            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                                part["text"] = compress_text(part["text"], stats)

    if stats["hits"]:
        saved = stats["bytes_before"] - stats["bytes_after"]
        pct = (saved / stats["bytes_before"] * 100) if stats["bytes_before"] > 0 else 0
        filters = ",".join(set(h["filter"] for h in stats["hits"]))
        from src.core.config_n_logg.logger import logger_proxy as logger
        logger.info(f"[RTK] saved {saved}B / {stats['bytes_before']}B ({pct:.1f}%) via [{filters}] hits={len(stats['hits'])}")

    return stats

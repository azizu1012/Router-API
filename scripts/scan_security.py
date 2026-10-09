"""Security audit for the HTTP surface — fails CI on auth and credential leaks.

``scan_secrets.py`` answers "is a credential committed?". This answers the two
questions that actually cost a self-hosted deployment its keys:

  1. Does this route check who is calling it?
  2. Does anything it hands back contain a credential?

Both are AST checks rather than greps, because the failure mode this exists to
prevent is silent: ``GET /api/stats`` returned HTTP 200 to an anonymous caller
and included every account's plaintext master key. Master keys are quota-exempt
by design, so one GET was a full privilege escalation. Nothing in the response
looked wrong, there was no error to grep for, and 879 tests were green.

The guards are resolved transitively. A handler that delegates to another
function is only as guarded as what it calls — ``generate_content`` does not call
``_check_auth`` itself, it calls ``_handle_gemini_native``, which does. Treating
that as unguarded would have produced six false positives and taught everyone to
ignore the tool.

Findings are reported, never auto-fixed: a rewrite is a change you did not
review, and which half of it was intended is not knowable from the regex.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent

# ── vocabulary ──────────────────────────────────────────────────────────────

# A call to any of these means "this code path decides who the caller is".
AUTH_GUARDS = frozenset({
    "_check_auth", "_require_dashboard", "_require_admin",
    "_resolve_auth", "_verify_session_token", "resolve_gemini_auth",
})

# Guards that also enforce a privilege tier.
ADMIN_GUARDS = frozenset({"_require_admin"})

# Endpoints that are meant to be reachable without credentials. Each one needs a
# reason; adding a path here is a decision, so it is written out rather than
# pattern-matched. Anything not listed and without a guard is a finding.
PUBLIC_ROUTES: dict[str, str] = {
    "/": "static frontend mount",
    "/health": "liveness probe for the process manager",
    "/mcp": "server banner / client bootstrap",
    "/stats": "serves index.html; all data behind it is fetched with a token",
    "/stats/{path:path}": "SPA route, same as /stats",
    "/dashboard/login": "you cannot present a token before you have one",
    "/dashboard/register": "gated by a single-use invite code instead",
}

# Paths whose payloads are account-wide rather than caller-scoped. A caller may
# read their own records with the weaker guard; reading everyone's needs admin.
# This list is deliberately a starting point, not the rule: several of these
# handlers do their own ``tier == "admin"`` branch, which is fine. So a hit here
# is only reported when neither _require_admin nor an in-handler tier check is
# present.
# Paths that hand back another account's records rather than the caller's own.
# Operational config (which model is degraded, how a pool is shaped) is not on
# this list: it describes shared infrastructure, not somebody else's usage.
ACCOUNT_WIDE_PREFIXES = ("/dashboard/admin/", "/dashboard/accounts",
                         "/dashboard/keys", "/api/stats", "/api/ping-model")

# Dict keys that carry a credential into a response body.
CREDENTIAL_KEYS = frozenset({
    "auth_key", "full_key", "password_hash", "password_salt",
    "password_enc", "api_key", "authKey",
})


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    file: str
    line: int
    where: str
    note: str


# ── analysis ────────────────────────────────────────────────────────────────

class ModuleIndex:
    """Every function defined under src/server, keyed by bare name."""

    def __init__(self, paths: Iterable[Path]) -> None:
        self.defs: dict[str, list[ast.stmt]] = {}
        self.src_by_def: dict[str, Path] = {}
        for path in paths:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.defs.setdefault(node.name, []).append(node)
                    self.src_by_def.setdefault(node.name, path)

    def callers_in(self, node: ast.AST) -> set[str]:
        return {
            n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }


def collect_routes(paths: Iterable[Path]) -> list[tuple[Path, ast.FunctionDef, str, str]]:
    """(file, handler, method, path) for every @app.<verb> route."""
    out = []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not dec.args:
                    continue
                f = dec.func
                if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                        and f.value.id == "app" and f.attr in {
                            "get", "post", "put", "patch", "delete", "websocket"}):
                    try:
                        rpath = ast.literal_eval(dec.args[0])
                    except (ValueError, SyntaxError):
                        rpath = "<dynamic>"
                    out.append((path, node, f.attr.upper(), str(rpath)))
    return out


def effective_guards(index: ModuleIndex, node: ast.AST, depth: int = 3) -> set[str]:
    """Guards reachable from this handler, following local delegation."""
    seen: set[str] = set()
    frontier = [(node, 0)]
    visited: set[int] = set()
    while frontier:
        cur, level = frontier.pop()
        if level > depth or id(cur) in visited:
            continue
        visited.add(id(cur))
        for name in index.callers_in(cur):
            if name in AUTH_GUARDS:
                seen.add(name)
                continue
            for target in index.defs.get(name, ()):
                frontier.append((target, level + 1))
    return seen


def credential_keys_in(index: ModuleIndex, node: ast.AST, depth: int = 3) -> set[str]:
    """Credential-ish dict keys reachable from a handler, following delegation."""
    found: set[str] = set()
    frontier = [(node, 0)]
    visited: set[int] = set()
    while frontier:
        cur, level = frontier.pop()
        if level > depth or id(cur) in visited:
            continue
        visited.add(id(cur))
        for sub in ast.walk(cur):
            if isinstance(sub, ast.Dict):
                for k in sub.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str) \
                            and k.value in CREDENTIAL_KEYS:
                        found.add(k.value)
            elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                for target in index.defs.get(sub.func.id, ()):
                    frontier.append((target, level + 1))
    return found


def has_tier_check(index: ModuleIndex, node: ast.AST, depth: int = 3) -> bool:
    """Does the handler branch on the caller's tier itself?

    ``/dashboard/accounts`` and friends call ``_require_dashboard`` and then
    split on ``payload.get("tier") == "admin"`` themselves, masking the fields a
    non-admin must not see. That is a legitimate shape; a path prefix is not
    enough to judge it.
    """
    frontier = [(node, 0)]
    visited: set[int] = set()
    while frontier:
        cur, level = frontier.pop()
        if level > depth or id(cur) in visited:
            continue
        visited.add(id(cur))
        for sub in ast.walk(cur):
            if isinstance(sub, ast.Compare):
                src = ast.unparse(sub)
                if "tier" in src and ("admin" in src or "free" in src
                                      or "premium" in src):
                    return True
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                for target in index.defs.get(sub.func.id, ()):
                    frontier.append((target, level + 1))
    return False


def _names_in(value: ast.AST) -> set[str]:
    """Names whose whole value flows into ``value``.

    A name used as the receiver of an attribute or a subscript is field
    selection, not disclosure: ``{"name": account.get("name")}`` ships the name
    and nothing else. Counting it would flag ``/dashboard/login``, which builds
    an internal ``{"auth_key": ...}`` record and returns three other fields.
    """
    skip: set[int] = set()
    for sub in ast.walk(value):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
            skip.add(id(sub.value))
        elif isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name):
            skip.add(id(sub.value))
    return {n.id for n in ast.walk(value)
            if isinstance(n, ast.Name) and id(n) not in skip}


def returned_credential_keys(index: ModuleIndex, node: ast.AST,
                             depth: int = 3) -> set[str]:
    """Credential dict keys that can reach the caller.

    A dictionary literal is not evidence on its own. ``/dashboard/login``
    builds ``{"auth_key": ...}`` as an internal record and returns only
    ``{"token", "name", "tier"}``, so flagging every literal produces a false
    positive and teaches people to ignore the tool.

    So the rule follows the shape the real bug had, which is two steps:

        rows.append({"full_key": a["auth_key"]})   # credential enters a container
        return {"rows": rows}                     # container leaves the function

    A literal counts when it is returned directly, appended to a list, or bound
    to a name; and when that name — or something assigned from it — appears in a
    return value. The login record above is bound to a name, but that name never
    reaches a return, so it stays quiet.
    """
    def visit(current: ast.AST, level: int) -> set[str]:
        if level > depth:
            return set()
        tainted: set[str] = set()
        returned: set[str] = set()
        aliases: dict[str, set[str]] = {}
        found: set[str] = set()

        for sub in ast.walk(current):
            # credential entering the picture
            if isinstance(sub, ast.Dict):
                hits = {k.value for k in sub.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                        and k.value in CREDENTIAL_KEYS}
                if not hits:
                    continue
                for parent in _parents(current, sub):
                    if isinstance(parent, ast.Return):
                        found |= hits
                    elif isinstance(parent, ast.Assign):
                        tainted |= _names_in(parent.targets[0])
                    elif (isinstance(parent, ast.Call)
                          and isinstance(parent.func, ast.Attribute)
                          and parent.func.attr in ("append", "extend", "add")):
                        tainted |= _names_in(parent.func.value)

            # what leaves the function
            if isinstance(sub, ast.Return) and sub.value is not None:
                returned |= _names_in(sub.value)

            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                for target in index.defs.get(sub.func.id, ()):
                    found |= visit(target, level + 1)

        # Rebinding is how the real handler shipped it: the enriched list is
        # built, assigned back to the original name, and that name is returned
        # further down. Resolving aliases after the walk rather than during it
        # keeps the answer independent of statement order.
        for sub in ast.walk(current):
            if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Name):
                for tgt in _names_in(sub.targets[0]):
                    aliases.setdefault(tgt, set()).add(sub.value.id)

        changed = True
        while changed:
            changed = False
            for name, sources in aliases.items():
                if name in returned:
                    for src in sources:
                        if src not in returned:
                            returned.add(src)
                            changed = True

        if tainted & returned:
            # A container that leaves the function carried a credential in.
            found |= {k.value for sub in ast.walk(current)
                      for k in (sub.keys if isinstance(sub, ast.Dict) else [])
                      if isinstance(k, ast.Constant) and isinstance(k.value, str)
                      and k.value in CREDENTIAL_KEYS}
        return found

    return visit(node, 0)


def _parents(root: ast.AST, target: ast.AST) -> list[ast.AST]:
    """Direct enclosing statements of ``target`` within ``root``."""
    out = []
    for parent in ast.walk(root):
        for child in ast.iter_child_nodes(parent):
            if child is target:
                out.append(parent)
    return out


def audit(paths: list[Path]) -> list[Finding]:
    index = ModuleIndex(paths)
    findings: list[Finding] = []

    for path, node, method, rpath in collect_routes(paths):
        rel = str(path.relative_to(ROOT)) if ROOT in path.parents else str(path)
        where = f"{method} {rpath}"
        guards = effective_guards(index, node)

        # R1 — reachable without credentials
        if not guards and rpath not in PUBLIC_ROUTES:
            findings.append(Finding(
                "unauthenticated-route", "critical", rel, node.lineno, where,
                "no auth guard is reachable from this handler, and the path is "
                "not in the documented public list"))

        # R2 — account-wide data behind a caller-scoped guard
        elif guards and ADMIN_GUARDS.isdisjoint(guards) \
                and rpath.startswith(ACCOUNT_WIDE_PREFIXES) \
                and not has_tier_check(index, node):
            findings.append(Finding(
                "weak-guard-for-account-wide-data", "high", rel, node.lineno, where,
                f"reads other accounts' data but only calls {sorted(guards)} and "
                "never branches on tier; add _require_admin, or an explicit tier "
                "check that strips what a non-admin must not see"))

# R3 — a credential on its way into a response body. A handler marked
        # with @reveals_credential is exempt, but only while it stays
        # admin-only: the marker exempts the shape, not the privilege, and
        # "show me the key" is still escalation if a free user can ask.
        marked = any(
            isinstance(d, ast.Name) and d.id == "reveals_credential"
            for d in node.decorator_list
        )
        exempt = marked and ADMIN_GUARDS.intersection(guards)
        if marked and not exempt:
            findings.append(Finding(
                "credential-reveal-without-admin-guard", "critical",
                rel, node.lineno, where,
                "marked @reveals_credential but only calls "
                f"{sorted(guards)}; returning a secret is not "
                "less sensitive per privilege level"))

        if not exempt:
            for key in sorted(returned_credential_keys(index, node)):
                findings.append(Finding(
                    "credential-in-payload", "critical", rel, node.lineno, where,
                    f"response carries '{key}'; a polled or screenshotted payload "
                    "should carry a masked value and a separate deliberate "
                    "reveal (see @reveals_credential)"))

    findings.extend(check_cors(paths))
    findings.extend(check_compare_digest(paths))
    return sorted(findings, key=lambda f: (f.severity, f.file, f.line))


def check_cors(paths: Iterable[Path]) -> list[Finding]:
    """Wildcard origin plus credentials lets any site ride the session."""
    out = []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            if not any(isinstance(v, ast.Constant) and v.value == "*"
                       for k, v in kw.items() if k in {"allow_origins", "origins"}):
                continue
            if not any(k == "allow_credentials" for k in kw):
                continue
            rel = str(path.relative_to(ROOT)) if ROOT in path.parents else str(path)
            out.append(Finding(
                "cors-wildcard-with-credentials", "high", rel,
                getattr(node, "lineno", 0), "CORS middleware",
                "allow_origins '*' together with allow_credentials lets any site "
                "make authenticated requests on a logged-in operator's behalf"))
    return out


def check_compare_digest(paths: Iterable[Path]) -> list[Finding]:
    """Credential equality must be constant-time."""
    out = []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            src = ast.unparse(node)
            if "==" not in src:
                continue
            if not any(tok in src.lower() for tok in
                       ("auth_key", "api_key", "password", "token", "secret")):
                continue
            if "compare_digest" in src:
                continue
            # Both sides constant means nothing is being guessed: `password ==
            # '1234'` asks "was the default left in place", and the caller
            # already controls their input. Only a comparison where one side is
            # a stored secret is worth reporting.
            operands = [n for n in ast.walk(node)
                        if isinstance(n, (ast.Name, ast.Attribute, ast.Subscript))]
            if len(operands) < 2:
                continue
            if all(isinstance(o, ast.Constant) for o in operands):
                continue
            if any(isinstance(o, ast.Constant) and isinstance(o.value, str)
                   and not o.value.isidentifier() for o in operands):
                continue
            # An ALL_CAPS name is a module constant, so `password ==
            # DEFAULT_PASSWORD` is still "was the default left in place".
            if any(isinstance(o, ast.Name) and o.id.isupper() for o in operands):
                continue
            rel = str(path.relative_to(ROOT)) if ROOT in path.parents else str(path)
            out.append(Finding(
                "credential-compared-with-equals", "medium", rel,
                node.lineno, src[:70],
                "a == b on a secret is timing-observable; use "
                "secrets.compare_digest(a.encode(), b.encode())"))
    return out


# ── reporting ───────────────────────────────────────────────────────────────

def render(findings: list[Finding]) -> str:
    if not findings:
        return "no security findings"
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    lines = [f"{len(findings)} finding(s)"]
    for f in sorted(findings, key=lambda x: (order.get(x.severity, 9), x.file, x.line)):
        lines.append(f"\n  [{f.severity.upper()}] {f.rule}")
        lines.append(f"    {f.file}:{f.line}  {f.where}")
        for chunk in _wrap(f.note, 66):
            lines.append(f"      {chunk}")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def server_files() -> list[Path]:
    return sorted((ROOT / "src" / "server").glob("**/*.py"))


def tracked_files() -> list[Path]:
    try:
        names = subprocess.run(["git", "ls-files", "src/server"],
                               cwd=ROOT, capture_output=True, text=True,
                               check=True).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return server_files()
    return [ROOT / n for n in names if n.endswith(".py") and (ROOT / n).exists()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Audit the HTTP surface for auth and credential leaks")
    ap.add_argument("--git", action="store_true",
                    help="only scan files git tracks (use in CI)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--warn-only", action="store_true",
                    help="report but exit 0")
    args = ap.parse_args(argv)

    paths = tracked_files() if args.git else server_files()
    findings = audit(paths)

    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        print(render(findings))

    if args.warn_only:
        return 0
    return 1 if any(f.severity in ("critical", "high") for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
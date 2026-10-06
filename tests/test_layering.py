"""Layering: the bottom may not import upward.

Three helpers sat in the wrong module, so a lower layer had to reach past the
layer above it to get them — `gemini_format` (providers) importing
`message_converter` (translator) for a function that exists only to satisfy
google-genai, and `auth.py` importing `sse_cache_agent` for an SSE frame
formatter. Nothing was broken; every call returned the right answer. That is
exactly why it survived, and why it needs a mechanical check rather than a note
in a document nobody re-reads.

Checks run over module-level imports only. There are 252 function-body imports
across the repo and they are deliberate house style, so parsing them as
violations would be noise, not signal.
"""
import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tracked_python_files():
    out = subprocess.run(
        ['git', 'ls-files', '*.py'], capture_output=True, text=True, cwd=REPO_ROOT
    )
    return [REPO_ROOT / p for p in out.stdout.split()]


def _module_level_imports(path: Path):
    """Absolute src.* modules imported at module scope.

    Function-body imports are deliberately excluded: they do not run at import
    time, so they cannot make importing the module expensive or cyclic on load.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    found = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('src.'):
            found.append(node.module)
    return found


# Each entry: (prefix that must not appear, modules allowed to, reason)
RULES = [
    (
        'src/core/providers/',
        ('src.logical_HQ_translator', 'src.api'),
        'providers is the layer that talks to the SDK. A Gemini-specific rule or a '
        'client-protocol helper belongs beside its own layer, not one the provider '
        'imports upward to borrow.',
    ),
    (
        'src/logical_HQ_translator/',
        ('src.api',),
        'translator is a peer of src/core, not a layer below it. It cannot reach into '
        'the proxy layer; shared helpers move to src/core/ as pure functions.',
    ),
    (
        'src/core/',
        ('src.server', 'src.api'),
        'core is below transport and protocol. Transport may depend on core, never '
        'the reverse.',
    ),
]

# Documented debt, not a licence. Each entry is (file, prefix, why it is tolerated).
KNOWN_EXCEPTIONS = [
    (
        'src/core/providers/custom_endpoint_manager.py',
        'src.server.websocket_manager',
        'needs to broadcast endpoint health to the live dashboard. The dependency runs '
        'the wrong way; the fix is an event sink in core that server subscribes to, '
        'which is a larger change than this refactor.',
    ),
]


def _is_exception(rel_path, module):
    return any(
        rel_path.replace('\\', '/') == file and module.startswith(prefix)
        for file, prefix, _ in KNOWN_EXCEPTIONS
    )


def _violations():
    found = []
    for path in _tracked_python_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for prefix, forbidden, reason in RULES:
            if not rel.startswith(prefix):
                continue
            for module in _module_level_imports(path):
                bad = [f for f in forbidden if module == f or module.startswith(f + '.')]
                if not bad:
                    continue
                if _is_exception(rel, module):
                    continue
                found.append((rel, module, reason))
    return found


class TestLayering:
    def test_no_module_imports_upward(self):
        bad = _violations()
        if not bad:
            return
        lines = ['Layering violated — lower layer importing upward:', '']
        for rel, module, reason in sorted(bad):
            lines.append(f'  {rel} -> {module}')
        lines += ['', f'First rule that applies: {bad[0][2]}']
        lines.append(
            'If a helper genuinely belongs to neither side, move it to src/core/ as a '
            'pure function and import it from there.'
        )
        raise AssertionError('\n'.join(lines))

    def test_the_checker_actually_detects_a_violation(self):
        """A guard that cannot fail is decoration.

        Feed it a file shaped exactly like the bug this test was written for and
        confirm it gets reported.
        """
        fake = 'from src.logical_HQ_translator.message_converter import _sanitize_schema_for_gemini\n'
        tree = ast.parse(fake)
        modules = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('src.'):
                modules.append(node.module)

        assert modules == ['src.logical_HQ_translator.message_converter'], modules
        banned = ('src.logical_HQ_translator', 'src.api')
        assert any(m.startswith(banned[0]) for m in modules), 'checker missed the import'

    def test_function_body_imports_are_not_treated_as_violations(self):
        """The 252 lazy imports are house style; scanning them would be noise."""
        lazy = 'def f():\n    from src.logical_HQ_translator.message_converter import x\n'
        tree = ast.parse(lazy)
        found = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('src.'):
                found.append(node.module)
        assert found == [], 'function-body imports must not be scanned'

    def test_every_exception_has_a_stated_reason(self):
        for _, _, why in KNOWN_EXCEPTIONS:
            assert len(why) > 40, 'an exception without a reason is just a hole'
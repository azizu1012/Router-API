"""Sanity tests for the secret scanner.

The scanner's value depends entirely on not crying wolf: a CI gate that fires on
every commit gets ignored, and then it protects nothing. These tests pin both
directions — real secrets are caught, and ordinary source is not.

Run: pytest tests/test_secret_scanner.py -v
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

# The scanner lives in scripts/ rather than the package because the CI workflow
# invokes it as a standalone script. Loading it by path keeps that arrangement
# and still gives the language server a symbol it can resolve — a bare
# sys.path.insert plus `from scan_secrets import ...` is invisible to static
# analysis and shows up as an unresolved-import error.
_SCAN_PATH = Path(__file__).resolve().parent.parent / "scripts" / "scan_secrets.py"
_spec = importlib.util.spec_from_file_location("scan_secrets", _SCAN_PATH)
assert _spec is not None and _spec.loader is not None, f"cannot load {_SCAN_PATH}"
scan_secrets = importlib.util.module_from_spec(_spec)
# Must be registered before exec_module: @dataclass resolves its own module via
# sys.modules[cls.__module__], and a module built by hand is not there yet.
sys.modules[_spec.name] = scan_secrets
_spec.loader.exec_module(scan_secrets)
scan_text = scan_secrets.scan_text


def rules_found(text: str) -> set[str]:
    return {f.rule for f in scan_text(text, "probe")}


# ── must catch ──────────────────────────────────────────────────────────────

class TestDetectsRealSecrets:
    def test_google_api_key(self):
        key = "AIza" + "B" * 33
        assert "google-api-key" in rules_found(f'GOOGLE_KEY = "{key}"')

    def test_github_pat(self):
        assert "github-token" in rules_found('tok = "ghp_' + "a1b2c3d4e5" * 4 + '"')

    def test_github_fine_grained(self):
        pat = "github_pat_" + "A" * 22 + "_" + "B" * 22
        assert "github-fine-grained-token" in rules_found(f'p = "{pat}"')

    def test_aws_key(self):
        assert "aws-access-key" in rules_found('k = "AKIAIOSFODNN7EXAMPLE"')

    def test_slack_token(self):
        assert "slack-token" in rules_found('s = "xoxb-1234567890-abcdefghij"')

    def test_private_key_block(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
        assert "private-key-block" in rules_found(pem)

    def test_jwt(self):
        jwt = ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
               ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")
        assert "jwt" in rules_found(f'HEADER = "Bearer {jwt}"')

    @pytest.mark.parametrize("dsn", [
        "postgresql://admin:s3cr3t@db.example.com:5432/app",
        "mysql://root:password@localhost/app",
        "mongodb+srv://u:p@cluster0.example.mongodb.net/db",
        "redis://:password@127.0.0.1:6379/0",
    ])
    def test_connection_strings(self, dsn):
        assert "db-uri-with-password" in rules_found(f'URL = "{dsn}"')

    def test_router_token_named(self):
        # the real wire format: sk-<name>-<6 code>, padded to look real
        assert "router-token" in rules_found('TOKEN = "sk-coder-7Kq2mW9xT4bN"')

    def test_router_token_legacy(self):
        assert "router-token-legacy" in rules_found('KEY = "sk-' + "A" * 43 + '"')

    def test_generic_password_assignment(self):
        assert "credential-assignment" in rules_found(
            'password = "Tr0ub4dor-and-3xyz-long"'
        )


# ── must stay quiet ─────────────────────────────────────────────────────────

class TestNoFalsePositives:
    @pytest.mark.parametrize("name", [
        "google-api-key", "github-token", "aws-access-key", "slack-token",
        "private-key-block", "jwt", "db-uri-with-password",
        "router-token", "router-token-legacy", "credential-assignment",
    ])
    def test_clean_source_is_silent(self, name):
        source = "\n".join([
            "import os",
            "def handler(request):",
            "    payload = request.json()",
            "    return {'status': 'ok', 'items': [1, 2, 3]}",
        ])
        assert name not in rules_found(source)

    def test_allowlisted_placeholders(self):
        text = (
            'GEMINI_API_KEY_1=your_gemini_key_1\n'
            'API_KEY=changeme\n'
            'password=dummy\n'
            'secret=REDACTED\n'
            'token=placeholder\n'
            'api_key=example\n'
        )
        assert scan_text(text, "env.example") == []

    def test_our_own_redacted_marker(self):
        assert scan_text('key = "sk-REDACTED-LEAKED-KEY"', "t.py") == []

    def test_docs_token_examples(self):
        # docs legitimately show the shape; short codes must not trip the gate
        text = 'parse_token("sk-azure-yena-abc123")  # -> ("azure-yena", "abc123")'
        assert "router-token" not in rules_found(text)

    def test_test_suite_bogus_tokens(self):
        text = 'for bogus in ("sk-anything-zzzzzz", "sk-totally-made-up-999999"):'
        assert scan_text(text, "t.py") == []

    def test_env_example_is_clean(self):
        repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        with open(os.path.join(repo, ".env.example"), encoding="utf-8") as fh:
            assert scan_text(fh.read(), ".env.example") == []

    def test_previews_never_contain_the_secret(self):
        """The report must locate a value without reprinting it."""
        key = "AIza" + "S" * 33
        findings = scan_text(f'KEY = "{key}"', "c.py")
        assert findings
        for f in findings:
            assert key not in f.preview
            assert "S" * 33 not in f.preview
            assert f"(len={len(key)})" in f.preview


# ── reporting ───────────────────────────────────────────────────────────────

class TestReporting:
    def test_clean_tree_reports_nothing(self):
        assert scan_text("x = 1\n", "a.py") == []

    def test_severity_is_carried(self):
        findings = scan_text('k = "AKIAIOSFODNN7EXAMPLE"', "a.py")
        crit = [f for f in findings if f.severity == "critical"]
        assert crit

    def test_line_numbers_are_reported(self):
        text = "line1\nline2\nKEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        findings = scan_text(text, "a.py")
        assert any(f.line == 3 for f in findings)

    def test_deduplicates_same_rule_same_line(self):
        text = 'KEY = "AKIAIOSFODNN7EXAMPLE AKIAIOSFODNN7EXAMPL2"'
        findings = [f for f in scan_text(text, "a.py") if f.rule == "aws-access-key"]
        assert len(findings) == 1
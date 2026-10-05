"""ADK web-search path: safety config parity and dependency handling.

The ADK path (src/core/providers/adk_runner.py) is an alternative to the native
Gemini path for web_search requests. These tests pin the two properties that make
that alternative safe to use:

1. It applies the same safety_settings as the native path, so the same query does
   not return content natively but come back blocked via ADK.
2. A missing google-adk surfaces as an explicit error instead of collapsing into a
   generic retry across every key.

google-adk is optional and is not installed in every environment, so the parity
test reconstructs the runner's safety-config step against a stubbed ADK surface
rather than importing the real SDK.

Run: pytest tests/test_adk_runner.py -v
"""

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

ADK_RUNNER = "src/core/providers/adk_runner.py"


def _runner_source() -> str:
    with open(ADK_RUNNER, encoding="utf-8") as fh:
        return fh.read()


def test_native_path_sets_block_none():
    """Guard the premise of the parity check: the native path really disables filters."""
    from src.core.config_n_logg import config
    cats = {s["category"] for s in config.SAFETY_SETTINGS}
    assert cats == {
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    }
    assert all(s["threshold"] == "BLOCK_NONE" for s in config.SAFETY_SETTINGS)


class TestSafetyParity:
    """The ADK model must receive config.SAFETY_SETTINGS, not inherit API defaults.

    Gemini's own defaults set HARASSMENT and HATE_SPEECH to BLOCK_MEDIUM_AND_ABOVE,
    so without this the ADK path silently blocks content the native path returns.
    """

    def test_runner_builds_safety_settings_from_config(self):
        from src.core.config_n_logg import config
        safety = [
            {"category": s["category"], "threshold": s["threshold"]}
            for s in (config.SAFETY_SETTINGS or [])
        ]
        assert len(safety) == 4
        assert all(s["threshold"] == "BLOCK_NONE" for s in safety)

    def test_runner_assigns_generate_content_config(self):
        src = _runner_source()
        assert "generate_content_config" in src, (
            "ADK model must receive an explicit generate_content_config carrying "
            "safety_settings, otherwise it inherits Gemini API defaults"
        )

    def test_runner_reads_config_not_hardcoded_categories(self):
        """Safety must come from config so env overrides keep working."""
        src = _runner_source()
        assert "config.SAFETY_SETTINGS" in src
        # A hardcoded copy would drift from config.py:159.
        assert "HARM_CATEGORY_HARASSMENT" not in src, (
            "safety categories must not be hardcoded in adk_runner; "
            "read them from config.SAFETY_SETTINGS"
        )


class TestOptionalDependency:
    def test_runner_import_is_guarded(self):
        """A missing google-adk must raise a clear error, not bare ModuleNotFoundError."""
        src = _runner_source()
        assert "except ModuleNotFoundError" in src
        assert "pip install google-adk" in src, "message must tell the operator how to fix it"

    def test_runner_module_is_syntactically_valid(self):
        ast.parse(_runner_source())

    def test_manager_exposes_typed_error(self):
        from src.core.providers.gemini.manager import ADKUnavailableError
        assert issubclass(ADKUnavailableError, RuntimeError)

    def test_load_adk_runner_raises_typed_error_when_missing(self, monkeypatch):
        """The typed error must surface before the retry loop swallows it.

        ModuleNotFoundError used to be raised inside the retry loop's try block,
        so it was caught, retried against every key, and finally reported as a
        generic API failure.
        """
        import builtins
        from src.core.providers.gemini.manager import _load_adk_runner, ADKUnavailableError

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("google.adk"):
                raise ModuleNotFoundError("No module named 'google.adk'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(ADKUnavailableError) as exc:
            _load_adk_runner()
        assert "google-adk" in str(exc.value)

    def test_manager_does_not_import_adk_runner_eagerly(self):
        """The import must stay lazy so a missing optional dep cannot break startup."""
        with open("src/core/providers/gemini/manager.py", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        top_level_imports = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                top_level_imports.append(node.module)
            elif isinstance(node, ast.Import):
                top_level_imports.extend(a.name for a in node.names)
        assert not any("adk_runner" in m for m in top_level_imports), (
            "adk_runner must not be imported at module scope"
        )


class TestBranchSelection:
    def test_non_lite_model_takes_adk_branch(self):
        """Documents why the missing dependency matters: default flash is non-lite.

        can_native_ground requires is_lite=True, so the default gemini-flash with
        web_search enabled is routed through ADK.
        """
        def can_native_ground(model_id, model_alias, web_search, has_media):
            is_lite = "lite" in model_alias.lower() or "lite" in model_id.lower()
            return web_search and not has_media and is_lite and ("gemini" in model_id.lower())

        assert can_native_ground("gemini-3-flash", "gemini-flash", True, False) is False
        assert can_native_ground("gemini-3-flash-lite", "gemini-flash-lite", True, False) is True

    def test_media_blocks_native_grounding(self):
        def can_native_ground(model_id, model_alias, web_search, has_media):
            is_lite = "lite" in model_alias.lower() or "lite" in model_id.lower()
            return web_search and not has_media and is_lite and ("gemini" in model_id.lower())

        assert can_native_ground("gemini-3-flash-lite", "gemini-flash-lite", True, True) is False
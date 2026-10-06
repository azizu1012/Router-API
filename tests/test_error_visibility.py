"""Two error handlers that failed without a trace.

Neither produced a wrong response. They produced *no* response you could
diagnose: a missing log line, and exception types too broad to notice. Both are
pinned below because both are invisible from the outside — the only way to tell
the fixed version from the broken one is to look for the log call or read the
handler's exception tuple.

    router.list_models  swallowed a failure while appending custom endpoint
                        models, so every custom model vanished from /v1/models
                        with nothing in any log.

    key_status          parsed and merged a JSON per_model column inside bare
                        except Exception handlers, so a genuine bug in those
                        loops looked exactly like a corrupt blob.

There were two such handlers, not one: the parse at the top of the row loop and
the merge further down. Both are narrowed now, and the test walks the AST of the
function to keep any new catch-all from being added back.
"""

import ast
import inspect
import json
import sqlite3
import textwrap
import time
from unittest.mock import patch

import pytest

from src.backend import key_status as ks
from src.core.router.core import router as router_singleton
import sys as _sys

# src.core.router.core.__init__ rebinds the attribute `router` to the singleton,
# so `import src.core.router.core.router as m` hands back the instance, not the
# module. The module object is only reachable through sys.modules.
router_module = _sys.modules["src.core.router.core.router"]

KEY_STATUS_DDL = """
CREATE TABLE key_status (
  key TEXT PRIMARY KEY,
  enabled INTEGER DEFAULT 1,
  usage INTEGER DEFAULT 0,
  active_requests INTEGER DEFAULT 0,
  frozen_until REAL DEFAULT 0,
  consecutive_failures INTEGER DEFAULT 0,
  last_success REAL DEFAULT 0,
  date TEXT DEFAULT '',
  today INTEGER DEFAULT 0,
  per_model TEXT DEFAULT '{}',
  data TEXT,
  tier TEXT DEFAULT 'free'
)
"""


def _conn_with(rows):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(KEY_STATUS_DDL)
    for values in rows:
        c.execute(
            "INSERT INTO key_status (key, per_model, data) VALUES (?, ?, ?)", values
        )
    c.commit()
    return c


class _LogSpy:
    def __init__(self):
        self.errors = []

    def error(self, msg, *args):
        self.errors.append(msg % args if args else msg)

    def __getattr__(self, _name):
        return lambda *a, **k: None


class TestCustomEndpointModelsAreNotSilentlyDropped:
    def _boom(self):
        raise RuntimeError("endpoints table locked")

    def test_a_failing_endpoint_listing_is_logged(self):
        spy = _LogSpy()
        with patch.object(router_module, "logger", spy), \
             patch("src.core.providers._custom_endpoint_manager.list_endpoints",
                   self._boom):
            router_singleton.list_models()

        assert spy.errors, "list_models swallowed the failure without logging it"
        assert "endpoints table locked" in spy.errors[0], spy.errors

    def test_the_gemini_models_are_still_returned_when_endpoints_fail(self):
        """Logging is the fix. The Gemini half must keep working regardless."""
        spy = _LogSpy()
        with patch.object(router_module, "logger", spy), \
             patch("src.core.providers._custom_endpoint_manager.list_endpoints",
                   self._boom):
            models = router_singleton.list_models()

        assert isinstance(models, list)
        assert models, "the Gemini model list should survive an endpoint failure"

    def test_a_healthy_endpoint_listing_logs_nothing(self):
        spy = _LogSpy()
        endpoints = [{"name": "ep", "enabled": True, "enabled_models": ["ep-model"]}]
        with patch.object(router_module, "logger", spy), \
             patch("src.core.providers._custom_endpoint_manager.list_endpoints",
                   lambda: endpoints):
            models = router_singleton.list_models()

        assert not spy.errors, f"logged on a healthy path: {spy.errors}"
        assert any(m["id"] == "ep-model" for m in models), \
            "the custom model should be present when nothing failed"


class TestPerModelHandlersOnlySwallowBadData:
    """A corrupt column is recoverable. An AttributeError is not."""

    def test_a_corrupt_per_model_blob_yields_empty_state(self):
        c = _conn_with([("k1", "not json at all", None)])
        with patch.object(ks, "_conn", lambda: c):
            rows = ks.get_key_status_db()

        assert "k1" in rows, rows
        assert rows["k1"]["per_model"] == {}, rows["k1"]
        assert rows["k1"]["consecutive_failures"] == 0, rows["k1"]

    def test_a_non_text_per_model_column_yields_empty_state(self):
        c = _conn_with([("k2", None, None)])
        c.execute("UPDATE key_status SET per_model = X'00FF' WHERE key = 'k2'")
        c.commit()
        with patch.object(ks, "_conn", lambda: c):
            rows = ks.get_key_status_db()

        assert "k2" in rows, rows
        assert rows["k2"]["per_model"] == {}, rows["k2"]

    def test_a_valid_per_model_blob_is_parsed(self):
        # frozen_until has to sit in the future: the row loop zeroes failures that
        # have been idle past the 300s auto-reset, which would hide a parse bug
        # behind a reset that looks identical to success.
        blob = json.dumps({
            "gemini-flash": {"failures": 2, "frozen_until": time.time() + 3600}
        })
        c = _conn_with([("k3", blob, None)])
        with patch.object(ks, "_conn", lambda: c):
            rows = ks.get_key_status_db()

        assert rows["k3"]["per_model"]["gemini-flash"]["failures"] == 2, \
            rows["k3"]["per_model"]

    def test_the_data_merge_narrows_to_data_errors(self):
        """The merge only fills in keys the row does not already carry."""
        c = _conn_with([("k4", "{}", json.dumps({"extra_state": "kept"}))])
        with patch.object(ks, "_conn", lambda: c):
            rows = ks.get_key_status_db()

        assert rows["k4"].get("extra_state") == "kept", rows["k4"]

    def test_no_catch_all_handler_remains_in_the_row_loop(self):
        src = textwrap.dedent(inspect.getsource(ks.get_key_status_db))
        tree = ast.parse(src)

        broad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if node.type is None:
                broad.append(node.lineno)
            elif isinstance(node.type, ast.Name) and node.type.id in ('Exception', 'BaseException'):
                broad.append(node.lineno)
            elif isinstance(node.type, ast.Tuple):
                for e in node.type.elts:
                    if isinstance(e, ast.Name) and e.id in ('Exception', 'BaseException'):
                        broad.append(node.lineno)

        assert not broad, (
            f"get_key_status_db has a catch-all handler at lines {broad}; it makes a "
            "programming error look like corrupt data"
        )


@pytest.mark.parametrize("blob", ["not json", "[1,2,3]", "", "\x00\xff"])
def test_garbage_never_raises(blob):
    """Whatever the column holds, the reader must not take the process down."""
    c = _conn_with([("kg", blob, None)])
    with patch.object(ks, "_conn", lambda: c):
        rows = ks.get_key_status_db()
    assert "kg" in rows

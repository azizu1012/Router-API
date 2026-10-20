"""Regression test: _resolve_and_call must receive request_id from callers.

Bug: _resolve_and_call referenced `request_id` in its body but did not
declare it as a parameter. When quota_exhausted was raised inside
_resolve_and_call, the NameError crashed the request instead of logging
the failure and retrying with a different key.

CI missed it because no test exercised the quota-exhausted path inside
_resolve_and_call.
"""

import ast
import inspect
import textwrap

from src.core.orchestration.pool_manager import PoolManager


def test_resolve_and_call_has_request_id_param():
    """_resolve_and_call must accept request_id so callers can propagate it."""
    sig = inspect.signature(PoolManager._resolve_and_call)
    assert "request_id" in sig.parameters, (
        "_resolve_and_call is missing 'request_id' parameter — "
        "any reference to request_id inside its body will raise NameError"
    )


def test_callers_pass_request_id():
    """Every call to _resolve_and_call must include request_id=request_id."""
    source = inspect.getsource(PoolManager)
    tree = ast.parse(textwrap.dedent(source))

    calls_missing_request_id = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match self._resolve_and_call(...)
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "_resolve_and_call"):
            continue
        kwarg_names = {kw.arg for kw in node.keywords}
        if "request_id" not in kwarg_names:
            calls_missing_request_id.append(node.lineno)

    assert not calls_missing_request_id, (
        f"_resolve_and_call called without request_id= at source lines: "
        f"{calls_missing_request_id}"
    )


def test_standalone_stream_path_label():
    """The standalone stream error path must log 'standalone_stream', not 'standalone_nonstream'."""
    source = inspect.getsource(PoolManager.call_stream)
    # In call_stream's standalone branch, record_attempt_failure should say standalone_stream
    assert '"standalone_nonstream"' not in source, (
        "call_stream standalone path logs 'standalone_nonstream' — "
        "should be 'standalone_stream'"
    )

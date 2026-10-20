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

import pytest

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


def test_safe_release_key_fallback():
    """_safe_release_key must support both modern 3-arg and legacy 1-arg signatures."""
    from src.core.orchestration.pool_manager import _safe_release_key
    import src.core.orchestration.pool_manager as pm_module

    # Test modern 3-arg call
    called_modern = []
    class ModernRouter:
        def release_key(self, key, res_id=None, metadata=None):
            called_modern.append((key, res_id, metadata))
            return True

    orig_router = pm_module.router
    try:
        pm_module.router = ModernRouter()
        res = _safe_release_key("key-123", "res-456", {"req": "test"})
        assert res is True
        assert called_modern == [("key-123", "res-456", {"req": "test"})]

        # Test legacy 1-arg mock
        called_legacy = []
        class LegacyRouter:
            def release_key(self, key):
                called_legacy.append(key)
                return True

        pm_module.router = LegacyRouter()
        res_legacy = _safe_release_key("key-123", "res-456", {"req": "test"})
        assert res_legacy is True
        assert called_legacy == ["key-123"]
    finally:
        pm_module.router = orig_router


@pytest.mark.anyio
async def test_stream_releases_key_on_quota_exhausted(monkeypatch):
    """When acquire_quota fails in call_stream, the key MUST be released to prevent capacity leaks."""
    import asyncio
    import src.core.orchestration.pool_manager as pool_module

    pm = pool_module.PoolManager()
    monkeypatch.setattr(pool_module.router, "resolve_pool", lambda alias: None)
    monkeypatch.setattr(
        pool_module,
        "_resolve_model",
        lambda *args, **kwargs: asyncio.sleep(
            0,
            result=(
                "gemini-flash",
                "gemini-flash-38",
                "test-leaked-key",
                "gemini-flash-38",
                {"provider": "gemini", "model_alias": "gemini-flash", "reservation_id": "res-999"},
            ),
        ),
    )
    # Simulate quota exhausted
    monkeypatch.setattr(pool_module.router, "acquire_quota", lambda *args: asyncio.sleep(0, result=False))

    released_keys = []
    monkeypatch.setattr(
        pool_module.router,
        "release_key",
        lambda key, *args, **kwargs: released_keys.append(key) or True,
    )
    monkeypatch.setattr(pool_module, "apply_error_penalty", lambda *args: None)
    monkeypatch.setattr(pool_module.router, "record_attempt_failure", lambda *args, **kwargs: None)
    monkeypatch.setattr(pool_module, "_retry_delay", lambda attempt: 0)

    with pytest.raises(RuntimeError, match="Standalone stream failed after retries"):
        async for _ in pm.call_stream("gemini-flash", [{"role": "user", "content": "hi"}]):
            pass

    # Across MAX_RETRIES attempts, the key MUST have been released each time!
    assert len(released_keys) == pool_module.config.MAX_RETRIES
    assert all(k == "test-leaked-key" for k in released_keys)


@pytest.mark.anyio
async def test_nonstream_standalone_unbound_reservation_protection(monkeypatch):
    """If _resolve_and_call raises early in standalone nonstream, reservation must not be unbound."""
    import asyncio
    import src.core.orchestration.pool_manager as pool_module

    pm = pool_module.PoolManager()
    monkeypatch.setattr(pool_module.router, "resolve_pool", lambda alias: None)

    async def fail_resolve(*args, **kwargs):
        raise ValueError("simulated network crash before reservation")

    monkeypatch.setattr(pm, "_resolve_and_call", fail_resolve)
    monkeypatch.setattr(pool_module, "_classify_error", lambda e: "network_error")
    monkeypatch.setattr(pool_module, "_retry_delay", lambda attempt: 0)

    # Should raise ValueError, NOT UnboundLocalError
    with pytest.raises(ValueError, match="simulated network crash before reservation"):
        await pm.call_nonstream("gemini-flash", [{"role": "user", "content": "hi"}])


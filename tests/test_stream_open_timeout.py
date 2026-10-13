"""A hung provider stream-open must not inherit the long request timeout."""

import asyncio
import types

import pytest

from src.core.config_n_logg import config
from src.core.providers.gemini import facade


class _BlockingModels:
    async def generate_content_stream(self, **_kwargs):
        await asyncio.Event().wait()


class _Pool:
    async def throttle(self, *_args):
        return None

    def get_key_last_used(self, _api_key):
        return 0.0

    def record_key_usage(self, _api_key):
        return None

    async def get_client(self, _api_key):
        return types.SimpleNamespace(
            aio=types.SimpleNamespace(models=_BlockingModels())
        )


@pytest.mark.anyio
async def test_stream_open_timeout_is_separate_from_request_timeout(monkeypatch):
    import src.core.providers.gemini.manager as manager

    fake_manager = types.SimpleNamespace(pool=_Pool())
    monkeypatch.setattr(manager, "api_manager", fake_manager)
    monkeypatch.setattr(config, "REQUEST_TIMEOUT_SECONDS", 600)
    monkeypatch.setattr(config, "STREAM_OPEN_TIMEOUT_SECONDS", 0.01)

    stream = facade._sdk_stream("key", "model", [], {})
    with pytest.raises(TimeoutError, match="gemini_stream_open_timeout"):
        await stream.__anext__()

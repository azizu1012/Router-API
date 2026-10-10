"""The admin picks the format; the router has to then act on that choice in
every place it touches the endpoint, not just the one that sends the request.

fetch_models probes /chat/completions to decide whether an endpoint is alive.
An Anthropic-compatible endpoint has no such path, so without the format the
probe reports a failure for a perfectly good endpoint.
"""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.providers.custom_endpoint_manager import CustomEndpointManager
from src.core.providers.custom_endpoint_client import anthropic_headers, anthropic_messages_url


@pytest.mark.anyio
class TestTheProbeAsksTheEndpointTheRightQuestion:
    @staticmethod
    def _patched():
        seen = {}

        class _Resp:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class _Session:
            def __init__(self):
                self.headers = {}

            def post(self, url, json=None, timeout=None):
                seen["url"] = url
                seen["body"] = json
                seen["headers"] = self.headers
                return _Resp()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def close(self):
                pass

        def factory(headers=None):
            s = _Session()
            s.headers = headers or {}
            return s

        return patch("aiohttp.ClientSession", factory), seen

    @pytest.mark.parametrize("fmt,expected", [
        ("openai", "https://ep.example/v1/chat/completions"),
        ("anthropic", "https://ep.example/v1/messages"),
        (None, "https://ep.example/v1/chat/completions"),
        ("nonsense", "https://ep.example/v1/chat/completions"),
    ])
    async def test_probe_url_follows_the_declared_format(self, fmt, expected):
        patcher, seen = self._patched()
        with patcher:
            ok = await CustomEndpointManager()._probe_chat_endpoint(
                "https://ep.example/v1", "key", fmt)
        assert ok is True
        assert seen["url"] == expected

    async def test_an_anthropic_probe_sends_the_anthropic_headers(self):
        patcher, seen = self._patched()
        with patcher:
            await CustomEndpointManager()._probe_chat_endpoint(
                "https://ep.example", "sk-secret", "anthropic")
        headers = seen["headers"]
        assert headers["x-api-key"] == "sk-secret"
        assert headers["anthropic-version"] == "2023-06-01"
        # max_tokens is required by Anthropic; omitting it is a 400.
        assert seen["body"]["max_tokens"] == 1


class TestTheAddPathCarriesTheFormat:
    def test_add_defaults_to_openai(self):
        with patch("src.core.providers.custom_endpoint_manager.add_endpoint_db") as add, \
             patch("src.core.providers.custom_endpoint_manager.update_endpoint_db") as upd:
            add.return_value = {"name": "x"}
            CustomEndpointManager().add("x", "https://e", "k")
            assert add.call_args.kwargs.get("api_format", "openai") == "openai"
            assert not upd.called

    def test_add_persists_a_non_default_format(self):
        with patch("src.core.providers.custom_endpoint_manager.add_endpoint_db") as add, \
             patch("src.core.providers.custom_endpoint_manager.update_endpoint_db") as upd:
            add.return_value = {"name": "x"}
            upd.return_value = {"name": "x", "api_format": "anthropic"}
            out = CustomEndpointManager().add("x", "https://e", "k",
                                              api_format="anthropic")
            assert upd.call_args.kwargs["api_format"] == "anthropic"
            assert out["api_format"] == "anthropic"


class TestTheSharedHelpers:
    def test_headers_carry_the_key_both_ways(self):
        """Anthropic reads x-api-key; a lot of proxies read Authorization."""
        h = anthropic_headers("sk-abc")
        assert h["x-api-key"] == "sk-abc"
        assert h["Authorization"] == "Bearer sk-abc"
        assert h["anthropic-version"] == "2023-06-01"
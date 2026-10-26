"""Nine-router lessons: provider context fallback + length-cut retry."""
import types

from src.logical_HQ_translator.model.alias import _provider_context_fallback
from src.api.claude_proxy.handler.proxy_nonstream import _text_was_cut_off


def test_provider_fallback_muse_is_1m():
    assert _provider_context_fallback("muse-1.3") == 1000000
    assert _provider_context_fallback("Muse-5.3") == 1000000


def test_provider_fallback_gpt5_grok_llama():
    assert _provider_context_fallback("cx/gpt-5.5") == 1000000
    assert _provider_context_fallback("grok-4") == 256000
    assert _provider_context_fallback("llama-3-70b") == 128000



def test_provider_fallback_kimi_family():
    # 9router v0.5.111 capabilities: kimi-k3.1-pro flagship 1M/128k,
    # kimi-for-coding + k2.7-code coding variants 256k/64k.
    assert _provider_context_fallback("kimi-k3.1-pro") == 1048576
    assert _provider_context_fallback("kimi-for-coding") == 262144
    assert _provider_context_fallback("kimi-k2.7-code") == 262144

def test_provider_fallback_unknown_is_zero():
    assert _provider_context_fallback("") == 0
    assert _provider_context_fallback("some-random-model") == 0


def _resp(finish, content, reasoning=None):
    msg = types.SimpleNamespace(content=content, reasoning_content=reasoning)
    choice = types.SimpleNamespace(finish_reason=finish, message=msg)
    return types.SimpleNamespace(choices=[choice])


def test_length_cut_with_reasoning_detected():
    assert _text_was_cut_off(_resp("length", "", "some thought")) is True


def test_normal_stop_not_cut():
    assert _text_was_cut_off(_resp("stop", "", "some thought")) is False


def test_length_cut_with_text_not_blank():
    assert _text_was_cut_off(_resp("length", "hello", "thought")) is False


def test_length_cut_without_reasoning_not_retried():
    # Genuinely empty (no thought at all) must NOT loop a retry.
    assert _text_was_cut_off(_resp("length", "", None)) is False
    assert _text_was_cut_off(_resp("length", "  ", "")) is False

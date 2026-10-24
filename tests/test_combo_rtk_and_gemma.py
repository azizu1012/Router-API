"""Unit and integration tests for:
1. Gemma 4 standalone models & safe context window limits (185k/14k)
2. RTK smart truncation on tool messages and large byte payloads (> 8192)
3. Combo Model Mix resolution (Waterfall & Load Balance) and MIN context length rule
4. Database alias persistence for combo targets
5. Keepalive stream stall and comment intervals
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from src.core.config.api_config import (
    AVAILABLE_MODELS,
    MODEL_CONTEXT_LENGTH,
    GEMMA_CONTEXT_LENGTH,
)
from src.logical_HQ_translator.compression.rtk import (
    compress_messages,
    filter_smart_truncate,
)
from src.logical_HQ_translator.model.alias import (
    get_model_safe_context_length,
    get_alias_context_length,
    resolve_combo_target,
)
from src.api.claude_proxy.handler.streaming.keepalive import (
    STREAM_STALL_TIMEOUT,
    KEEPALIVE_INTERVAL,
)


# ---------------------------------------------------------------------------
# 1. Gemma 4 & Safe Context Length Tests
# ---------------------------------------------------------------------------

def test_safe_context_length_defaults():
    assert MODEL_CONTEXT_LENGTH == 185000
    assert GEMMA_CONTEXT_LENGTH == 14000

    assert "gemma-4-26b" in AVAILABLE_MODELS
    assert "gemma-4-31b" in AVAILABLE_MODELS

    gemma26 = AVAILABLE_MODELS["gemma-4-26b"]
    assert gemma26["rpm"] == 30
    assert gemma26["tpm"] == 16000
    assert gemma26["rpd"] == 14400
    assert gemma26["context_length"] == 14000
    assert gemma26["physical_context"] == 262144

    gemma31 = AVAILABLE_MODELS["gemma-4-31b"]
    assert gemma31["rpm"] == 30
    assert gemma31["tpm"] == 16000
    assert gemma31["rpd"] == 14400
    assert gemma31["context_length"] == 14000
    assert gemma31["physical_context"] == 262144

    flash = AVAILABLE_MODELS["gemini-flash"]
    assert flash["context_length"] == 185000
    assert flash["physical_context"] == 1048576


def test_get_model_safe_context_length():
    assert get_model_safe_context_length("gemini-flash") == 185000
    assert get_model_safe_context_length("gemini-flash-lite") == 185000
    assert get_model_safe_context_length("gemma-4-26b") == 14000
    assert get_model_safe_context_length("gemma-4-31b") == 14000
    # Custom / unknown model defaults to MODEL_CONTEXT_LENGTH
    assert get_model_safe_context_length("custom-unknown-model") == 185000


# ---------------------------------------------------------------------------
# 2. RTK Smart Truncation & Message Compression Tests
# ---------------------------------------------------------------------------

def test_rtk_truncate_lines():
    # >= 250 lines triggers line truncation
    lines = [f"line {i}" for i in range(300)]
    text = "\n".join(lines)
    truncated = filter_smart_truncate(text)
    assert "... +" in truncated
    assert "lines truncated" in truncated


def test_rtk_truncate_large_single_line():
    # Long text with < 250 lines but > 8192 bytes triggers character truncation
    big_payload = "A" * 15000
    truncated = filter_smart_truncate(big_payload)
    assert len(truncated) < 15000
    assert "truncated by RTK to preserve 250K TPM budget" in truncated
    assert truncated.startswith("A" * 4000)
    assert truncated.endswith("A" * 2000)


def test_rtk_compress_messages_openai_tool_role():
    # OpenAI style message with role: "tool"
    huge_content = "RESULT_DATA " * 1500  # > 16k chars
    messages = [
        {"role": "user", "content": "Fetch data"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_123"}]},
        {"role": "tool", "tool_call_id": "call_123", "content": huge_content},
    ]

    compress_messages(messages, enabled=True)
    tool_msg = messages[2]
    assert "truncated by RTK to preserve 250K TPM budget" in tool_msg["content"]
    assert len(tool_msg["content"]) < len(huge_content)


def test_rtk_compress_messages_anthropic_tool_result():
    # Anthropic style message with content as list of dicts (tool_result)
    huge_output = "OUTPUT " * 2000
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_1",
                        "content": huge_output,
                    }
                ],
            }
        ]
    }
    compress_messages(body, enabled=True)
    block = body["messages"][0]["content"][0]
    assert "truncated by RTK to preserve 250K TPM budget" in block["content"]


# ---------------------------------------------------------------------------
# 3. Combo Model Mix & MIN Context Length Tests
# ---------------------------------------------------------------------------

def test_get_alias_context_length_single_target():
    alias = {
        "target_model": "gemma-4-26b",
        "context_length": 0,
        "targets": "",
    }
    # Inherits from target_model
    assert get_alias_context_length(alias) == 14000

    # Explicit override takes priority
    alias["context_length"] = 50000
    assert get_alias_context_length(alias) == 50000


def test_get_alias_context_length_combo_min_rule():
    # Combo with Flash (185k) and Gemma (14k)
    targets = [
        {"model": "gemini-flash", "priority": 1},
        {"model": "gemma-4-26b", "priority": 2},
    ]
    alias = {
        "targets": json.dumps(targets),
        "target_model": "gemini-flash",
        "context_length": 0,
    }
    # MIN(185000, 14000) = 14000
    assert get_alias_context_length(alias) == 14000


def test_get_alias_context_length_combo_with_custom_endpoint():
    # Custom endpoint with context_length 64000 and Flash (185k)
    targets = [
        {"model": "gemini-flash", "priority": 1},
        {"endpoint": "vllm-deepseek", "context_length": 64000, "priority": 2},
    ]
    alias = {
        "targets": targets,  # passed as Python list
        "target_model": "gemini-flash",
        "context_length": 0,
    }
    assert get_alias_context_length(alias) == 64000


def test_resolve_combo_target_waterfall():
    targets = [
        {"model": "gemini-flash", "priority": 1},
        {"model": "gemini-flash-lite", "priority": 2},
        {"model": "gemma-4-26b", "priority": 3},
    ]
    # Waterfall first attempt (attempt 0)
    tgt0 = resolve_combo_target(targets, strategy="waterfall", attempt_index=0)
    assert tgt0["model"] == "gemini-flash"

    # Waterfall fallback (attempt 1)
    tgt1 = resolve_combo_target(targets, strategy="waterfall", attempt_index=1)
    assert tgt1["model"] == "gemini-flash-lite"

    # Waterfall fallback (attempt 2)
    tgt2 = resolve_combo_target(targets, strategy="waterfall", attempt_index=2)
    assert tgt2["model"] == "gemma-4-26b"

    # Out-of-bounds wraps around
    tgt3 = resolve_combo_target(targets, strategy="waterfall", attempt_index=3)
    assert tgt3["model"] == "gemini-flash"


def test_resolve_combo_target_load_balance():
    targets = [
        {"model": "gemini-flash", "weight": 10},
        {"model": "gemini-flash-lite", "weight": 5},
    ]
    selected = set()
    for _ in range(50):
        t = resolve_combo_target(targets, strategy="load_balance")
        selected.add(t["model"])
    # Both targets should be sampled over 50 iterations
    assert "gemini-flash" in selected
    assert "gemini-flash-lite" in selected


# ---------------------------------------------------------------------------
# 4. Database CRUD & Schema Auto-Migration for Combo Aliases
# ---------------------------------------------------------------------------

def test_alias_db_combo_persistence():
    from src.backend.models.aliases import (
        add_alias_db,
        get_alias_by_id_db,
        resolve_alias_db,
        update_alias_db,
    )
    import uuid

    alias_name = f"test-combo-{uuid.uuid4().hex[:6]}"
    targets = [
        {"model": "gemini-flash", "priority": 1},
        {"model": "gemma-4-26b", "priority": 2},
    ]

    alias_id = add_alias_db(
        account_id="acc-test-123",
        alias_name=alias_name,
        target_model="gemini-flash",
        targets=targets,
        strategy="waterfall",
        context_length=0,
        label="Test combo alias",
    )
    assert bool(alias_id)

    record = get_alias_by_id_db(alias_id)
    assert record is not None
    assert record["alias_name"] == alias_name
    assert record["strategy"] == "waterfall"
    assert record["targets"] == json.dumps(targets)
    assert record["context_length"] == 0

    # Resolving alias should return targets and computed context length
    resolved = resolve_alias_db(account_id="acc-test-123", alias_name=alias_name)
    assert resolved is not None
    assert resolved["target_model"] == "gemini-flash"
    assert resolved["targets"] == json.dumps(targets)
    assert resolved["strategy"] == "waterfall"

    # Update alias
    update_alias_db(
        alias_id=alias_id,
        strategy="load_balance",
        context_length=32000,
    )
    updated = get_alias_by_id_db(alias_id)
    assert updated["strategy"] == "load_balance"
    assert updated["context_length"] == 32000


# ---------------------------------------------------------------------------
# 5. Keep-Alive Configuration Tests
# ---------------------------------------------------------------------------

def test_keepalive_configuration():
    # Stall timeout must be generous (at least 600s, default 900s) to endure deep thinking and multi-key rotations
    assert STREAM_STALL_TIMEOUT >= 600.0
    assert KEEPALIVE_INTERVAL == 3.0


# ---------------------------------------------------------------------------
# 6. Gemma 4 Thinking Support & Budget Sanitization Tests
# ---------------------------------------------------------------------------

def test_gemma_thinking_support_and_budget_sanitization():
    from src.core.providers.gemini.thinking import (
        get_default_thinking_for_model,
        resolve_thinking_config,
        sanitize_thinking_for_gemma,
        build_thinking_config,
    )
    from src.core.providers.gemini.input_builder import build_gemini_inputs

    # 1. Default thinking for Gemma 4 is high, NEVER thinking_budget (which causes HTTP 400)
    for model in ["gemma-4-26b", "gemma-4-26b-a4b-it", "gemma-4-31b", "gemma-4-31b-it"]:
        cfg = get_default_thinking_for_model(model)
        assert cfg == {"thinking_level": "high", "include_thoughts": True}
        assert "thinking_budget" not in cfg

    # 2. Client passing thinking_budget > 0 is safely converted to thinking_level="high"
    res_budget = resolve_thinking_config("gemma-4-26b-a4b-it", thinking_budget=2048)
    assert res_budget == {"thinking_level": "high", "include_thoughts": True}
    assert "thinking_budget" not in res_budget

    # 3. Client passing thinking_budget = 0 disables thinking via thinking_level="minimal"
    res_zero = resolve_thinking_config("gemma-4-26b-a4b-it", thinking_budget=0)
    assert res_zero == {"thinking_level": "minimal", "include_thoughts": False}

    # 4. Client passing "low" or "medium" is mapped to "high" (Google API rejects low/medium on Gemma with 400)
    res_low = resolve_thinking_config("gemma-4-26b-a4b-it", thinking_level="low")
    assert res_low == {"thinking_level": "high", "include_thoughts": True}

    res_med = resolve_thinking_config("gemma-4-31b-it", thinking_level="medium")
    assert res_med == {"thinking_level": "high", "include_thoughts": True}

    # 5. Client turning thinking off maps to "minimal"
    for off_val in ["none", "off", "false", "minimal"]:
        res_off = resolve_thinking_config("gemma-4-26b-a4b-it", thinking_level=off_val)
        assert res_off == {"thinking_level": "minimal", "include_thoughts": False}

    # 6. Direct sanitization of dirty thinking_config containing thinking_budget
    dirty_cfg = {"thinking_budget": 8192, "include_thoughts": True}
    clean_cfg = sanitize_thinking_for_gemma(dirty_cfg)
    assert clean_cfg == {"thinking_level": "high", "include_thoughts": True}
    assert "thinking_budget" not in clean_cfg

    # 7. build_gemini_inputs sanitizes thinking_config for Gemma models
    messages = [{"role": "user", "content": "Hello"}]
    contents, system, tools, resolved_thinking = build_gemini_inputs(
        model_id="gemma-4-26b-a4b-it",
        messages=messages,
        tools=None,
        temperature=0.7,
        max_output_tokens=100,
        reasoning_effort=None,
        thinking_config={"thinking_budget": 4096, "include_thoughts": True},
    )
    assert resolved_thinking == {"thinking_level": "high", "include_thoughts": True}
    assert "thinking_budget" not in resolved_thinking


# ---------------------------------------------------------------------------
# 7. Gemma Model Alias Resolution & Anti-Hijack Routing Tests
# ---------------------------------------------------------------------------

def test_gemma_alias_normalization():
    from src.core.router import router

    assert router.resolve_model_alias("gemma-4-26b") == "gemma-4-26b"
    assert router.resolve_model_alias("gemma-4-26b-a4b-it") == "gemma-4-26b"
    assert router.resolve_model_alias("gemma-4-27b") == "gemma-4-26b"
    assert router.resolve_model_alias("gemma-27b") == "gemma-4-26b"
    assert router.resolve_model_alias("gemma-4-31b") == "gemma-4-31b"
    assert router.resolve_model_alias("gemma-4-31b-it") == "gemma-4-31b"
    assert router.resolve_model_alias("gemma-31b") == "gemma-4-31b"
    assert router.resolve_model_alias("gemma-4") == "gemma-4-26b"


@pytest.mark.anyio
async def test_gemma_and_native_never_hijacked_by_account_endpoint():
    from src.core.providers.custom_endpoint.passthrough import resolve_custom_endpoint_for_request

    fake_account = {"account_id": "c39b4e87be0dc8af"}
    # Native and Gemma models must NEVER be captured by account-level default custom endpoints
    for model_name in [
        "gemma-4-26b",
        "gemma-4-27b",
        "gemma-27b",
        "gemma-4-31b",
        "gemma-31b",
        "gemini-flash",
        "gemini-flash-lite",
    ]:
        ep = await resolve_custom_endpoint_for_request(account=fake_account, requested_model=model_name)
        assert ep is None, f"{model_name} should not be hijacked to a custom endpoint!"



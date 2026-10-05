"""Anthropic Messages API protocol conformance tests.

Covers the translation layer between the Anthropic wire format and the
OpenAI-shaped structures PoolManager produces. These are offline tests — no
network, no API keys, no running server.

Run: pytest tests/test_anthropic_protocol.py -v
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.claude_proxy.handler.anthropic_spec import (
    apply_tool_choice_to_tools,
    build_error_event,
    compute_usage,
    count_cache_breakpoints,
    estimate_input_tokens,
    estimate_tokens,
    extract_sampling_params,
    extract_tool_choice,
    map_stop_reason,
    thinking_signature,
)
from src.logical_HQ_translator.message_converter import _convert_messages


# ── Request params ──────────────────────────────────────────────────────────

class TestExtractSamplingParams:
    def test_all_params(self):
        body = {"stop_sequences": ["STOP", "END"], "top_p": 0.85, "top_k": 40}
        out = extract_sampling_params(body)
        assert out["stop_sequences"] == ["STOP", "END"]
        assert out["top_p"] == 0.85
        assert out["top_k"] == 40

    def test_missing_params_absent(self):
        assert extract_sampling_params({}) == {}

    def test_top_p_clamped(self):
        assert extract_sampling_params({"top_p": 5.0})["top_p"] == 1.0
        assert extract_sampling_params({"top_p": -1.0})["top_p"] == 0.0

    def test_rejects_wrong_types(self):
        # Anthropic always sends these typed; a malformed body must not crash us.
        out = extract_sampling_params({"top_p": "high", "top_k": True, "stop_sequences": "STOP"})
        assert "top_p" not in out
        assert "top_k" not in out
        assert "stop_sequences" not in out

    def test_empty_stop_sequences_dropped(self):
        assert "stop_sequences" not in extract_sampling_params({"stop_sequences": []})

    def test_filters_non_string_stops(self):
        assert extract_sampling_params({"stop_sequences": ["A", 5, None, ""]})["stop_sequences"] == ["A"]


class TestExtractToolChoice:
    def test_auto(self):
        assert extract_tool_choice({"tool_choice": {"type": "auto"}})["tool_choice"] == "auto"

    def test_any(self):
        assert extract_tool_choice({"tool_choice": {"type": "any"}})["tool_choice"] == "any"

    def test_none(self):
        assert extract_tool_choice({"tool_choice": {"type": "none"}})["tool_choice"] == "none"

    def test_forced_tool(self):
        out = extract_tool_choice({"tool_choice": {"type": "tool", "name": "Bash"}})
        assert out["tool_choice"] == {"type": "function", "function": {"name": "Bash"}}

    def test_disable_parallel_propagates(self):
        out = extract_tool_choice({"tool_choice": {"type": "auto", "disable_parallel_tool_use": True}})
        assert out["disable_parallel_tool_use"] is True

    def test_absent_tool_choice(self):
        assert extract_tool_choice({}) == {}

    def test_forced_tool_without_name_ignored(self):
        assert extract_tool_choice({"tool_choice": {"type": "tool"}}) == {}

    def test_tool_choice_none_strips_tools(self):
        tools = [{"type": "function", "function": {"name": "Bash"}}]
        assert apply_tool_choice_to_tools({"tool_choice": "none"}, tools) == []

    def test_tool_choice_auto_keeps_tools(self):
        tools = [{"type": "function", "function": {"name": "Bash"}}]
        assert apply_tool_choice_to_tools({"tool_choice": "auto"}, tools) == tools


# ── Response: stop_reason ───────────────────────────────────────────────────

class TestMapStopReason:
    def test_end_turn(self):
        assert map_stop_reason("stop")[0] == "end_turn"

    def test_max_tokens(self):
        assert map_stop_reason("length")[0] == "max_tokens"
        assert map_stop_reason("MAX_TOKENS")[0] == "max_tokens"

    def test_tool_use(self):
        assert map_stop_reason("stop", has_tool_calls=True)[0] == "tool_use"
        assert map_stop_reason("tool_calls", has_tool_calls=True)[0] == "tool_use"

    def test_refusal_from_content_filter(self):
        assert map_stop_reason("content_filter")[0] == "refusal"
        assert map_stop_reason("SAFETY")[0] == "refusal"
        assert map_stop_reason("RECITATION")[0] == "refusal"

    def test_stop_sequence_detected(self):
        reason, seq = map_stop_reason(
            "stop", stop_sequences=["HALT"], emitted_text="all done HALT more"
        )
        assert reason == "stop_sequence"
        assert seq == "HALT"

    def test_stop_sequence_absent_falls_back_to_end_turn(self):
        reason, seq = map_stop_reason(
            "stop", stop_sequences=["HALT"], emitted_text="all done"
        )
        assert reason == "end_turn"
        assert seq is None

    def test_tool_use_beats_stop_sequence_when_tools_present(self):
        # A stop sequence firing mid-tool-call still means we owe the client tools.
        reason, _ = map_stop_reason(
            "stop", has_tool_calls=True, stop_sequences=["HALT"], emitted_text="x HALT"
        )
        assert reason == "tool_use"

    def test_max_tokens_beats_stop_sequence(self):
        reason, _ = map_stop_reason(
            "length", stop_sequences=["HALT"], emitted_text="x HALT"
        )
        assert reason == "max_tokens"

    def test_none_finish_reason(self):
        assert map_stop_reason(None)[0] == "end_turn"

    def test_all_anthropic_stop_reasons_are_reachable(self):
        # Guards against regressions that drop a value from the enum.
        seen = {
            map_stop_reason("stop")[0],
            map_stop_reason("length")[0],
            map_stop_reason("stop", stop_sequences=["X"], emitted_text="X")[0],
            map_stop_reason("stop", has_tool_calls=True)[0],
            map_stop_reason("content_filter")[0],
        }
        assert seen == {"end_turn", "max_tokens", "stop_sequence", "tool_use", "refusal"}


# ── Response: usage ─────────────────────────────────────────────────────────

class TestCacheBreakpoints:
    def test_counts_system_tools_messages(self):
        body = {
            "system": [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}],
            "tools": [{"name": "Bash", "cache_control": {"type": "ephemeral"}}],
            "messages": [{
                "role": "user",
                "content": [{"type": "text", "text": "t", "cache_control": {"type": "ephemeral"}}],
            }],
        }
        assert count_cache_breakpoints(body) == 3

    def test_no_breakpoints(self):
        body = {"system": "plain", "tools": [{"name": "Bash"}],
                "messages": [{"role": "user", "content": "hi"}]}
        assert count_cache_breakpoints(body) == 0

    def test_string_system_ignored(self):
        assert count_cache_breakpoints({"system": "text"}) == 0


class TestComputeUsage:
    def test_all_four_fields_always_present(self):
        usage = compute_usage({}, 1000, 50)
        assert set(usage) == {
            "input_tokens", "output_tokens",
            "cache_creation_input_tokens", "cache_read_input_tokens",
        }

    def test_no_cache_control_means_no_cache_tokens(self):
        usage = compute_usage({}, 1000, 50)
        assert usage["cache_read_input_tokens"] == 0
        assert usage["cache_creation_input_tokens"] == 0
        assert usage["input_tokens"] == 1000

    def test_cache_control_splits_prefix(self):
        body = {"system": [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}]}
        usage = compute_usage(body, 4000, 50)
        assert usage["cache_read_input_tokens"] == 3000
        assert usage["input_tokens"] == 1000

    def test_cache_control_below_minimum_reports_no_cache(self):
        # Anthropic will not cache a prefix under its minimum, so neither do we.
        from src.api.claude_proxy.handler.anthropic_spec import MIN_CACHEABLE_TOKENS
        body = {"system": [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}]}
        usage = compute_usage(body, MIN_CACHEABLE_TOKENS - 1, 50)
        assert usage["cache_read_input_tokens"] == 0
        assert usage["input_tokens"] == MIN_CACHEABLE_TOKENS - 1

    def test_totals_are_conserved(self):
        # input + cache_read must still equal the real input size, or the client's
        # context bar drifts away from reality.
        body = {"tools": [{"name": "Bash", "cache_control": {"type": "ephemeral"}}]}
        usage = compute_usage(body, 4000, 100)
        assert usage["input_tokens"] + usage["cache_read_input_tokens"] == 4000

    def test_zero_output_safe(self):
        assert compute_usage({}, 0, 0)["output_tokens"] == 0


class TestEstimateInputTokens:
    def test_counts_system_and_messages(self):
        body = {"system": "you are helpful", "messages": [{"role": "user", "content": "hello"}]}
        assert estimate_input_tokens(body) >= 1

    def test_counts_tool_schemas(self):
        with_tools = {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "Bash", "description": "run a shell command"}],
        }
        without = {"messages": [{"role": "user", "content": "hi"}]}
        assert estimate_input_tokens(with_tools) > estimate_input_tokens(without)

    def test_counts_tool_result_content(self):
        body = {"messages": [{
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x" * 4000}],
        }]}
        assert estimate_input_tokens(body) > 500

    def test_empty_body_safe(self):
        assert estimate_input_tokens({}) == 1

    def test_ignores_non_dict_messages(self):
        assert estimate_input_tokens({"messages": ["junk", None]}) >= 1


class TestThinkingSignature:
    def test_non_empty_and_prefixed(self):
        sig = thinking_signature("some reasoning")
        assert sig.startswith("gmni_")
        assert len(sig) > 10

    def test_deterministic(self):
        assert thinking_signature("abc") == thinking_signature("abc")

    def test_differs_by_content(self):
        assert thinking_signature("abc") != thinking_signature("abd")

    def test_empty_input_still_produces_signature(self):
        # Anthropic rejects an empty signature on a thinking block.
        assert thinking_signature("").startswith("gmni_")


class TestBuildErrorEvent:
    def test_shape(self):
        ev = build_error_event(ValueError("boom"), "overloaded_error")
        assert ev["type"] == "error"
        assert ev["error"]["type"] == "overloaded_error"
        assert ev["error"]["message"] == "boom"

    def test_long_message_truncated(self):
        ev = build_error_event(ValueError("x" * 5000))
        assert len(ev["error"]["message"]) <= 500


def test_estimate_tokens_sane():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


# ── Content blocks ──────────────────────────────────────────────────────────

class TestContentBlockConversion:
    def test_document_block_not_silently_dropped(self):
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{
                "role": "user",
                "content": [{"type": "document", "source": {"type": "base64",
                                 "media_type": "application/pdf", "data": "JVBER"}}],
            }],
        }
        msgs, _ = _convert_messages(body)
        joined = " ".join(str(m.get("content", "")) for m in msgs)
        assert "Document attached" in joined

    def test_search_result_block_converted(self):
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{
                "role": "user",
                "content": [{"type": "search_result", "source": "https://x.test",
                             "title": "T",
                             "content": [{"type": "text", "text": "grounded text"}]}],
            }],
        }
        msgs, _ = _convert_messages(body)
        assert any("grounded text" in str(m.get("content", "")) for m in msgs)

    def test_tool_result_still_works(self):
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "user", "content": "list files"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_abc123", "name": "list_dir",
                     "input": {"path": "/tmp"}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_abc123", "content": "a.txt"}]},
            ],
            "tools": [{"name": "list_dir", "description": "ls",
                       "input_schema": {"type": "object"}}],
        }
        msgs, tools = _convert_messages(body)
        assert len(tools) == 1
        assert any(m.get("role") == "tool" and m.get("tool_call_id") == "toolu_abc123"
                   for m in msgs)

    def test_agent_use_maps_to_agent_tool_name(self):
        # Regression guard for the bug documented in docs/bug-logs.md: Gemini must
        # receive "Agent", never the agent_type metadata value.
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "user", "content": "explore"},
                {"role": "assistant", "content": [
                    {"type": "agent_use", "id": "toolu_a1", "agent_type": "general-purpose",
                     "prompt": "go look"}]},
                {"role": "user", "content": [
                    {"type": "agent_result", "agent_use_id": "toolu_a1", "content": "found it"}]},
            ],
        }
        msgs, _ = _convert_messages(body)
        assistant = next(m for m in msgs if m.get("role") == "assistant")
        fn_name = assistant["tool_calls"][0]["function"]["name"]
        assert fn_name == "Agent"
        assert fn_name != "general-purpose"

    def test_thinking_blocks_are_dropped_not_echoed(self):
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": [
                    {"type": "thinking", "thinking": "secret", "signature": "sig"},
                    {"type": "text", "text": "answer"}]},
            ],
        }
        msgs, _ = _convert_messages(body)
        joined = json.dumps(msgs)
        assert "secret" not in joined
        assert "answer" in joined


# ── End-to-end shape validation ─────────────────────────────────────────────

def _assert_valid_anthropic_message(msg: dict):
    """Assert the response satisfies the Anthropic Message schema."""
    assert msg["type"] == "message"
    assert msg["role"] == "assistant"
    assert isinstance(msg["id"], str) and msg["id"].startswith("msg_")
    assert isinstance(msg["content"], list)
    assert msg["stop_reason"] in {
        "end_turn", "max_tokens", "stop_sequence", "tool_use",
        "pause_turn", "refusal", "model_context_window_exceeded",
    }
    assert "stop_sequence" in msg
    usage = msg["usage"]
    for field in ("input_tokens", "output_tokens",
                  "cache_creation_input_tokens", "cache_read_input_tokens"):
        assert field in usage, f"usage missing {field}"
        assert isinstance(usage[field], int)
    for block in msg["content"]:
        assert block["type"] in {"text", "thinking", "redacted_thinking",
                                 "tool_use", "server_tool_use", "agent_use"}
        if block["type"] == "thinking":
            # Anthropic requires a non-empty signature on every thinking block.
            assert block.get("signature"), "thinking block missing signature"
        if block["type"] == "text":
            assert isinstance(block.get("text"), str)
        if block["type"] == "tool_use":
            assert block.get("id") and block.get("name")
            assert isinstance(block.get("input"), dict)


def test_spec_module_importable_from_route_layer():
    """The count_tokens route and the proxy must share one implementation."""
    from src.api.claude_proxy.handler.anthropic_spec import estimate_input_tokens as a
    from src.server.openai_server.routes.completions_routes import estimate_input_tokens as b
    assert a is b
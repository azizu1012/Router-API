"""Provider-declared RPM per custom endpoint + GenAI<->OpenAI tool interop.

Two guarantees pinned here:

1. The provider-side RPM lives on the endpoint row. The account-side gate
   runs separately in the request path, so the effective ceiling is
   min(provider_rpm, account_rpm). These tests pin the provider half:
   per-endpoint buckets (two endpoints serving one model do not share),
   and rpm=0 meaning unset (falls back to the built-in default bucket).

2. Tool calls survive the GenAI<->OpenAI translation both ways. A Gemini
   native client doing function calling through a custom endpoint must get
   its functionCall parts back, and the OpenAI messages sent out must carry
   tool_calls / tool results with matching ids so the orphan guard keeps
   the pair instead of dropping it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.providers.custom_endpoint import adapter as cep_adapter
from src.core.providers.custom_endpoint import client as cep_client
from src.core.providers.genai_types import types as gt


@pytest.mark.anyio
async def test_provider_rpm_buckets_are_per_endpoint():
    ok_a1 = await cep_client.check_custom_pool_rate("shared-model-x", "prov-ep-a", 1)
    blocked_a2 = await cep_client.check_custom_pool_rate("shared-model-x", "prov-ep-a", 1)
    ok_b1 = await cep_client.check_custom_pool_rate("shared-model-x", "prov-ep-b", 1)
    blocked_b2 = await cep_client.check_custom_pool_rate("shared-model-x", "prov-ep-b", 1)
    assert (ok_a1, blocked_a2, ok_b1, blocked_b2) == (True, False, True, False)


@pytest.mark.anyio
async def test_provider_rpm_zero_means_default_bucket():
    for _ in range(3):
        assert await cep_client.check_custom_pool_rate("m-default-x", "prov-ep-c", 0) is True
    assert await cep_client.check_custom_pool_rate("m-default-x", "prov-ep-other", 0) is True


def _contents_with_tools():
    return [
        gt.Content(role="user", parts=[gt.Part.from_text(text="weather?")]),
        gt.Content(role="model", parts=[
            gt.Part.from_text(text="looking up"),
            gt.Part(function_call=gt.FunctionCall(name="get_weather", args={"city": "Hanoi"})),
        ]),
        gt.Content(role="user", parts=[
            gt.Part(function_response=gt.FunctionResponse(name="get_weather", response={"temp": 30})),
        ]),
    ]


def test_genai_contents_keep_tool_calls_with_matching_ids():
    msgs = cep_adapter.genai_contents_to_openai_messages(_contents_with_tools(), "sys")
    assert msgs[0] == {"role": "system", "content": "sys"}
    assistants = [m for m in msgs if m.get("role") == "assistant"]
    assert len(assistants) == 1
    tool_calls = assistants[0].get("tool_calls") or []
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "get_weather"
    assert tool_calls[0]["function"]["arguments"] == '{"city": "Hanoi"}'
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == tool_calls[0]["id"]


def test_tool_pair_survives_orphan_guard():
    from src.core.providers.custom_endpoint.client import _drop_orphaned_tool_results
    msgs = cep_adapter.genai_contents_to_openai_messages(_contents_with_tools(), "")
    kept = _drop_orphaned_tool_results(msgs)
    assert any((m.get("tool_calls") or []) for m in kept if m.get("role") == "assistant")
    assert any(m.get("role") == "tool" for m in kept)


def test_unanswered_tool_call_is_dropped_not_sent():
    """9router rejects assistant tool_calls with no matching tool result.

    Middle-out truncation cuts on message boundaries, so a call can lose its
    result upstream. The guard must drop the unanswered call (the model
    re-issues it next turn) instead of sending a pair 9router answers 400
    `No tool output found for function call ...`.
    """
    from src.core.providers.custom_endpoint.client import _drop_orphaned_tool_results
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "call_lost", "type": "function",
                         "function": {"name": "Bash", "arguments": "{}"}}]},
        # No tool message answering call_lost — its result was pruned upstream.
    ]
    kept = _drop_orphaned_tool_results(msgs)
    for m in kept:
        assert not (m.get("role") == "assistant" and m.get("tool_calls")), kept


def test_answered_call_kept_while_unanswered_sibling_dropped():
    from src.core.providers.custom_endpoint.client import _drop_orphaned_tool_results
    msgs = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_ok", "type": "function",
             "function": {"name": "Bash", "arguments": "{}"}},
            {"id": "call_lost", "type": "function",
             "function": {"name": "Read", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_ok", "content": "done"},
    ]
    kept = _drop_orphaned_tool_results(msgs)
    assistants = [m for m in kept if m.get("role") == "assistant"]
    assert len(assistants) == 1
    ids = [tc.get("id") for tc in assistants[0].get("tool_calls") or []]
    assert ids == ["call_ok"], kept


def test_duplicate_tool_results_keep_only_first_output():
    """Strict Responses-compatible endpoints allow one output per call ID."""
    from src.core.providers.custom_endpoint.client import _drop_orphaned_tool_results

    msgs = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_once", "type": "function",
             "function": {"name": "Bash", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_once", "content": "first"},
        {"role": "tool", "tool_call_id": "call_once", "content": "retry"},
    ]

    kept = _drop_orphaned_tool_results(msgs)
    outputs = [m for m in kept if m.get("role") == "tool"]
    assert outputs == [{"role": "tool", "tool_call_id": "call_once", "content": "first"}]


def test_genai_tools_convert_to_openai():
    tools = [gt.Tool(function_declarations=[gt.FunctionDeclaration(
        name="get_weather",
        description="city weather",
        parameters={"type": "object", "properties": {"city": {"type": "string"}}},
    )])]
    out = cep_adapter.genai_tools_to_openai_tools(tools)
    assert len(out) == 1
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "get_weather"
    assert out[0]["function"]["parameters"]["type"] == "object"


def test_openai_result_carries_tool_calls_back_to_genai():
    resp = cep_adapter.openai_result_to_genai_response(
        "",
        5,
        7,
        tool_calls=[{"id": "genai_get_weather", "type": "function", "function": {
            "name": "get_weather", "arguments": '{"city": "Hanoi"}'}}],
    )
    parts = resp["candidates"][0]["content"]["parts"]
    assert parts == [{"functionCall": {"name": "get_weather", "args": {"city": "Hanoi"}}}]
    assert resp["candidates"][0]["finishReason"] == "STOP"
    assert resp["usageMetadata"]["promptTokenCount"] == 5


def test_db_roundtrip_keeps_provider_limits(temp_db):
    from src.backend.endpoints import manager as ep_mgr
    ep = ep_mgr.add_endpoint_db("prov-ep-db", "http://x.local", "k", rpm=50, timeout=17)
    assert ep["rpm"] == 50
    assert ep["timeout"] == 17
    ep2 = ep_mgr.update_endpoint_db("prov-ep-db", rpm=25)
    assert ep2 is not None
    assert ep2["rpm"] == 25
    assert ep2["timeout"] == 17
    got = ep_mgr.get_endpoint_db("prov-ep-db")
    assert got is not None
    assert got["rpm"] == 25
    assert got["timeout"] == 17


def test_db_coerces_garbage_limits_to_zero(temp_db):
    from src.backend.endpoints import manager as ep_mgr
    ep_mgr.add_endpoint_db("prov-ep-garbage", "http://y.local", "k")
    ep = ep_mgr.update_endpoint_db("prov-ep-garbage", rpm="abc", timeout=None)
    assert ep is not None
    assert ep["rpm"] == 0
    assert ep["timeout"] == 0

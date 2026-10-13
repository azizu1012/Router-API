import sys
import os
import json

# Ensure root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.logical_HQ_translator.messages.converter import _convert_messages, _GLOBAL_TOOL_NAME_CACHE

def test_conversion_normal_and_truncated():
    # 1. Test normal message flow where tool call is present
    body = {
        "model": "claude-sonnet-4-20250514",
        "system": "You are a helpful assistant.",
        "messages": [
            {
                "role": "user",
                "content": "List files."
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_12345",
                        "name": "list_dir",
                        "input": {"path": "/var/tmp"}
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_12345",
                        "content": "file1.txt\nfile2.txt"
                    }
                ]
            }
        ],
        "tools": [
            {
                "name": "list_dir",
                "description": "list directories",
                "input_schema": {"type": "object"}
            }
        ]
    }
    
    openai_messages, openai_tools = _convert_messages(body)
    print("Normal messages output:")
    print(json.dumps(openai_messages, indent=2))
    
    # Assertions for normal run
    assert len(openai_messages) == 4 # system, user, assistant with tool_calls, tool response
    assert openai_messages[0]["role"] == "system"
    assert openai_messages[1]["role"] == "user"
    assert openai_messages[2]["role"] == "assistant"
    assert "tool_calls" in openai_messages[2]
    assert openai_messages[2]["tool_calls"][0]["id"] == "toolu_12345"
    assert openai_messages[3]["role"] == "tool"
    assert openai_messages[3]["name"] == "list_dir"
    
    # Verify global cache was populated
    assert _GLOBAL_TOOL_NAME_CACHE.get("toolu_12345") == "list_dir"

    # 2. Test truncated message flow (assistant tool call message is missing)
    truncated_body = {
        "model": "claude-sonnet-4-20250514",
        "system": "You are a helpful assistant.",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_12345",
                        "content": "file1.txt\nfile2.txt"
                    }
                ]
            }
        ],
        "tools": [
            {
                "name": "list_dir",
                "description": "list directories",
                "input_schema": {"type": "object"}
            }
        ]
    }
    
    openai_messages_trunc, _ = _convert_messages(truncated_body)
    print("\nTruncated messages output (converted to user message):")
    print(json.dumps(openai_messages_trunc, indent=2))
    
    # Assertions for truncated run
    # Should have system message and user message representing the orphaned tool output
    assert len(openai_messages_trunc) == 2
    assert openai_messages_trunc[0]["role"] == "system"
    assert openai_messages_trunc[1]["role"] == "user"
    assert openai_messages_trunc[1]["content"] == "[Tool Result: list_dir]\nfile1.txt\nfile2.txt"
    
    print("\nALL CONVERSION AND TRUNCATION TESTS PASSED SUCCESSFULY!")


def _orphans(msgs):
    """tool_result ids kept whose tool_use was pruned."""
    produced = {tc["id"] for m in msgs if m.get("role") == "assistant"
                for tc in (m.get("tool_calls") or [])}
    needed = {m["tool_call_id"] for m in msgs
              if str(m.get("role") or "").startswith("tool")}
    return needed - produced


def _wire_orphans(messages):
    """Orphans actually left in the payload the provider receives.

    Mirrors what custom_endpoint/client.py does: guard, then serialise. The
    guard is deliberately not inside the payload builders, which stay pure
    translations.
    """
    from src.core.providers.anthropic_wire import openai_to_anthropic_body
    from src.core.providers.custom_endpoint.client import _drop_orphaned_tool_results

    wire = openai_to_anthropic_body(
        "m", _drop_orphaned_tool_results(messages), max_tokens=16)
    used, answered = set(), set()
    for m in wire["messages"]:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if b.get("type") == "tool_use":
                used.add(b.get("id"))
            elif b.get("type") == "tool_result":
                answered.add(b.get("tool_use_id"))
    return answered - used


def test_wire_layer_drops_orphans_whatever_produced_them():
    """The Anthropic wire is the last gate before the provider sees the body.

    Every editor of message history runs after the converter's own orphan
    handling, so none of them can be trusted to leave a consistent pair. This
    asserts the invariant on the serialized payload instead of on any one
    upstream stage, which is the only place it actually has to hold.
    """
    cases = {
        "id desync from id synthesis": [
            {"role": "assistant", "tool_calls": [{"id": "synthetic_a", "function": {"name": "Read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "", "content": "result"},
        ],
        "truncation cut the tool_use": [
            {"role": "tool", "tool_call_id": "call_gone", "content": "result"},
            {"role": "assistant", "content": "unrelated"},
        ],
        "unknown id": [
            {"role": "assistant", "tool_calls": [{"id": "call_ok", "function": {"name": "Read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_ok", "content": "good"},
            {"role": "tool", "tool_call_id": "call_bad", "content": "bad"},
        ],
    }
    for label, msgs in cases.items():
        assert not _wire_orphans(msgs), f"{label}: {_wire_orphans(msgs)}"


def test_wire_layer_keeps_intact_tool_pairs():
    from src.core.providers.anthropic_wire import openai_to_anthropic_body
    from src.core.providers.custom_endpoint.client import _drop_orphaned_tool_results

    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "tool_calls": [{"id": "call_1", "function": {"name": "Read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "the file"},
        {"role": "assistant", "content": "done"},
    ]
    wire = openai_to_anthropic_body(
        "m", _drop_orphaned_tool_results(msgs), max_tokens=16)
    blocks = [b for m in wire["messages"] if isinstance(m.get("content"), list)
              for b in m["content"]]
    assert any(b.get("type") == "tool_result" for b in blocks), blocks


def test_the_openai_dialect_is_guarded_too():
    """One endpoint serves both dialects, so both payloads must be clean.

    The reported 400 came from an Anthropic-format endpoint, but the same owner
    also configures that endpoint as OpenAI-format. The invariant is the same —
    a `role: "tool"` message must answer a `tool_calls` entry — so a server
    that validates it rejects the request the same way, just worded differently.
    """
    from src.core.providers.custom_endpoint.client import _drop_orphaned_tool_results

    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "tool_calls": [
            {"id": "call_ok", "function": {"name": "Read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_ok", "content": "good"},
        {"role": "tool", "tool_call_id": "call_orphan", "content": "bad"},
    ]
    kept = _drop_orphaned_tool_results(msgs)
    ids = [m.get("tool_call_id") for m in kept if m.get("role") == "tool"]
    assert ids == ["call_ok"], ids


def _wire_faults(messages):
    """Every malformation the Anthropic wire would reject, as a set of strings."""
    from src.core.providers.anthropic_wire import openai_to_anthropic_body

    wire = openai_to_anthropic_body("m", messages, max_tokens=32)
    roles = [m["role"] for m in wire["messages"]]
    faults = set()
    if any(roles[i] == roles[i + 1] for i in range(len(roles) - 1)):
        faults.add("non_alternating")
    used, answered = set(), set()
    for m in wire["messages"]:
        for b in (m["content"] if isinstance(m["content"], list) else []):
            if b.get("type") == "tool_use":
                used.add(b.get("id"))
            elif b.get("type") == "tool_result":
                answered.add(b.get("tool_use_id"))
    if answered - used:
        faults.add("orphaned_tool_result")
    if used - answered:
        faults.add("unanswered_tool_use")
    return faults


ORPHAN_CASES = {
    "orphan between two assistant turns": [
        {"role": "user", "content": "go"},
        {"role": "assistant", "tool_calls": [
            {"id": "A", "function": {"name": "Read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "ORPHAN", "content": "stray"},
        {"role": "assistant", "content": "done"},
    ],
    "orphan trailing the history": [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "done"},
        {"role": "tool", "tool_call_id": "ORPHAN", "content": "stray"},
    ],
    "every result orphaned": [
        {"role": "user", "content": "go"},
        {"role": "tool", "tool_call_id": "ORPHAN", "content": "stray"},
        {"role": "assistant", "content": "done"},
    ],
    "one of two results orphaned": [
        {"role": "user", "content": "go"},
        {"role": "assistant", "tool_calls": [
            {"id": "A", "function": {"name": "Read", "arguments": "{}"}},
            {"id": "B", "function": {"name": "Edit", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "A", "content": "r1"},
        {"role": "tool", "tool_call_id": "B", "content": "r2"},
        {"role": "tool", "tool_call_id": "ORPHAN", "content": "stray"},
        {"role": "assistant", "content": "done"},
    ],
    "orphan between two user turns": [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "thinking"},
        {"role": "tool", "tool_call_id": "ORPHAN", "content": "stray"},
        {"role": "user", "content": "and now?"},
    ],
}


def test_the_guard_never_creates_a_new_malformation():
    """Removing an orphan must not trade one 400 for another.

    A `tool` message becomes a user turn on the wire, so it is what separates
    two assistant turns. Deleting one collapses that separator, and Anthropic
    rejects consecutive assistant messages as firmly as it rejects an orphaned
    tool_result. The guard is only worth having if it never introduces a fault
    that was not already there, so each case is compared against the unguarded
    result rather than judged in isolation.
    """
    from src.core.providers.custom_endpoint.client import _drop_orphaned_tool_results

    for label, msgs in ORPHAN_CASES.items():
        before = _wire_faults(list(msgs))
        after = _wire_faults(_drop_orphaned_tool_results(msgs))
        assert "orphaned_tool_result" in before, f"{label}: case is not an orphan case"
        assert "orphaned_tool_result" not in after, f"{label}: {after}"
        introduced = after - before
        assert not introduced, f"{label}: guard introduced {introduced} (had {before})"


def test_the_guard_keeps_a_pair_whose_id_is_empty_on_both_sides():
    """An empty id is an id. Dropping that pair would strand its tool_use.

    A tool_calls entry with no `id` yields a tool_use with id "", and the tool
    message answering it carries tool_call_id "". The two match, so treating an
    empty id as "no id" would delete the answer and leave the call hanging.
    """
    from src.core.providers.custom_endpoint.client import _drop_orphaned_tool_results

    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "Read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "", "content": "r"},
    ]
    kept = _drop_orphaned_tool_results(msgs)
    assert [m for m in kept if m.get("role") == "tool"], kept


def test_merging_adjacent_assistant_turns_preserves_tool_calls():
    from src.core.providers.custom_endpoint.client import _merge_adjacent_assistant_turns

    merged = _merge_adjacent_assistant_turns([
        {"role": "assistant", "content": "first", "tool_calls": [
            {"id": "A", "function": {"name": "Read", "arguments": "{}"}}]},
        {"role": "assistant", "content": "second", "tool_calls": [
            {"id": "B", "function": {"name": "Edit", "arguments": "{}"}}]},
    ])
    assert len(merged) == 1, merged
    assert merged[0]["content"] == "first\nsecond", merged
    assert [tc["id"] for tc in merged[0]["tool_calls"]] == ["A", "B"], merged


def test_the_guard_covers_all_four_custom_endpoint_send_paths():
    """Nonstream and stream, OpenAI and Anthropic, must all pass the guard.

    These four are the only places a custom endpoint is contacted, so an
    unwrapped one is a 400 waiting for whatever the stage above did to history.
    """
    import inspect

    from src.core.providers.custom_endpoint import client

    src = inspect.getsource(client)
    found = src.count("_drop_orphaned_tool_results(")
    assert found == 5, (
        "expected the definition plus 4 call sites "
        f"(openai/anthropic x nonstream/stream), found {found}"
    )


def test_plain_chat_is_untouched_by_the_wire_guard():
    """A chat with no tool traffic must survive the guard unchanged."""
    from src.core.providers.custom_endpoint.client import _drop_orphaned_tool_results

    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    assert _drop_orphaned_tool_results(msgs) == msgs


def test_id_less_tool_result_is_repaired_not_orphaned():
    """An empty tool_use_id is re-paired with its tool_use, so the tool
    exchange survives conversion instead of being dropped at the wire.

    This was the input-side orphan: `ensure_tool_ids_in_messages` synthesised an
    id for an empty `tool_use.id` but the `if tid and ...` guard skipped the
    matching result, so the two sides disagreed before anything was truncated.
    """
    from src.logical_HQ_translator.messages.converter import ensure_tool_ids_in_messages

    msgs = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "", "name": "Read", "input": {"p": "a"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "", "content": "file body"}]},
    ]
    ensure_tool_ids_in_messages(msgs)

    use_id = msgs[0]["content"][0]["id"]
    result_id = msgs[1]["content"][0]["tool_use_id"]
    assert use_id, use_id
    assert result_id == use_id, f"use={use_id!r} result={result_id!r}"


def test_repair_leaves_an_existing_id_alone():
    from src.logical_HQ_translator.messages.converter import ensure_tool_ids_in_messages

    msgs = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call_a", "name": "Read", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_a", "content": "r"}]},
    ]
    ensure_tool_ids_in_messages(msgs)
    assert msgs[1]["content"][0]["tool_use_id"] == "call_a"


def test_repair_pairs_parallel_calls_by_position():
    from src.logical_HQ_translator.messages.converter import ensure_tool_ids_in_messages

    msgs = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "", "name": "Read", "input": {}},
            {"type": "tool_use", "id": "", "name": "Edit", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "", "content": "a"},
            {"type": "tool_result", "tool_use_id": "", "content": "b"}]},
    ]
    ensure_tool_ids_in_messages(msgs)
    uses = [b["id"] for b in msgs[0]["content"]]
    results = [b["tool_use_id"] for b in msgs[1]["content"]]
    assert len(set(uses)) == 2, uses
    assert results == uses, (results, uses)


def test_truncation_never_leaves_an_orphaned_tool_result():
    """Middle-out truncation must not keep a tool_result whose tool_use it dropped.

    `split_idx` is a message boundary, not a tool-exchange boundary, so it can
    land between an assistant's tool_calls and the role="tool" messages that
    answer them. The result was a 400 `orphaned tool_result in conversation
    history` from Anthropic-format endpoints: the converter's own orphan guard
    runs before this truncation, so it never sees the orphan this creates.
    """
    from src.logical_HQ_translator.messages.truncation import emergency_truncate_to_limit

    base = [
        {"role": "user", "content": "start"},
        {"role": "user", "content": "filler " * 200},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "Read", "arguments": "{}"}},
            {"id": "call_2", "type": "function",
             "function": {"name": "Read", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "result one " * 50},
        {"role": "tool", "tool_call_id": "call_2", "content": "result two " * 50},
        {"role": "assistant", "content": "done"},
    ]

    # Sweep the whole budget range: the cut point must land mid-exchange at
    # some of these limits, which is exactly the reported failure.
    for limit in range(40, 260, 5):
        out = emergency_truncate_to_limit([dict(m) for m in base], limit)
        assert not _orphans(out), f"limit={limit} orphaned: {_orphans(out)} -> {out}"


def test_truncation_keeps_a_tool_exchange_it_did_not_cut():
    """Dropping the orphan is the fix; do not throw away intact tool pairs."""
    from src.logical_HQ_translator.messages.truncation import emergency_truncate_to_limit

    msgs = [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "Read", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "the file body"},
        {"role": "assistant", "content": "done"},
    ]
    out = emergency_truncate_to_limit(msgs, 10_000)
    assert not _orphans(out)
    assert any(m.get("role") == "tool" for m in out), out


class TestGeminiToolArgsAreNotFaked:
    """Gemini needs a real object, so there is no partial form to send.

    The same truncation that leaves a client tool call unusable reaches the
    Gemini wire here, and the handler answered it with an empty dict — which the
    model reads as a complete call with no parameters, not as an unfinished one.
    """

    @staticmethod
    def _calls(arguments):
        from src.core.providers.gemini.format import convert_messages_to_contents

        body = convert_messages_to_contents([
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "Write", "arguments": arguments}},
            ]},
        ])
        found = []
        for c in (body.get("contents") or []):
            for part in (c.get("parts") or []):
                if "functionCall" in part:
                    found.append(part["functionCall"])
        return found

    def test_a_truncated_call_is_dropped_rather_than_sent_empty(self):
        assert self._calls('{"path": "/a.py", "cont') == []

    def test_a_complete_call_keeps_its_arguments(self):
        calls = self._calls('{"path": "/a.py"}')
        assert len(calls) == 1, calls
        assert calls[0]["args"] == {"path": "/a.py"}, calls
        assert calls[0]["name"] == "Write", calls

    def test_a_no_argument_tool_still_goes_through(self):
        calls = self._calls("{}")
        assert len(calls) == 1, calls
        assert calls[0]["args"] == {}, calls


def test_truncation_does_not_mutate_the_callers_messages():
    """The notice must not be written back into the caller's history.

    This runs twice over the same body — the opening turn and the recursive turn
    after a hosted search — so an in-place write compounded the notice and left
    the caller's messages rewritten underneath them.
    """
    from src.logical_HQ_translator.messages.truncation import emergency_truncate_to_limit

    messages = [
        {"role": "user", "content": [{"type": "text", "text": "do the thing"}]},
        {"role": "user", "content": "filler " * 400},
        {"role": "assistant", "content": "done"},
    ]
    # Snapshot has to go two levels down. A shallow `dict(m)` copy still shares
    # the content list, which is exactly the aliasing under test — comparing
    # against it would compare the list with itself and always pass.
    import copy
    before = copy.deepcopy(messages)

    emergency_truncate_to_limit(messages, 20)

    assert messages == before, "the caller's messages were rewritten in place"


def test_repeated_truncation_does_not_stack_the_notice():
    from src.logical_HQ_translator.messages.truncation import emergency_truncate_to_limit

    messages = [
        {"role": "user", "content": [{"type": "text", "text": "do the thing"}]},
        {"role": "user", "content": "filler " * 400},
        {"role": "assistant", "content": "done"},
    ]
    # The caller's copy is what the recursion re-sends, so it must come back
    # unchanged; the notice belongs to the returned list only.
    emergency_truncate_to_limit(messages, 20)
    emergency_truncate_to_limit(messages, 20)
    text = messages[0]["content"][0]["text"]
    assert "lược bớt" not in text, text


class TestBroadcastDoesNotLetOneSlowClientStallTheFeed:
    """A stalled reader used to decide the latency for everyone.

    `broadcast` awaited each subscriber in turn with no timeout. The log watcher
    pushes one event per line, so a dashboard tab that stopped reading held the
    loop until its TCP window filled, and the watcher could not read another
    line meanwhile — one slow tab froze the feed and every request sharing that
    loop.
    """

    def test_stalled_subscribers_cost_one_timeout_not_one_each(self):
        import asyncio
        import time

        from src.server.realtime.websocket import ConnectionManager

        class _Fast:
            async def send_text(self, _payload):
                return None

        class _Stalled:
            async def send_text(self, _payload):
                await asyncio.sleep(30)      # never finishes within the test

        mgr = ConnectionManager()
        fast = _Fast()
        stalled = [_Stalled() for _ in range(3)]
        mgr._channels["log:x"] = {fast, *stalled}
        for ws in {fast, *stalled}:
            mgr._ws_channels[ws] = {"log:x"}

        async def run():
            return await mgr.broadcast("log:x", {"line": "hello"})

        started = time.monotonic()
        sent = asyncio.run(asyncio.wait_for(run(), timeout=30))
        elapsed = time.monotonic() - started

        # The fast reader was served and the stalled ones dropped. Waiting for
        # them in turn would cost one send timeout each; running them together
        # costs one, so adding slow tabs does not add latency.
        assert sent == 1, sent
        assert elapsed < 12, f"broadcast took {elapsed:.1f}s for 3 stalled clients"

    def test_broadcast_to_nobody_returns_immediately(self):
        import asyncio

        from src.server.realtime.websocket import ConnectionManager

        sent = asyncio.run(ConnectionManager().broadcast("log:none", {"a": 1}))
        assert sent == 0


def test_opencode_subagent_detection_and_response(monkeypatch):
    import asyncio
    monkeypatch.setattr(asyncio, "ensure_future", lambda *args, **kwargs: None)

    from src.api.opencode_proxy.handler.proxy import _is_sub_agent_request
    from src.api.opencode_proxy.handler.response import build_response

    # Test sub-agent detection via system prompt keyword
    subagent_body = {
        "model": "gemini-flash",
        "system": "you are the explore subagent",
        "messages": [{"role": "user", "content": "hello"}]
    }
    assert _is_sub_agent_request(subagent_body) is True

    # Test main agent detection (should not be subagent)
    main_body = {
        "model": "gemini-flash",
        "system": "you are the interactive main agent",
        "messages": [{"role": "user", "content": "hello"}]
    }
    assert _is_sub_agent_request(main_body) is False

    # Test build_response combines reasoning for sub-agent
    class MockChoice:
        def __init__(self, content, reasoning_content=None):
            class Message:
                def __init__(self, c, r):
                    self.content = c
                    self.reasoning_content = r
                    self.thinking = r
            self.message = Message(content, reasoning_content)
            self.finish_reason = "stop"

    class MockResponse:
        def __init__(self, choices):
            self.choices = choices
            self.usage = {"prompt_tokens": 10, "completion_tokens": 20}

    mock_resp = MockResponse([MockChoice("The main answer text", "Thinking process content")])
    
    # Under sub-agent body, build_response should wrap thinking in XML and prepend to text
    res = build_response(subagent_body, mock_resp, "gemini-flash", "test-key-12345", 10)
    msg = res["choices"][0]["message"]
    assert "<thinking>" in msg["content"]
    assert "Thinking process content" in msg["content"]
    assert "</thinking>" in msg["content"]
    assert "The main answer text" in msg["content"]
    assert "reasoning_content" not in msg or not msg["reasoning_content"]

    # Under main-agent body, build_response should keep thinking separate in reasoning_content
    res_main = build_response(main_body, mock_resp, "gemini-flash", "test-key-12345", 10)
    msg_main = res_main["choices"][0]["message"]
    assert "Thinking process content" not in msg_main["content"]
    assert msg_main["reasoning_content"] == "Thinking process content"


if __name__ == "__main__":
    test_conversion_normal_and_truncated()
    class DummyMonkeyPatch:
        def setattr(self, obj, attr, val):
            setattr(obj, attr, val)
    test_opencode_subagent_detection_and_response(DummyMonkeyPatch())



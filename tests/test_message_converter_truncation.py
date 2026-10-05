import sys
import os
import json

# Ensure root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.logical_HQ_translator.message_converter import _convert_messages, _GLOBAL_TOOL_NAME_CACHE

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

"""End-to-end regression for the strict 9router function-call invariant."""

import asyncio
import json

from src.core.providers.custom_endpoint.client import call_custom_nonstream
from src.logical_HQ_translator.messages.converter import _convert_messages


def test_fake_9router_accepts_resumed_claude_history_after_normalization():
    asyncio.run(_run_fake_9router_test())


async def _run_fake_9router_test():
    call_id = "call_01a16cc88b5572a99e9614c9abbd1daa"
    received = {}

    async def handler(reader, writer):
        headers = await reader.readuntil(b"\r\n\r\n")
        content_length = 0
        for line in headers.decode().splitlines():
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())
        payload = json.loads((await reader.readexactly(content_length)).decode())
        received["payload"] = payload
        calls = [
            tc.get("id")
            for msg in payload.get("messages", [])
            if msg.get("role") == "assistant"
            for tc in msg.get("tool_calls", [])
        ]
        outputs = [
            msg.get("tool_call_id")
            for msg in payload.get("messages", [])
            if msg.get("role") == "tool"
        ]
        if len(calls) != len(set(calls)) or len(outputs) != len(set(outputs)):
            body = json.dumps({"error": {"message": (
                f"Duplicate function_call_output for call_id '{call_id}'"
            )}}).encode()
            writer.write(
                b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
            )
        else:
            body = json.dumps({
                "id": "chatcmpl-fake",
                "choices": [{"message": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop"}],
            }).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
            )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        body = {"model": "muse/muse-spark-1.3-contributor", "messages": [
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": call_id,
                "name": "mcp__ccd_turn__update_plan", "input": {"advance": True},
            }]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": call_id, "content": "done",
            }]},
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": call_id,
                "name": "mcp__ccd_turn__update_plan", "input": {"advance": True},
            }]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": call_id, "content": "resent",
            }]},
        ]}
        messages, _ = _convert_messages(body)
        response = await call_custom_nonstream(
            f"http://127.0.0.1:{port}", "test-key", body["model"], messages,
            max_tokens=32,
        )
        assert response.choices[0].message.content == "ok"
        assert received["payload"]["messages"]
    finally:
        server.close()
        await server.wait_closed()

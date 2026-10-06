"""Anthropic SSE frame formatting.

One line of string building, used by the claude proxy (35 call sites), the SSE
cache agent (16), and auth middleware (6). auth needed it to emit its simulated
sub-agent failure stream, which meant auth imported sse_cache_agent for a frame
formatter — an import inside a function, which is the shape a cycle takes.
"""
import json
from typing import Any, Dict


def sse_event(event: str, data: Dict[str, Any]) -> bytes:
    """
    Tạo một chuỗi định dạng SSE (Server-Sent Events) từ tên sự kiện và dữ liệu.

    Args:
        event (str): Tên của sự kiện SSE.
        data (Dict[str, Any]): Dữ liệu (dưới dạng dictionary) sẽ được chuyển đổi thành JSON và gửi đi.

    Returns:
        bytes: Chuỗi bytes đã được mã hóa UTF-8 ở định dạng SSE.
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
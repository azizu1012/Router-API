"""Anthropic SSE frame formatting.

One line of string building, used by the claude proxy (35 call sites), the SSE
cache agent (16), and auth middleware (6). auth needed it to emit its simulated
sub-agent failure stream, which meant auth imported sse_cache_agent for a frame
formatter — an import inside a function, which is the shape a cycle takes.

It was not a cycle: the proxies never import auth. This module has no imports
beyond json, so nothing can make it one. See docs/bug-logs.md Bug #6.
"""
import json
from typing import Any, Dict, Optional


def sse_data(line: Any) -> Optional[str]:
    """Return the payload of an SSE data line, or None if not one.

    Gateways each end SSE slightly differently (``data: {...}`` with or
    without the space, final frame with or without a trailing newline), so
    every parser in the repo must accept every shape. This helper is that
    single spelling: ``client._parse_sse_stream`` and ``manager._call_chat``
    used to diverge here, and the tail half of the former silently dropped a
    final ``data:{...finish...}`` frame — turning a good turn into an
    abnormal EOF (commit 6ddf8b7).
    """
    if isinstance(line, (bytes, bytearray)):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(line, str):
        return None
    trimmed = line.strip()
    if trimmed.startswith("data: "):
        return trimmed[6:]
    if trimmed.startswith("data:"):
        return trimmed[5:]
    return None


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

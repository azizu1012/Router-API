"""Shared SSE wire helpers.

``sse_event`` writes one Anthropic-style frame; the Claude proxy is its only
caller — 59 sites in ``claude_proxy/handler/proxy_stream.py``. ``sse_data``
reads the payload back out of a ``data:`` line and is shared by both
custom-endpoint stream readers, which is why it accepts every spacing and
trailing-newline variant.

Streaming is the common transport for every endpoint, but framing is not yet
centralised: the OpenAI-compatible paths build ``chat.completion.chunk``
frames inside ``opencode_proxy``, and the Responses adapter emits its own
typed events. Only the Anthropic dialect goes through this module today.

Nothing here imports beyond ``json``/``typing``, so it cannot take part in an
import cycle. The old function-local import from ``sse_cache_agent`` was not
one either — see docs/bug-logs.md Bug #6.
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


def sse_event(event: str, data: Dict[str, Any], event_id: Optional[str] = None) -> bytes:
    """
    Tạo một chuỗi định dạng SSE (Server-Sent Events) từ tên sự kiện và dữ liệu.

    Args:
        event (str): Tên của sự kiện SSE.
        data (Dict[str, Any]): Dữ liệu (dưới dạng dictionary) sẽ được chuyển đổi thành JSON và gửi đi.

    Returns:
        bytes: Chuỗi bytes đã được mã hóa UTF-8 ở định dạng SSE.
    """
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")

"""Claude Proxy — pure format converter.

Delegates all key management, pool logic, retry, and quota to PoolManager.
"""

from typing import Any, Dict, List, Optional
import re

from src.core.config_n_logg import config
from .proxy_nonstream import ClaudeProxyNonstreamMixin
from .proxy_stream import ClaudeProxyStreamMixin


def _model_supports_thinking(model_id: str) -> bool:
    """
    Kiểm tra xem một model cụ thể có hỗ trợ tính năng "thinking" hay không.
    Các model "lite" không hỗ trợ thinking. Các model Gemini từ 2.0 trở lên
    (ví dụ: Gemini 2, Gemini 2.5, Gemini 3, Gemini 3.5) được coi là hỗ trợ thinking.

    Args:
        model_id (str): ID của model cần kiểm tra.

    Returns:
        bool: True nếu model hỗ trợ thinking, False nếu ngược lại.
    """
    m = model_id.lower()
    if "lite" in m:
        return False
    return any(x in m for x in ["gemini-2", "gemini-2.5", "gemini-3", "gemini-3.5"])


class ClaudeProxy(ClaudeProxyNonstreamMixin, ClaudeProxyStreamMixin):
    """
    `ClaudeProxy` hoạt động như một bộ chuyển đổi định dạng thuần túy cho các yêu cầu và phản hồi
    API của Claude, đặc biệt là cho các mô hình của Anthropic Claude.
    Nó kế thừa chức năng cho các yêu cầu non-streaming và streaming từ
    `ClaudeProxyNonstreamMixin` và `ClaudeProxyStreamMixin` tương ứng.

    Giống như `OpenCodeProxy`, nó ủy quyền tất cả logic quản lý khóa, logic pool, thử lại
    và quản lý hạn ngạch cho `PoolManager`. Vai trò chính của nó là:
    - Chuẩn bị tin nhắn: Chuyển đổi định dạng tin nhắn đến thành định dạng tương thích với các nhà cung cấp LLM backend.
    - Định dạng phản hồi: Chuyển đổi phản hồi từ các nhà cung cấp LLM backend trở lại định dạng Claude.
    - Xử lý các yêu cầu non-streaming và streaming một cách thống nhất thông qua các mixin.

    Proxy này được thiết kế để không chứa bất kỳ logic kinh doanh phức tạp nào liên quan đến pool,
    quản lý khóa hoặc cơ chế thử lại. Thay vào đó, nó dựa hoàn toàn vào `PoolManager` để xử lý
    các khía cạnh đó, đảm bảo sự tách biệt rõ ràng về mối quan tâm (separation of concerns).
    """
    pass


claude_proxy = ClaudeProxy()

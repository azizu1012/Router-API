"""Cross-request mapping from client tool_call id to tool name.

Gemini does not echo the tool_call id back on a function call, so the only
way to name the tool in the response is to remember the id->name pairing
from the request that produced it. message_converter writes it, providers
reads it back, so it cannot live in either — it is shared mutable state and
this module is the neutral floor both sides already stand on.

Both of those callers once reached into src/logical_HQ_translator/messages/converter.py
for it, which put a providers->translator edge in the dependency graph. Moving the
cache out is what removed that edge; tests/test_layering.py now keeps it removed.
See docs/bug-logs.md Bug #6.
"""
from collections import OrderedDict
import threading


class ToolNameCache:
    """
    `ToolNameCache` là một cache LRU (Least Recently Used) để lưu trữ ánh xạ từ ID cuộc gọi công cụ
    (tool call ID) sang tên công cụ (tool name).

    Attributes:
        max_size (int): Kích thước tối đa của cache. Mặc định là 5000.
        cache (OrderedDict[str, str]): Lưu trữ các cặp key-value theo thứ tự LRU.
        lock (threading.Lock): Khóa để đồng bộ hóa truy cập cache.
    """
    def __init__(self, max_size=5000):
        self.max_size = max_size
        self.cache = OrderedDict()
        self.lock = threading.Lock()

    def set(self, key: str, value: str):
        """
        Thêm hoặc duy trì một cặp key-value vào cache.
        Nếu key đã tồn tại, không ghi đè để bảo vệ tên tool của session trước đó
        và refresh độ mới của entry.
        Nếu cache đạt đến `max_size`, mục cũ nhất sẽ bị loại bỏ.
        """
        if not key:
            return
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return
            if len(self.cache) >= self.max_size:
                self.cache.popitem(last=False)
            self.cache[key] = value

    def get(self, key: str) -> str:
        """
        Lấy tên công cụ từ cache dựa trên ID cuộc gọi công cụ và refresh vị trí LRU.
        """
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return ""

_GLOBAL_TOOL_NAME_CACHE = ToolNameCache()
"""
Instance toàn cục của `ToolNameCache` được sử dụng để duy trì ánh xạ
tên công cụ trên toàn bộ ứng dụng. Điều này cho phép các phần khác nhau của code
tra cứu tên công cụ một cách nhất quán và hiệu quả.
"""


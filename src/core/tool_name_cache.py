"""Cross-request mapping from client tool_call id to tool name.

Gemini does not echo the tool_call id back on a function call, so the only
way to name the tool in the response is to remember the id->name pairing
from the request that produced it. message_converter writes it, providers
reads it back, so it cannot live in either — it is shared mutable state and
this module is the neutral floor both sides already stand on.

Both of those callers once reached into src/logical_HQ_translator/message_converter.py
for it, which put a providers->translator edge in the dependency graph. Moving the
cache out is what removed that edge; tests/test_layering.py now keeps it removed.
See docs/bug-logs.md Bug #6.
"""
import threading


class ToolNameCache:
    """
    `ToolNameCache` là một cache LRU (Least Recently Used) để lưu trữ ánh xạ từ ID cuộc gọi công cụ
    (tool call ID) sang tên công cụ (tool name). Điều này rất hữu ích trong quá trình chuyển đổi
    tin nhắn để nhanh chóng tra cứu tên công cụ cho các phản hồi công cụ.

    Cache này có kích thước tối đa cố định (`max_size`) và sử dụng một `threading.Lock`
    để đảm bảo an toàn luồng khi truy cập và sửa đổi cache.

    Attributes:
        max_size (int): Kích thước tối đa của cache. Mặc định là 5000.
        cache (Dict[str, str]): Dictionary lưu trữ các cặp key-value (tool_call_id: tool_name).
        keys_list (List[str]): Danh sách các key theo thứ tự được thêm vào/truy cập để quản lý LRU.
        lock (threading.Lock): Khóa để đồng bộ hóa truy cập cache.
    """
    def __init__(self, max_size=5000):
        self.max_size = max_size
        self.cache = {}
        self.keys_list = []
        self.lock = threading.Lock()

    def set(self, key: str, value: str):
        """
        Thêm hoặc cập nhật một cặp key-value vào cache.
        Nếu cache đạt đến `max_size`, mục cũ nhất sẽ bị loại bỏ.

        Args:
            key (str): ID của cuộc gọi công cụ.
            value (str): Tên của công cụ.
        """
        if not key:
            return
        with self.lock:
            if key in self.cache:
                self.cache[key] = value
                return
            if len(self.cache) >= self.max_size:
                oldest = self.keys_list.pop(0)
                self.cache.pop(oldest, None)
            self.cache[key] = value
            self.keys_list.append(key)

    def get(self, key: str) -> str:
        """
        Lấy tên công cụ từ cache dựa trên ID cuộc gọi công cụ.

        Args:
            key (str): ID của cuộc gọi công cụ.

        Returns:
            str: Tên công cụ nếu tìm thấy, ngược lại là chuỗi rỗng.
        """
        with self.lock:
            return self.cache.get(key) or ""

_GLOBAL_TOOL_NAME_CACHE = ToolNameCache()
"""
Instance toàn cục của `ToolNameCache` được sử dụng để duy trì ánh xạ
tên công cụ trên toàn bộ ứng dụng. Điều này cho phép các phần khác nhau của code
tra cứu tên công cụ một cách nhất quán và hiệu quả.
"""

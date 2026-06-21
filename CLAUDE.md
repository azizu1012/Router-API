# Router API — Claude Code Instructions

Xem `AGENTS.md` cho workflow chung (TODO list, batch read, sub-agent usage).

## Quy tắc code

- Không commit `.env`, `usage.db`, `logs/`
- DB dùng SQLite qua `src/backend/_db.py`
- Dùng logger:

| File | Import |
|------|--------|
| Chung | `from src.core.config import logger` |
| Theo ngữ cảnh | `from src.core.logger import logger_system/proxy/keys/api/keepalive` |

## Pool & Key rules quan trọng

Các quy tắc và kiến trúc liên quan đến Pool & Key được mô tả chi tiết trong `docs/architecture_overview.md` và `docs/routing_and_resilience.md`. Vui lòng tham khảo các tài liệu này trước khi sửa đổi code liên quan đến pool/key.

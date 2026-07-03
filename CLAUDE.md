# Router API — Claude Code Instructions

Xem `AGENTS.md` cho workflow chung (TODO list, batch read, sub-agent usage).

## Quy tắc code

- Always query CodeGraph MCP tools to resolve symbol definitions and understand code topology before falling back to heavy grep commands.
- Không commit `.env`, `usage.db`, `logs/`
- DB dùng SQLite qua `src/backend/_db.py`
- Dùng logger:

| Ngữ cảnh | Import |
|----------|--------|
| Chung | `from src.core.config_n_logg.logger import logger_system as logger` |
| Proxy | `from src.core.config_n_logg.logger import logger_proxy as logger` |
| Keys | `from src.core.config_n_logg.logger import logger_keys as logger` |
| API | `from src.core.config_n_logg.logger import logger_api` |
| Keepalive | `from src.core.config_n_logg.logger import logger_keepalive` |

## Pool & Key rules quan trọng

Các quy tắc và kiến trúc liên quan đến Pool & Key được mô tả chi tiết trong `docs/architecture_overview.md` và `docs/routing_and_resilience.md`. Vui lòng tham khảo các tài liệu này trước khi sửa đổi code liên quan đến pool/key.

- Pool hiện tại là concurrent worker pool (`src/core/router/pool.py`) — ModelPool singleton với slot-based `acquire`/`release`, lock-protected. Custom endpoint là first-class member của pool thông qua `pool_assignments`.
- Model ngoài `MODEL_POOLS` chạy standalone mode (không qua pool acquire/release).
- `pool_manager.py` hiện tại ~573 dòng.

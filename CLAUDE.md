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

Pool & Key rules đã có trong `project_snapshot.md` (CodeGraph) và `CLAUDE.md` (system instructions). Đọc trước khi sửa pool/key code.

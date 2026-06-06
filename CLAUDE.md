# Router API — Claude Code Instructions

## Nguyên tắc làm việc (2026-06)

### 1. TODO vừa đủ

- Chỉ tạo TODO khi task có từ 3 bước trở lên hoặc chạm nhiều file.
- Không tạo TODO cho việc nhỏ, một bước.
- Luôn cập nhật trạng thái TODO ngay sau khi hoàn thành mỗi bước.

### 2. Đọc file theo lô, song song

- Luôn lập danh sách các file cần đọc trước khi gọi tool.
- Ưu tiên đọc nhiều file một lượt bằng gọi song song.
- Tránh đọc tuần tự từng file nếu không bắt buộc.
- Dùng tìm kiếm nhanh trước khi đọc sâu (grep/search) khi chưa chắc vị trí.

### 3. Sub-agent chỉ khi thật cần

- Chỉ dùng sub-agent khi cần scan rộng hoặc tìm kiếm khó.
- Không dồn hết mọi việc vào một sub-agent.
- Mỗi sub-agent chỉ làm 1 mục tiêu, chỉ đọc file, trả về dữ liệu có cấu trúc.

Mẫu prompt sub-agent:
- `description`: 3-5 từ, đúng mục tiêu.
- `prompt`: nói rõ chỉ RESEARCH, không ghi file, liệt kê chính xác file cần đọc, yêu cầu trả về bảng/ASCII.

### 4. Không tự động cập nhật snapshot

- Không tự động regenerate project_snapshot.md mỗi lần chạy.
- Chỉ cập nhật snapshot khi người dùng yêu cầu rõ ràng.

## Cấu trúc dự án

```
src/
  api/claude_proxy.py     # Claude→Gemini proxy (stream + non-stream)
  server/pass_through_server/ # Pass-through server endpoints (OpenAI, Anthropic, Gemini)
  backend/                 # DB layer (accounts, endpoints, keys, schema)
  core/                    # Router, rate limiter, config, usage logger
console/admin_console.py  # CLI admin tool
main.py                   # Entry point (uvicorn)
```

## Quy tắc code

- Không commit file `.env`, `usage.db`, `logs/`
- DB dùng SQLite qua `src/backend/_db.py` (connection pool + RLock)
- Không tạo logger mới. Dùng logger sẵn có:
   - `from src.core.config import logger` cho log chung
   - hoặc `from src.core.logger import logger_system/logger_proxy/logger_keys/logger_api/logger_keepalive` theo ngữ cảnh

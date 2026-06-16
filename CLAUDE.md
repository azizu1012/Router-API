# Router API — Claude Code Instructions

## Nguyên tắc làm việc (2026-06)

### 1. TODO bắt buộc cho task phức tạp

- Khi thấy task từ 3 bước trở lên hoặc chạm nhiều file → **tự động tạo TODO list ngay** trước khi làm.
- Ghi cụ thể từng bước, theo đúng thứ tự cần làm.
- Làm xong bước nào → **tick hoàn thành bước đó ngay** (dùng todowrite).
- Không tự ý làm bước sau khi bước trước chưa xong.
- Chỉ skip TODO nếu task chỉ 1-2 bước đơn giản.

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
- Chỉ cập nhật snapshot khi người dùng yêu cầu rõ ràng.


## Cấu trúc dự án

```
src/
  api/claude_proxy.py     # Claude→Gemini proxy (stream + non-stream)
  logical_HQ_translator/   # Resources/converters shared between Claude proxy and OpenCode proxy
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

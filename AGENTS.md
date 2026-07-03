# Project Instructions

- Always query CodeGraph MCP tools to resolve symbol definitions and understand code topology before falling back to heavy grep commands.

## 1. TODO bắt buộc cho task phức tạp

- Task từ 3 bước trở lên hoặc chạm nhiều file → tạo TODO list ngay trước khi làm.
- Ghi cụ thể từng bước, đúng thứ tự.
- Làm xong bước nào → tick ngay (dùng todowrite).
- Không tự ý làm bước sau khi bước trước chưa xong.
- Chỉ skip TODO nếu task đơn giản (1-2 bước).

## 2. Đọc file theo lô, song song

- Lập danh sách file trước khi gọi tool.
- Ưu tiên đọc nhiều file một lượt (gọi song song).
- Dùng grep/glob trước khi đọc sâu nếu chưa chắc vị trí.

## 3. Sub-agent / explore

- Chỉ dùng khi cần scan rộng hoặc tìm kiếm khó.
- Mỗi sub-agent chỉ làm 1 mục tiêu, chỉ RESEARCH, không ghi file.
- `description`: 3-5 từ, đúng mục tiêu.

## 4. Code rules

- Không commit `.env`, `usage.db`, `logs/`
- DB dùng SQLite qua `src/backend/_db.py`
- Dùng logger có sẵn: `from src.core.config_n_logg.logger import logger_system/logger_proxy/logger_keys/logger_api/logger_keepalive`

## 5. Luồng request & file map (đọc trước khi planning)

Một request đi qua các tầng theo thứ tự sau. Xác định task chạm vào tầng nào → chỉ đọc file tương ứng:

```
Client → src/server/ (routes)
  → src/api/<proxy>/handler/proxy.py  (format converter, ko có logic pool/key)
    → src/core/pool_manager.py         (retry loop, error classify, swap member)
      → src/core/router/               (APIRouter, KeyResolver, ModelPool)
        → src/core/limits/             (GeminiRateLimiter, RPM/TPM)
          → src/core/providers/        (gemini_facade, custom_endpoint_manager)
```

| Task | File(s) chạm |
|------|-------------|
| Thêm/xoá model alias | `src/core/router/core/router.py`, `src/core/api_config.py` |
| Sửa retry/backoff logic | `src/core/pool_manager.py` |
| Sửa cách chọn key | `src/core/router/core/key_resolver.py` (Double Random) |
| Sửa rate limit | `src/core/limits/gemini_rate_limiter.py` |
| Thêm provider mới | `src/core/providers/`, `src/backend/endpoints.py` |
| Sửa response format | `src/api/<proxy>/handler/` |
| Sửa DB schema | `src/backend/_db.py` + file tương ứng trong `src/backend/` |
| Dashboard FE | `frontend-src/` (React build → `src/frontend/`) |
| Admin console CLI | `src/console/admin_console/` |
**PoolManager (573 dòng)** là monolithic intentional (`docs/architecture_overview.md` mục 6). Không cần decompose — chỉ cần focus vào nhánh transient error vs hard error + pool vs standalone.

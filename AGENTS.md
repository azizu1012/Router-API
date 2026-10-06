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
        → src/core/limits/             (GeminiRateLimiter, TokenRateLimiter, RPM/TPM)
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
| Sửa DB schema | `src/backend/schema.py` + `_db.py` nếu bảng đọc lúc import |
| Dashboard FE | `frontend-src/` (React build → `src/frontend/`) |
| Admin console CLI | `src/console/admin_console/` |
**PoolManager (581 dòng)** là monolithic intentional (`docs/architecture_overview.md` mục 6). Không cần decompose — chỉ cần focus vào nhánh transient error vs hard error + pool vs standalone.

## 6. Phân tầng — tầng dưới KHÔNG được import tầng trên

Thứ tự tầng, từ trên xuống:

```
src/api/<proxy>/        protocol client (Anthropic / OpenAI)
src/server/             transport (FastAPI, routes, auth)
src/core/               engine
src/core/providers/     đáy — nói chuyện với SDK
```

`src/logical_HQ_translator/` **không phải một tầng** — nó là ngang hàng với `src/core/`.

| Quy tắc | Lý do |
|---------|--------|
| `src/core/providers/` không import `logical_HQ_translator` hay `src/api/` | providers là tầng đáy. Luật riêng cho Gemini phải nằm cạnh SDK nó phục vụ, không phải chỗ khác rồi vay lên |
| `src/logical_HQ_translator/` không import `src/api/` | translator là peer của core, không nằm dưới tầng protocol |
| `src/core/` không import `src/server/` hay `src/api/` | core nằm dưới transport; transport được phép phụ thuộc core, không ngược lại |

### Vì sao cần máy kiểm, không chỉ cần quy tắc

Bug này **không sinh ra lỗi runtime nào** — mọi hàm vẫn trả về đúng. Nên nó sống sót. `tests/test_layering.py` chạy bằng AST và **fail CI** khi vi phạm. Đây là lý do file đó tồn tại; đừng xoá vì "docs đã ghi rồi".

Nếu vi phạm, đừng import `from __future__` hay `# noqa` cho qua. Làm đúng một trong hai:

1. **Helper thuộc về một tầng** → dời sang tầng đó. Ví dụ `_sanitize_schema_for_gemini` chỉ tồn tại để chiều google-genai → `src/core/providers/gemini/schema_sanitizer.py`.
2. **Helper thuộc về cả hai** → đặt vào `src/core/` như hàm thuần: không đọc config, không giữ state riêng, không I/O. Ví dụ `sub_agent_detect`, `sse_format`, `tool_name_cache`.

Hàm có side effect thì phân bố lại **sẽ** đổi hành vi — phải test trước.

### Import trong hàm không phải là lá chữa

Có **252** import trong thân hàm, và đó là chủ ý — chủ yếu để phá vòng lòng thật. Nhưng import trong hàm **cũng che được vi phạm tầng**: `auth.py` từng import `is_sub_agent_body` trong thân hàm, trông như vòng lòng, nhưng proxy **không bao giờ** import `auth` — nên nó chỉ là vi phạm tầng được giấu.

Muốn lazy-import một module, hãy xác nhận trước nó có tạo vòng lòng thật không. Nếu không, chuyển lên module-level.

### Nợ đã biết

`src/core/providers/custom_endpoint_manager.py` import `src.server.websocket_manager` để broadcast health lên dashboard — sai hướng, được liệt kê tường minh trong `KNOWN_EXCEPTIONS` của `tests/test_layering.py`. Sửa cần event sink trong `core/`, việc lớn hơn một refactor vị trí.

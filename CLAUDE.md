# Router API — Claude Code Instructions

Xem `AGENTS.md` cho workflow chung (TODO list, batch read, sub-agent usage).

## Phân tầng — đọc trước khi sửa import

`src/api/` và `src/server/` ở trên, `src/core/` ở dưới, `src/core/providers/` là đáy.
`src/logical_HQ_translator/` là **peer của `src/core/`**, không phải một tầng.

Tầng dưới **không được** import tầng trên. Quy tắc đầy đủ ở `AGENTS.md` mục 6, và
`tests/test_layering.py` **fail** khi vi phạm — nếu bạn thấy test đó đỏ sau khi sửa
import, đó là báo đúng, không phải test sai.

Helper dùng chung cho cả hai bên thì đặt vào `src/core/` như hàm thuần:
`src/core/sub_agent_detect.py`, `sse_format.py`, `tool_name_cache.py`.
Lý do và lịch sử: `docs/bug-logs.md` Bug #6.

## Quy tắc code

- Always query CodeGraph MCP tools to resolve symbol definitions and understand code topology before falling back to heavy grep commands.
- Không commit `.env`, `usage.db`, `logs/`
- DB dùng SQLite qua `src/backend/_db.py`
- Khi viết test integration (nhất là test routing), **đừng assert cứng `200`** nếu chưa mock rate limiter/pool. DB trống trên CI sẽ làm request bị trả về `429` (do capacity=0) hoặc `503`. Hãy assert loại trừ (`not in (404, 405)`) hoặc mock cẩn thận.
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
- `pool_manager.py` hiện tại 631 dòng.

## Điều phối Sub-Agent OpenCode (Multi-Agent Orchestration)

Khi cần sửa nhiều code đồng loạt, refactor quy mô lớn, viết boilerplate/tests hoặc "brute-force" chỉnh sửa mã nguồn, Claude đóng vai trò **Architect/Reviewer** điều phối **OpenCode CLI** thực thi trực tiếp thay vì tự làm tốn context/token:

- **Lệnh điều phối**: `opencode run --model <model> --auto "<chỉ thị rõ ràng>"`
- **Các model có sẵn trong OpenCode**:
  1. `opencode/space-bunny-free`:
     - **Ưu tiên số 1** cho các task cày việc nặng, brute-force sửa nhiều file, quét codebase lớn nhờ 1M context miễn phí, tốc độ phản hồi nhanh, ổn định và $0 cost.
     - *Rủi ro*: Có thể chạm rate limit hoặc hết quota tạm thời trong giai đoạn preview.
  2. `azuris-router/gemini-flash` (chạy qua local gateway `http://127.0.0.1:58100/opencode/v1`):
     - Thông minh, suy luận tốt, thích hợp cho các bài toán logic phức tạp hơn.
     - *Rủi ro*: Chạy qua lớp tương thích local gateway nên đôi khi có thể bị lag, đơ hoặc treo kết nối.
- **Chiến lược phân bổ task**:
  - Phân phối task hợp lý: dùng `space-bunny-free` làm lực lượng cày việc chính (nhanh, ổn định, rẻ); dùng `gemini-flash` khi cần logic sâu hoặc khi `space-bunny-free` hết quota.
  - Nếu `gemini-flash` bị lag hoặc timeout, lập tức fallback sang `space-bunny-free`.
- **Quy trình nghiệm thu (Quality Gate)**:
  - Sau khi OpenCode hoàn thành chỉnh sửa trên đĩa, Claude **luôn** kiểm tra lại bằng `git diff` và chạy test (như `pytest tests/test_layering.py`) trước khi xác nhận.

<!-- claude-reflect: learned rules (saved from the band; edit freely) -->
## Learned rules
- Implement robust key rotation that automatically switches keys on failure rather than returning errors, and ensure core functionality survives agent failures without relying solely on 9router.
<!-- end claude-reflect -->

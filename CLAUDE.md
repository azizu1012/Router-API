# Router API — Claude Code Instructions

Xem `AGENTS.md` cho workflow chung (TODO list, batch read, sub-agent usage).

## Phân tầng — đọc trước khi sửa import

`src/api/` và `src/server/` ở trên, `src/core/` ở dưới, `src/core/providers/` là đáy.
`src/logical_HQ_translator/` là **peer của `src/core/`**, không phải một tầng.

Tầng dưới **không được** import tầng trên. Quy tắc đầy đủ ở `AGENTS.md` mục 6, và
`tests/test_layering.py` **fail** khi vi phạm — nếu bạn thấy test đó đỏ sau khi sửa
import, đó là báo đúng, không phải test sai.

Helper dùng chung cho cả hai bên thì đặt vào `src/core/runtime/` như hàm thuần:
`src/core/runtime/sub_agent_detect.py`, `sse_format.py`, `tool_name_cache.py`.
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
- `PoolManager` hiện tại nằm ở `src/core/orchestration/pool_manager.py` (631 dòng).

## Điều phối Sub-Agent OpenCode (Hybrid Co-working & Support Worker)

### 1. Nguyên tắc cốt lõi: Claude làm chính (Primary Engineer), OpenCode trợ lực khi cần
- **Mặc định Claude tự tay thực hiện**: Các task thông thường, sửa bug, viết tính năng vừa/nhỏ, tối ưu logic lõi, refactor cục bộ và chạy test sẽ do **Claude trực tiếp làm và kiểm tra**.
- **Không thụ động khoán trắng**: OpenCode là công cụ hỗ trợ (worker assistant), không thay thế vai trò xử lý trực tiếp của Claude. Kể cả khi điều động OpenCode, Claude vẫn là người đồng hành (thiết kế luồng, xử lý logic tinh vi, soát lại toàn bộ và vá lỗi).

### 2. Tiêu chí Kích Hoạt OpenCode (Trigger Criteria)
Chỉ kích hoạt OpenCode CLI hỗ trợ khi gặp các trường hợp sau:
1. **Tác động diện rộng (Bulk / Broad impact)**: Refactor, đổi tên symbol/pattern hoặc di chuyển code đồng loạt trên nhiều file (> 3-4 files).
2. **Cày boilerplate & Viết test số lượng lớn**: Viết hàng loạt unit test/mock hoặc sinh code lặp lại tốn nhiều lượt token.
3. **Độ khó / phức tạp cao hoặc quét phân tích sâu**: Cần phân nhánh xử lý song song, hoặc quét/phân tích sâu ngữ cảnh khổng lồ trên toàn bộ repo mà không muốn làm đầy context phiên chính.
4. **Người dùng yêu cầu cụ thể**: Khi người dùng chỉ định dùng OpenCode để làm task.

### 3. Các Model trong OpenCode & Chiến lược phân bổ:
1. `opencode/deepseek-v4.1-flash-free`:
   - **Ưu tiên số 1 mặc định**: Miễn phí, code thông minh, nhanh, ổn định và $0 cost.
   - *Lưu ý*: Nếu chạm rate limit/hết quota preview thì chuyển sang model 2.
2. `azuris-router/gemini-flash` (local gateway `http://127.0.0.1:58100/opencode/v1`):
   - Dùng khi cần suy luận logic sâu hoặc khi model 1 hết quota.
   - *Lưu ý*: Nếu chạy qua local gateway bị lag/treo > 30s ➔ Lập tức fallback về `opencode/deepseek-v4.1-flash-free`.

### 4. Quy trình Thực thi & Nghiệm thu khi dùng OpenCode (Quality Gate):
1. **Giao việc an toàn & Tiết kiệm token tối đa**:
   - Tạo file chỉ thị tạm (e.g. `opencode_task.txt`), chạy:
     `opencode run --model <model> --auto -f opencode_task.txt "<prompt ngắn gọi thực thi file>"`, sau đó xóa file tạm.
   - **Tuyệt đối không poll/sleep/tail/read lắt nhắt** trong nhiều turn gây phình context và tốn token của người dùng. Để OpenCode tự chạy độc lập một mạch ở tầng dưới và nhận thẳng kết quả/báo cáo tổng hợp khi xong.
2. **Hậu kiểm với CodeGraph & Git Diff**:
   - Chạy `git diff` để rà soát chi tiết từng dòng OpenCode đã sửa.
   - Dùng **CodeGraph** (`codegraph_explore`) để kiểm tra lại call-graph và symbol topology: xác nhận OpenCode sửa có bị sót nhánh, thiếu điều kiện rẽ nhánh, vi phạm import layering hoặc gây gãy logic ở các module phụ thuộc không.
3. **Chạy test xác nhận**: Chạy `python -m pytest tests/` liên quan.
4. **Vá lỗi tồn đọng & Hoàn thiện (Co-working)**: Claude đích thân hoàn thiện, vá lại các edge case tinh tế hoặc logic còn thiếu trước khi bàn giao cho người dùng.

<!-- claude-reflect: learned rules (saved from the band; edit freely) -->
## Learned rules
- Implement robust key rotation that automatically switches keys on failure rather than returning errors, and ensure core functionality survives agent failures without relying solely on 9router.
- Sau mỗi `git push`, kiểm tra CI ngay bằng `gh run list --branch main --limit 2` (và `gh run view <id> --log-failed` nếu đỏ). Đừng đợi user nhắc — CI đỏ sau push là việc của mình.
- Khi CI fail với `stream_ended_without_finish_reason` (hoặc `empty_stream_response`), kiểm tra fixture mock trước khi sửa code: stream fixture phải yield đúng contract provider thật (frame payload + frame có `finish_reason`), frame trần kiểu `{"ok": True}` sẽ bị drain-and-validate reject đúng luật. Lịch sử: `tests/test_custom_endpoint_stream_circuit_breaker.py` (commit dc756ff).
<!-- end claude-reflect -->

# Bug Logs

Nơi ghi lại các bug đã fix hoặc log ra bug, cách sửa và lý do tại sao sửa như vậy, để sau này tra cứu khi gặp lại.

---

## Bug #1: Agent Block Không Pass Được Qua Translator & Proxy (2026-07-05)

### Mô tả
Khi Claude Code (agent mode) gửi request chứa `agent_use`/`agent_result` blocks, hệ thống trả về lỗi `"Thiếu agent block support"` hoặc Gemini không nhận diện được function response → retry loop → context bloat.

### Tỉ lệ
Không phải 100% — Gemini API đôi khi lenient về functionResponse name mismatch nên thoáng qua được, chỉ fail "tỉ lệ nho nhỏ" như user report.

### Root cause

Có 6 bugs, chia làm 2 phía:

**INPUT SIDE (message_converter.py):**
1. `agent_use` block dùng `agent_type` (VD: `"general-purpose"`) làm function name thay vì `"Agent"` — Gemini không match được với tool declaration tên `"Agent"`.

**OUTPUT SIDE (5 files):**
2-6. Cả 5 file output đều check `name == "Task"` để sinh `agent_use` block, nhưng Gemini trả về tool name `"Agent"` (không phải `"Task"`). Kết quả: `tool_use {name:"Agent"}` được emit ra SSE như tool_use thường, Claude Code không transform nó thành `agent_use` → lỗi.

### Chain đầy đủ

```
Claude Code gửi:           agent_use {agent_type:"general-purpose", prompt:"..."}
  ↓ message_converter.py (BUG 1)
Gemini nhận:               functionResponse {name:"general-purpose", ...}  ← sai!
  ↓ (nếu Gemini vẫn trả lời)
Proxy emit:                tool_use {name:"Agent"} ← đúng
  ↓ proxy_stream.py (BUG 2-6): check "Task" → sai → không nhận ra là agent
Claude Code nhận:          tool_use {name:"Agent"} ← không được transform
  ↓
Lần request sau gửi:       tool_use → agent_use {agent_type:"general-purpose"} ← name vẫn sai
  ... loop
```

### Fix

#### 1. INPUT: `src/logical_HQ_translator/message_converter.py:435`

**Before:** `t_name = block.get("name") or block.get("agent_type") or "Task"`

**After:** `t_name = block.get("name") or ("Agent" if b_type == "agent_use" else "Task")`

**Vì sao:** `agent_type` là metadata nội bộ của Claude Code (VD: `"general-purpose"`), không phải tên tool. Tool declaration Claude Code gửi xuống có tên `"Agent"`. Dùng `"Agent"` làm function name để Gemini match với tool declaration.

#### 2-6. OUTPUT: 5 files check `== "Task"` → `in ("Agent", "Task")`

| File | Dòng |
|------|------|
| `src/api/claude_proxy/handler/proxy_stream.py` | 565 |
| `src/api/claude_proxy/handler/proxy_nonstream.py` | 281 |
| `src/api/claude_proxy/handler/stream_executor.py` | 248 |
| `src/api/claude_proxy/handler/nonstream_executor.py` | 408 |
| `src/api/claude_proxy/stream.py` | 294, 352, 363 |

**Vì sao:** Gemini trả về tool `name: "Agent"` (theo tool declaration trong system prompt). Code cũ chỉ check `"Task"` — di sản từ Claude Code cũ. Giữ `"Task"` làm fallback cho backward compatibility với setup cũ, thêm `"Agent"` cho setup mới.

### Tại sao không fix OpenCode proxy

OpenCode proxy (`opencode_proxy/handler/`) dùng OpenAI-compatible format, không có `agent_use`/`agent_result` blocks. Bug này chỉ ảnh hưởng Claude Code agent mode.

### Tại sao không over-engineer

- Hardcode `"Agent"` thay vì scan tools array — ít code, đủ dùng, tool declaration không đổi giữa các request.
- Không refactor message_converter — chỉ sửa 1 dòng.
- Giữ nguyên fallback `"Task"` — zero risk cho non-agent requests.

### Files changed

```
src/logical_HQ_translator/message_converter.py          | 2 +-
src/api/claude_proxy/handler/proxy_stream.py             | 2 +-
src/api/claude_proxy/handler/proxy_nonstream.py          | 2 +-
src/api/claude_proxy/handler/stream_executor.py          | 2 +-
src/api/claude_proxy/handler/nonstream_executor.py       | 2 +-
src/api/claude_proxy/stream.py                           | 6 +++---
```

---

## Bug #2: Máy Mới Không Khởi Động Được Vì DB Rỗng (2026-10-06)

### Mô tả
Deploy trên một máy chưa từng chạy Router API: app chết ngay lúc khởi động với `sqlite3.OperationalError: no such table: key_status`.

### Root cause
`src/core/router/__init__.py` tạo `APIRouter()` ở **module level**, và constructor đó gọi `get_key_status_db()`. `custom_endpoint_manager` cũng dựng cache ở module level. Cả hai đọc DB **trong lúc import** — trước mọi startup hook của FastAPI. Trên máy có `usage.db` sẵn thì không sao vì bảng đã tồn tại; máy mới thì không có gì để đọc.

Không phải mọi entrypoint đều qua `main.py`, nên sửa ở `main.py` là chưa đủ: bất kỳ `import app` nào cũng phải crash.

### Fix
`src/backend/_db.py` — `conn()` tạo hai bảng đọc lúc import ngay lần mở connection đầu tiên. `main.py` gọi `init_config_tables()` trước khi bind port để lỗi schema nổi ra lúc khởi động chứ không phải trên request đầu tiên.

### Ranh giới dễ vỡ
`CREATE TABLE IF NOT EXISTS` **không** sửa được bảng đã có sai shape. Nên nếu DDL ở `_db.py` lệch với `schema.py`, bản bootstrap chạy trước và thắng; `CREATE` của `schema.py` thành no-op; bất kỳ cột nào không có `ALTER` migration sẽ không bao giờ được tạo. Lần đầu viết sai cột cho `custom_endpoints` và `key_usage` đã đúng vào loại lỗi này. `tests/test_schema_bootstrap.py` so DDL của hai bên để chặn.

---

## Bug #3: Mã Đăng Ký Va Chạm Thành Lỗi 500 (2026-10-06)

### Mô tả
Nút "Cấp mã" của admin thỉnh thoảng trả 500: `sqlite3.IntegrityError: UNIQUE constraint failed: invite_codes.code`.

### Root cause
Mã chỉ 4 chữ số → 10 000 giá trị. `create_invite_db()` xoá mã chưa dùng rồi sinh mã ngẫu nhiên — nhưng mã **đã dùng** được giữ lại làm audit trail. Khi bảng có đủ mã cũ, xác suất trùng là 1/10 000 mỗi lần cấp, và sẽ tăng dần theo thời gian.

Bug này không xuất hiện trên database mới nên không thấy cho tới khi săn flake — nó hiện ra thành một lần build đỏ ngẫu nhiên trong `test_account_auth.py`.

### Fix
Thử tối đa 8 lần trước khi báo lỗi, và lỗi cuối cùng nói rõ nguyên nhân thay vì để `IntegrityError` lọt lên làm 500.

### Test
`tests/test_invite_codes.py` — ép va chạm bằng `patch("secrets.choice")`, kiểm tra retry hoạt động và giới hạn 8 lần.

---

## Bug #4: Dashboard Nuốt Mất Lý Do Lỗi (2026-10-06)

### Mô tả
Mọi lỗi từ endpoint admin hiện thành `HTTP error! status: 404` thay vì thông báo thật.

### Root cause
`frontend-src/src/utils/api.js` đọc `errorData.error`. Nhưng các route admin raise `HTTPException(detail="Account not found")` — FastAPI đặt chuỗi vào `detail`, không phải `error`. Nên nguyên nhân thật bị vứt đi.

### Fix
Đọc cả `error` lẫn `detail`, và parse `detail` dạng JSON string mà FastAPI dùng khi detail là object.

---

## Bug #5: Effort Bị Đảo Ngược Hoặc Bỏ Qua (2026-10-06)

Chi tiết ở [`routing_and_resilience.md`](routing_and_resilience.md) mục 9 và 10. Tóm tắt: `output_config.effort` — field Claude Code thật sự gửi — không được đọc, nên mọi effort đều rơi về `low`; `reasoning_effort: medium` lại tắt thinking; `thinking: disabled` bị `return {}` nuốt mất.

Điểm chung của bug #2–#5: **không cái nào sinh ra lỗi để báo cáo**. Response vẫn hợp lệ, request vẫn 200. Chỉ có hành vi sai. Vì vậy chúng không tự lộ ra ngoài đời thực — cần test so sánh đầu/cuối mới thấy được.

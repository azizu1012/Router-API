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

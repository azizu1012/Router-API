# Router API v2

Proxy Anthropic ↔ OpenAI ↔ Gemini — pool key, rate limiter, circuit breaker, usage dashboard.

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\Activate
pip install -r requirements.txt
# Copy .env.example → .env, fill GEMINI_API_KEY_1..N
python main.py
```

Server auto-kills old instance on same port. Dashboard: http://127.0.0.1:58100/stats

## Admin Console

```bash
python -m src.console.admin_console shell
```

Commands: `create`, `list`, `enable`, `disable`, `rotate-key`, `delete`, `defaults`, `endpoint`, `dashboard`.

## Cấu hình với Claude Code

Claude Code sử dụng giao thức Anthropic Messages API (`/v1/messages`).

### Giao thức qua settings.json (Khuyên dùng)
*   **Đường dẫn file (Windows):** `C:\Users\<Tên_User>\.claude\settings.json`
*   **Đường dẫn file (Unix/macOS):** `~/.claude/settings.json`

Thêm cấu hình `env` vào file `settings.json` như sau:
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:58100",
    "ANTHROPIC_AUTH_TOKEN": "sk-<account-key>"
  }
}
```

### Thiết lập qua biến môi trường Shell
```bash
# Windows (PowerShell)
$env:ANTHROPIC_BASE_URL="http://127.0.0.1:58100"
$env:ANTHROPIC_AUTH_TOKEN="sk-<account-key>"
$env:ANTHROPIC_MODEL="gemini-flash-35"

# Linux / macOS
export ANTHROPIC_BASE_URL="http://127.0.0.1:58100"
export ANTHROPIC_AUTH_TOKEN="sk-<account-key>"
export ANTHROPIC_MODEL="gemini-flash-35"
```
Khởi chạy bằng lệnh: `claude`.

---

## Cấu hình với OpenCode

OpenCode sử dụng giao thức OpenAI-compatible API.

### 1. Qua biến môi trường (Nhanh)
* **Base URL:** `http://127.0.0.1:58100/opencode/v1`
* **API Key:** `sk-<account-key>`

```bash
# Windows (PowerShell)
$env:OPENAI_BASE_URL="http://127.0.0.1:58100/opencode/v1"
$env:OPENAI_API_KEY="sk-<account-key>"

# Linux / macOS
export OPENAI_BASE_URL="http://127.0.0.1:58100/opencode/v1"
export OPENAI_API_KEY="sk-<account-key>"
```

### 2. Qua file opencode.json (Khuyên dùng)
Tạo file `opencode.json` trong project root:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "router": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Router API (Gemini)",
      "options": {
        "baseURL": "http://127.0.0.1:58100/opencode/v1",
        "apiKey": "sk-<account-key>",
        "timeout": 600000,
        "chunkTimeout": 60000
      },
      "models": {
        "gemini-flash": { "name": "Gemini Flash Pool" },
        "gemini-flash-lite": { "name": "Gemini Flash Lite" },
        "gemini-flash-35": { "name": "Gemini Flash 3.5" }
      }
    }
  },
  "model": "router/gemini-flash"
}
```

---

## Tùy chỉnh Model Agent (Sub-agent)

Router API hỗ trợ cấu hình model riêng cho các Agent/Sub-agent. Bạn có thể tùy chỉnh model cho sub-agent thông qua các cách sau:
1. **Qua cấu hình Account:** Thêm trường `subagent_model`, `agent_model`, hoặc `sub_agent_model` vào cấu hình tài khoản trong cơ sở dữ liệu/`accounts.json` (ví dụ: `"subagent_model": "gemini-flash-lite"`).
2. **Qua biến môi trường:** Cấu hình trong file `.env`:
   ```env
   OPENCODE_SUB_AGENT_MODEL=gemini-flash-lite
   SUB_AGENT_MODEL=gemini-flash-lite
   ```
Nếu không cấu hình, mặc định sub-agent của cả OpenCode và Claude Code sẽ tự động fallback về `gemini-flash-lite`.

---

## Cấu hình Custom Endpoint

Router API hỗ trợ custom endpoint (ví dụ: OpenAI-compatible proxy) làm first-class pool member thông qua `pool_assignments`.

### Thêm endpoint qua Admin Console

```bash
python -m src.console.admin_console endpoint add my-provider https://api.example.com/v1
python -m src.console.admin_console endpoint set-model my-provider my-model
python -m src.console.admin_console endpoint assign my-provider pool gemini-flash:flash
```

Endpoint được gán vào pool member qua `pool_assignments` (VD: `gemini-flash` pool → member `flash` = endpoint `my-provider/model`). Nếu model không có trong `MODEL_POOLS`, endpoint sẽ fallback về standalone mode.

Xem `docs/architecture_overview.md` để biết chi tiết luồng xử lý.

---

## Chi tiết Dự án & Kiến trúc

Để biết thêm thông tin chi tiết về các endpoints, sơ đồ cơ sở dữ liệu, các giao thức, công cụ tìm kiếm, cơ chế keepalive, cấu hình thinking và cấu trúc dự án, vui lòng tham khảo các tài liệu sau:
👉 **[architecture_overview.md](./docs/architecture_overview.md)** — Tổng quan toàn bộ kiến trúc & cấu trúc dự án.
👉 **[routing_and_resilience.md](./docs/routing_and_resilience.md)** — Chi tiết cơ chế chống lỗi 429/503, thuật toán Double Random, Jitter và phòng chống nghẽn tải.

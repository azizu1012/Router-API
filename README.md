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

Claude Code sử dụng giao thức Anthropic Messages API (`/v1/messages`). Để cấu hình Claude Code kết nối qua Router API, bạn có thể thực hiện theo một trong hai cách dưới đây:

### Cách 1: Sử dụng file cấu hình `settings.json` (Khuyên dùng)
Bạn có thể cấu hình trực tiếp vào file cấu hình global của Claude Code để không cần thiết lập lại mỗi khi mở terminal mới:
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

### Cách 2: Thiết lập qua biến môi trường Shell
Thiết lập các biến môi trường trước khi khởi động Claude Code:
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
Khởi chạy bằng lệnh: `claude`. Kiểm tra trạng thái kết nối bên trong CLI bằng lệnh `/status`.

## Các Giao Thức Chính

### 1. Giao thức Google GenAI SDK (Pass-through Native)
Dành cho bot hoặc các client sử dụng thư viện chính thức `google-genai` của Google. Giao thức này chuyển tiếp trực tiếp payload gốc của Gemini API mà không qua lớp dịch trung gian của OpenAI.

*   **Endpoint:**
    *   `POST /v1beta/models/{model_id}:generateContent` (Non-stream)
    *   `POST /v1beta/models/{model_id}:streamGenerateContent` (Stream - Server-Sent Events)
    *(Hỗ trợ cả tiền tố `/v1alpha` và `/v1`)*
*   **Cách cấu hình Client SDK (Python):**
    ```python
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key="sk-<account-key>",
        http_options=types.HttpOptions(
            base_url="http://127.0.0.1:58100"
        )
    )

    # Sử dụng Google Search Grounding thông qua tools
    response = client.models.generate_content(
        model="gemini-flash-25",
        contents="Thời tiết hôm nay thế nào?",
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
    )
    ```
*   **Tham số chi tiết:**
    *   `contents`: Danh sách các content block native (`role`, `parts` chứa text hoặc `inlineData` của ảnh).
    *   `systemInstruction`: Chỉ dẫn hệ thống (System prompt).
    *   `generationConfig`: Thiết lập tham số (`temperature`, `topP`, `maxOutputTokens`).
    *   `tools`: Nếu chứa `googleSearch` hoặc `google_search` thì Router tự động kích hoạt Web Grounding Search.

### 2. Giao thức OpenAI Compatible (Translator)
Dành cho các thư viện OpenAI SDK chuẩn. Router sẽ dịch payload OpenAI thành định dạng thích hợp cho Gemini.

*   **Endpoint:** `POST /v1/chat/completions`
*   **Tham số chi tiết:**
    *   `model`: Model alias trong pool (ví dụ: `gemini-flash-lite`, `gemini-flash-25`, `gemini-flash-35`).
    *   `messages`: Mảng hội thoại chuẩn OpenAI (`role`, `content` có thể chứa text hoặc mảng chứa hình ảnh dạng `image_url`).
    *   `web_search`: Boolean (`true` để bật tìm kiếm Grounding/Hybrid Search thủ công trên server).
    *   `max_tokens`/`max_completion_tokens`: Số token output tối đa.
    *   `temperature`, `top_p`.

## Cấu trúc

```
main.py                          # Entry point (auto-kill old instance)
src/
├── api/claude_proxy/            # Anthropic→Gemini proxy (stream + non-stream)
│   ├── handler.py               #   ClaudeProxy: msg convert, pool retry, WebSearch
│   ├── utils.py                 #   SSE helpers, token estimation, tool merge
│   └── stream.py                #   Anthropic SSE chunk conversion
│
├── server/openai_server/        # FastAPI app (OpenAI + Anthropic endpoints)
│   ├── routes.py                #   HTTP routes, auth, stats dashboard
│   ├── handler.py               #   Chat completion logic, pool dispatch
│   └── auth.py                  #   Bearer token + account rate limiter
│
├── core/
│   ├── config_n_logg/           # Config + logging
│   │   ├── config.py            #   RouterApiConfig dataclass (từ .env)
│   │   ├── logger.py            #   stdout + rotating file handler
│   │   └── __init__.py          #   Re-exports
│   ├── router/
│   │   ├── core.py              #   APIRouter: key reserve, freeze, cooldown
│   │   └── pool.py              #   ModelPool: swap logic, failure tracking
│   ├── limits/
│   │   ├── gemini_rate_limiter.py # RPM/TPM/RPD limiter + per-key usage + penalty
│   │   └── account_limiter.py   #   Per-account RPM/TPM/RPD
│   ├── providers/
│   │   ├── gemini_api_manager.py # Gemini genai SDK pipeline + retry
│   │   └── custom_endpoint_manager.py # Custom endpoint CRUD + pool
│   ├── accounts/
│   │   └── account_manager.py   # Account auth facade
│   ├── api_config.py            # Model definitions + pools + sunset
│   ├── preflight.py             # Health check
│   └── usage_logger.py          # Async → SQLite batch flush
│
├── backend/                     # SQLite DB layer
│   ├── _db.py                   #   Shared connection + RLock
│   ├── schema.py                #   DDL + migration from JSON
│   ├── accounts.py              #   Account CRUD
│   ├── endpoints.py             #   Custom endpoint CRUD + pool assignment
│   └── key_status.py            #   Key status + usage atomic ops
│
├── console/                     # CLI admin console
│   ├── admin_console.py         #   Entry: main() + AccountConsole (cmd.Cmd)
│   ├── console_endpoint.py      #   Endpoint wizard: add, pool assign, ping
│   └── console_helpers.py       #   Helpers: print, keypress, interactive selector
│
└── tools/
    └── duckduckgo.py            # WebSearch tool (dùng trong proxy)
```

## Endpoints

| Method | Path | Mô tả |
|--------|------|-------|
| GET | `/stats` | Usage dashboard (Chart.js) |
| GET | `/api/stats` | JSON stats |
| GET | `/health` | Health check |
| GET | `/preflight` | Check keys, models, auth |
| GET | `/account` | Account info + usage |
| GET | `/v1/models` | List models |
| POST | `/v1/chat/completions` | OpenAI chat |
| POST | `/v1/messages` | Anthropic messages |

## Env chính

| Variable | Default | Mô tả |
|----------|---------|-------|
| `GEMINI_API_KEY_1..N` | — | Gemini keys |
| `ROUTER_API_PORT` | `58100` | Server port |
| `ROUTER_API_MAX_RETRIES` | `5` | Max retry |
| `POOL_SWAP_FAILURES` | `2` | Pool failover threshold per model |
| `POOL_MAX_ATTEMPTS` | `6` | Pool max attempts |
| `KEY_429_COOLDOWN_SECONDS` | `15` | Freeze key sau rate-limit |

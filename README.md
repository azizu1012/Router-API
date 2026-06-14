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

### Giao thức qua settings.json (Khuyên dùng)
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

### Thiết lập qua biến môi trường Shell
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

---

## Cấu hình với OpenCode

OpenCode sử dụng giao thức OpenAI-compatible API. Để cấu hình OpenCode chạy qua Router API:
* **Base URL:** `http://127.0.0.1:58100/opencode/v1` (Sử dụng đường dẫn `/opencode` để Router tự động nhận diện chính xác các request từ OpenCode).
* **API Key:** `sk-<account-key>`

Thiết lập các biến môi trường trước khi khởi chạy OpenCode:
```bash
# Windows (PowerShell)
$env:OPENAI_BASE_URL="http://127.0.0.1:58100/opencode/v1"
$env:OPENAI_API_KEY="sk-<account-key>"

# Linux / macOS
export OPENAI_BASE_URL="http://127.0.0.1:58100/opencode/v1"
export OPENAI_API_KEY="sk-<account-key>"
```

---

## Tùy chỉnh Model Agent (Sub-agent)

Router API hỗ trợ cấu hình model riêng cho các Agent/Sub-agent (các tiến trình chạy ngầm quét file, lập kế hoạch, kiểm tra lỗi...) độc lập với model chính được chọn để tối ưu hóa chi phí và tài nguyên (ví dụ: model chat chính dùng `gemini-flash` nhưng sub-agent dùng `gemini-flash-lite`).

Bạn có thể tùy chỉnh model cho sub-agent thông qua các cách sau:
1. **Qua cấu hình Account:** Thêm trường `subagent_model`, `agent_model`, hoặc `sub_agent_model` vào cấu hình tài khoản trong cơ sở dữ liệu/`accounts.json` (ví dụ: `"subagent_model": "gemini-flash-lite"`).
2. **Qua biến môi trường:** Cấu hình trong file `.env`:
   ```env
   OPENCODE_SUB_AGENT_MODEL=gemini-flash-lite
   SUB_AGENT_MODEL=gemini-flash-lite
   ```
Nếu không cấu hình, mặc định sub-agent của cả OpenCode và Claude Code sẽ tự động fallback về `gemini-flash-lite`.

## Các Giao Thức Chính

### 1. Giao thức Google GenAI SDK (Pass-through Native)
Dành cho bot hoặc các client sử dụng thư viện chính thức `google-genai` của Google. Giao thức này chuyển tiếp trực tiếp payload gốc của Gemini API mà không qua lớp dịch trung gian của OpenAI.

*   **Endpoint:**
    *   `POST /v1beta/models/{model_id}:generateContent` (Non-stream)
    *   `POST /v1beta/models/{model_id}:streamGenerateContent` (Stream thực tế - Server-Sent Events)
    *(Hỗ trợ cả tiền tố `/v1alpha` và `/v1`)*
*   **Tính năng mới (v2.1+):**
    *   **Streaming thực tế**: `streamGenerateContent` dùng `generate_content_stream` của Google GenAI SDK, không phải fake chunking.
    *   **Tool calling đầy đủ**: Hỗ trợ `functionCall` và `functionResponse` trong history chuyển tiếp (fix mất dữ liệu reasoning loop).
    *   **Cost tracking chính xác**: Cả streaming & non-streaming log usage bằng `model_id` thực tế → dashboard hiển thị chi phí & tiết kiệm đúng.
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

    # Streaming thực tế
    stream = client.models.generate_content_stream(
        model="gemini-flash-25",
        contents="Viết bài thơ về coding.",
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
    )
    for chunk in stream:
        print(chunk.text, end="", flush=True)
    ```
*   **Tham số chi tiết:**
    *   `contents`: Danh sách các content block native (`role`, `parts` chứa text, `inlineData` ảnh, `functionCall`, `functionResponse`).
    *   `systemInstruction`: Chỉ dẫn hệ thống (System prompt).
    *   `generationConfig`: Thiết lập tham số (`temperature`, `topP`, `maxOutputTokens`).
    *   `tools`: Nếu chứa `googleSearch`/`google_search` → Router tự kích hoạt Web Grounding Search.

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
│   ├── handler/                 #   Orchestrator, pool retry, WebSearch intercept
│   │   ├── proxy.py             #     ClaudeProxy singleton: thinking, retry loop
│   │   ├── proxy_stream.py      #     Streaming call mixer with keepalive
│   │   ├── proxy_nonstream.py   #     Non-streaming call mixer
│   │   ├── stream_executor.py   #     Search status streaming, SSE yield
│   │   ├── pool_stream.py       #     Pool retry wrapper for streaming
│   │   ├── nonstream_executor.py#     WebSearch, tool recursion, thinking
│   │   └── helpers.py           #     Error classification, system status
│   ├── utils/                   #   Utilities
│   │   ├── format_normalizer.py #     StreamingTextNormalizer + XMLThinkingExtractor
│   │   ├── sse_cache_agent.py   #     SSE builders, cache simulation
│   │   ├── message_converter.py #     Claude→OpenAI schema converter
│   │   ├── model_resolver.py    #     Model alias + key concurrency
│   │   └── truncation.py        #     Emergency truncation
│   └── stream.py                #     Anthropic SSE: thinking_delta + signature_delta
│
├── api/opencode_proxy/          # OpenCode→Gemini proxy (OpenAI-compatible)
│   ├── handler/
│   │   ├── proxy.py             #     OpenCodeProxy orchestrator
│   │   ├── stream_executor.py   #     OpenAI streaming + search status
│   │   ├── nonstream_executor.py#     Non-streaming + tool recursion
│   │   ├── search.py            #     Sub-agent web search
│   │   ├── websearch.py         #     Search intent detection
│   │   ├── detection.py         #     Sub-agent override detector
│   │   ├── response.py          #     Response builders + cost
│   │   ├── sse.py               #     OpenAI SSE formatter
│   │   └── error.py             #     Error classification
│   └── sse.py                   #     SSE event builder
│
├── server/                      # HTTP server
│   ├── websocket_manager.py     #   WebSocket connection manager
│   ├── log_watcher.py           #   Async log tail + ring buffer
│   ├── stats_pusher.py          #   Real-time stats pusher
│   ├── openai_server/           #   OpenAI + Anthropic endpoints
│   │   ├── handler.py           #     Chat completion, grounding
│   │   ├── completion_helpers.py#     Response/stream builders
│   │   ├── auth.py              #     Bearer token + rate limiter
│   │   ├── security.py          #     Brute force protection
│   │   └── routes/              #     Route modules
│   │       ├── app_init.py      #       FastAPI app factory + lifespan
│   │       ├── standard_routes.py#      Health, models, MCP
│   │       ├── completions_routes.py#   /v1/chat/completions + /v1/messages
│   │       ├── opencode_routes.py#     /opencode/v1/chat/completions
│   │       ├── dashboard_routes.py#    Stats dashboard HTML + JSON
│   │       ├── ws_routes.py     #       Dashboard WebSocket
│   │       ├── auth_session.py  #       Dashboard JWT
│   │       └── admin/           #       Admin REST API
│   │           ├── accounts.py  #         Account CRUD
│   │           ├── endpoints.py #         Endpoint CRUD
│   │           └── keys.py      #         Gemini key mgmt
│   └── pass_through_server/     #   Native Gemini pass-through
│       └── routes/
│           ├── gemini_routes.py     #   generateContent/streamGenerateContent routes
│           ├── gemini_handlers.py   #   Main pass-through handler with grounding
│           ├── gemini_parsers.py    #   Auth + content/tool parsing
│           └── gemini_streaming.py  #   Streaming + custom endpoint streaming
│
├── core/                        # Core engine
│   ├── config_n_logg/           #   Config + logging
│   │   ├── config.py            #     RouterApiConfig từ .env
│   │   └── logger.py            #     6 rotating handlers + console
│   ├── router/                  #   Key pool & routing
│   │   ├── core/router.py       #     APIRouter: key registry, scoring
│   │   ├── core/key_resolver.py #     Circuit breaker, adaptive cooldown
│   │   └── pool.py              #     ModelPool: failover state machine
│   ├── limits/                  #   Rate limiters
│   │   ├── gemini_rate_limiter.py#    Per-model RPM/TPM/RPD sliding window
│   │   └── account_limiter/     #     Per-account limits
│   │       ├── limiter.py       #       Sliding window
│   │       ├── capacity.py      #       Pool capacity by tier
│   │       └── effective_limits.py#     Limits after sharing
│   ├── providers/               #   LLM backends
│   │   ├── gemini/              #     Gemini SDK pipeline
│   │   │   ├── manager.py       #       Semaphore, retry
│   │   │   ├── caller.py        #       SDK caller + safety
│   │   │   ├── pool.py          #       ClientPool health
│   │   │   ├── error.py         #       Error classification
│   │   │   ├── thinking_config.py#      ThinkingConfig builder
│   │   │   └── utils.py         #       Error handling, tools, backoff
│   │   ├── gemini_api_manager.py#     Thin facade → gemini/
│   │   ├── gemini_api_helpers.py#     Error classification mixin
│   │   ├── litellm_wrapper.py   #     LiteLLM acompletion wrapper
│   │   ├── search_manager.py    #     Search intent + grounding
│   │   └── custom_endpoint_manager.py# Non-Gemini endpoints CRUD
│   ├── accounts/account_manager.py # Account auth facade (10s cache)
│   ├── api_config.py            #   Model pools + sunset
│   ├── preflight.py             #   Health diagnostics
│   └── usage_logger.py          #   Async → SQLite batch flush
│
├── backend/                     # SQLite DB layer
│   ├── _db.py                   #   Shared connection + WAL + RLock
│   ├── schema.py                #   DDL + migration
│   ├── accounts.py              #   Account CRUD
│   ├── endpoints.py             #   Custom endpoint CRUD
│   ├── key_status.py            #   Key circuit breaker
│   └── model_prices.py          #   Cost lookup
│
├── console/                     # CLI admin
│   ├── admin_console.py         #   Main shell (cmd.Cmd)
│   ├── console_endpoint.py      #   Endpoint wizard
│   └── console_helpers.py       #   Helpers + selectors
│
└── tools/                       # Web search engine
    ├── duckduckgo.py            #   AdvancedSearchManager
    ├── ddg_ranking.py           #   Consensus ranking
    ├── ddg_utils.py             #   URL normalization + dedup
    └── ddg_data.py              #   Topic data + cache
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
| POST | `/v1/chat/completions` | OpenAI chat (stream → OpenCodeProxy) |
| POST | `/v1/messages` | Anthropic messages |
| POST | `/opencode/v1/chat/completions` | OpenCode proxy routing |
| POST | `/v1/{version}/models/{model}:generateContent` | Gemini pass-through (non-stream) |
| POST | `/v1/{version}/models/{model}:streamGenerateContent` | Gemini pass-through (stream) |
| POST | `/dashboard/admin/keys/add` | Add Gemini key |
| POST | `/dashboard/admin/accounts/*` | Account CRUD |
| POST | `/dashboard/admin/endpoints/*` | Endpoint CRUD |

## Thinking / Reasoning

Router API hỗ trợ `thinking` (reasoning) cho tất cả Gemini models. Mặc định **tự động bật thinking** với level phù hợp theo từng dòng model:
- **V3 models** (`gemini-3.*`): `thinking_level = "medium"`
- **V2.5 models** (`gemini-2.5.*`): `thinking_budget = -1` (dynamic)

### Cách dùng với OpenAI-compatible clients

Gửi kèm các tham số sau trong request body:

| Tham số | Kiểu | Mô tả |
|---------|------|-------|
| `thinking_level` | string | `"low"`, `"medium"`, `"high"` — chỉ dùng cho V3 |
| `thinking_budget` | int | Số token tối đa cho thinking (`-1` = dynamic) |
| `include_thoughts` | bool | `true` để nhận nội dung thinking trong response |

Ví dụ:

```bash
curl http://127.0.0.1:58100/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-<key>" \
  -d '{
    "model": "gemini-flash-35",
    "messages": [{"role": "user", "content": "Giải thích quantum computing"}],
    "thinking_level": "high",
    "include_thoughts": true
  }'
```

Response sẽ có thêm trường `reasoning_content` trong `message`:

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "Quantum computing...",
      "reasoning_content": "Let me think about this step by step..."
    }
  }]
}
```

### Cách dùng với Anthropic-style clients

Gửi kèm field `thinking` trong body:

```json
{
  "thinking": {
    "type": "enabled",
    "budget_tokens": 16000
  }
}
```

Response sẽ có content block `type: "thinking"`.

### Custom endpoints

Proxy tự động forward mọi tham số thinking (bao gồm `enableThinking` của LM Studio) đến custom endpoint backend qua `extra_body`.

## Env chính

| Variable | Default | Mô tả |
|----------|---------|-------|
| `GEMINI_API_KEY_1..N` | — | Gemini keys |
| `ROUTER_API_PORT` | `58100` | Server port |
| `ROUTER_API_MAX_RETRIES` | `5` | Max retry |
| `POOL_SWAP_FAILURES` | `2` | Pool failover threshold per model |
| `POOL_MAX_ATTEMPTS` | `6` | Pool max attempts |
| `KEY_429_COOLDOWN_SECONDS` | `15` | Freeze key sau rate-limit |

# Router API

<div align="center">

**Anthropic · OpenAI · Gemini → một gateway bền bỉ.**

Xoay vòng key, rate limiting, circuit breaker, custom endpoint, MCP search và dashboard trực tiếp — dành cho các coding-agent session chạy dài.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Giao thức](https://img.shields.io/badge/giao%20thức-Anthropic%20%7C%20OpenAI%20%7C%20Gemini-0f766e.svg)](#cấu-hình-client)
[![Dashboard](https://img.shields.io/badge/dashboard-live-7c3aed.svg)](#dashboard)

<img src="docs/assets/router-api-architecture.svg" alt="Kiến trúc Router API" width="920" />

**[English](README.md)** · [Tiếng Việt](README_VN.md)

</div>

---

## Tóm tắt nhanh

| Khả năng | Bạn nhận được gì |
|---|---|
| **Ba giao thức** | Claude Code, OpenCode, Cline, Gemini và MCP cùng dùng một gateway |
| **Định tuyến bền bỉ** | Retry, cooldown thích ứng, xoay key, swap model và circuit breaker |
| **Cô lập account** | RPM, TPM, RPD, concurrency và auth token riêng cho từng account |
| **Nhiều provider** | Gemini pool và custom endpoint OpenAI-compatible là member hạng nhất |
| **Quan sát vận hành** | Dashboard trực tiếp, trace request, usage, health probe và log |
| **Search không tốn quota** | DuckDuckGo nội bộ, không tiêu token quota model Gemini |

> **Phù hợp nhất:** gateway local hoặc single-worker cho developer muốn agent tiếp tục chạy khi key, model hoặc endpoint tạm thời gặp lỗi.

## Vấn đề

Chạy coding agent trên một API key duy nhất thì rất dễ vỡ. Dính 429 giữa chừng task, một key chết là cả session đứng, và bạn không biết hôm qua token thực sự được model nào phục vụ.

Router API đặt trước **một pool API key Gemini** và **một pool các model thay thế nhau**, rồi giấu tất cả sau ba giao thức tương thích client:

- **Anthropic Messages API** — Claude Code nối vào không sửa gì
- **OpenAI Chat Completions** — OpenCode, Cline, bất kỳ thứ gì
- **Gemini native** — pass-through cho `generateContent` / `streamGenerateContent`

Khi một key bị rate limit, router cho nó nghỉ và chuyển sang key khác. Khi một model trong pool bắt đầu lỗi, router swap member. Client của bạn vẫn nhận về response bình thường.

---

## Cài đặt nhanh

```bash
git clone https://github.com/azizu1012/Router-API.git
cd Router-API

python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1

pip install -r requirements.txt
cp .env.example .env             # rồi điền GEMINI_API_KEY_1..N
python main.py
```

Dashboard: **http://127.0.0.1:58100/stats**

API reference: **http://127.0.0.1:58100/api/help**.

`main.py` tự kill process cũ đang chiếm port trước khi start, nên chạy lại nhiều lần cũng an toàn.

### Tạo account

```bash
python -m src.console.admin_console create coder
python -m src.console.admin_console list --show-keys
```

Account là lớp auth đặt trước key pool — mỗi account có hạn mức RPM/TPM/RPD riêng, nên một người dùng không thể cạn pool của tất cả.

---

## Cấu hình client

### Endpoint khuyến nghị

| Loại client | Base URL | Credential |
|---|---|---|
| Claude Code / Anthropic | `http://127.0.0.1:58100` | `ANTHROPIC_AUTH_TOKEN` |
| OpenCode / OpenAI-compatible | `http://127.0.0.1:58100/opencode/v1` | `OPENAI_API_KEY` |
| Gemini native | `http://127.0.0.1:58100` | bearer token của account |
| MCP client | `http://127.0.0.1:58100/mcp` | `Authorization: Bearer ...` |

### Claude Code (Anthropic Messages API)

Qua `settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:58100",
    "ANTHROPIC_AUTH_TOKEN": "sk-<account-key>"
  }
}
```

Hoặc bằng shell:

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:58100"
export ANTHROPIC_AUTH_TOKEN="sk-<account-key>"
export ANTHROPIC_MODEL="gemini-flash-35"
claude
```

### OpenCode (OpenAI-compatible)

```bash
export OPENAI_BASE_URL="http://127.0.0.1:58100/opencode/v1"
export OPENAI_API_KEY="sk-<account-key>"
```

Hoặc `opencode.json`:

```json
{
  "provider": {
    "router": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Router API",
      "options": {
        "baseURL": "http://127.0.0.1:58100/opencode/v1",
        "apiKey": "sk-<account-key>",
        "timeout": 600000,
        "chunkTimeout": 60000
      },
      "models": {
        "gemini-flash": { "name": "Gemini Flash Pool" },
        "gemini-flash-lite": { "name": "Gemini Flash Lite Pool" }
      }
    }
  },
  "model": "router/gemini-flash"
}
```

---

## Định tuyến hoạt động thế nào

```
Client ──► Proxy layer      chỉ chuyển đổi format, không logic key/pool
        ──► PoolManager     vòng lặp retry, phân loại lỗi, swap member
        ──► APIRouter       chọn model + key
        ──► KeyResolver     circuit breaker, adaptive cooldown, Double Random
        ──► GeminiRateLimiter   sliding-window RPM/TPM cho từng key
        ──► GeminiFacade / Custom endpoint
```

Proxy cố tình "ngây thơ": chỉ dịch giao thức và ủy quyền phần còn lại. Toàn bộ logic chống lỗi nằm trong `PoolManager`, nên sửa ở đó là áp dụng cho mọi giao thức client cùng lúc.

### Model pool ảo

Bạn request `gemini-flash`. Đằng sau nó là một tập model thay thế nhau, có thứ tự ưu tiên:

| Pool | Thành viên | Ghi chú |
|---|---|---|
| `gemini-flash` | `3.8` · `3.7` · `3.6` · `3.5` · `3.0` · `2.5` | 3.7 bị giới hạn capacity phía Google nên RPM bị bóp |
| `gemini-flash-lite` | `3.5-lite` · `3.1` · `2.5` | tier rẻ hơn, dùng cho sub-agent |

Mỗi member có hạn mức riêng. `ModelPool` cho phép mỗi member xử lý **một request tại một thời điểm**, nên một model chậm không thể bóp chết các model khác. Sau `POOL_SWAP_FAILURES` lỗi liên tiếp, member đó bị loại và router chuyển sang cái tiếp theo.

Alias theo quy ước `gemini-flash-<version>` → `gemini-<version>-flash`. Ghi đè bất kỳ model backing nào qua env (xem `.env.example`).

### Cơ chế chống lỗi

- **Phân loại lỗi tạm thời vs vĩnh viễn** — `rate_limit` / `unavailable` / `timeout` nghỉ ngắn rồi retry; `invalid_key` / `permission_denied` đóng băng key một tiếng.
- **Double Random** — chọn key ngẫu nhiên trong 50% khỏe nhất, và delay retry có jitter ±20%. Thiếu cả hai thì request đồng thời dồn vào một key rồi 429 dây chuyền.
- **Additive sliding window** — đếm RPM/TPM trong deque dưới `asyncio.Lock`, nên bước kiểm tra và cập nhật là atomic.
- **Extreme mode** — sau nhiều lỗi liên tiếp, quota siết còn 70% và chỉ dùng key hoàn toàn rảnh, như một van an toàn cố ý hạ nhiệt.

Custom endpoint (bất kỳ provider OpenAI-compatible nào) là thành viên pool first-class qua `pool_assignments`, không phải kênh phụ.

---

## Web search

Ba cách, tất cả đều tuỳ chọn:

| Cách | Endpoint | Dùng quota Gemini? |
|---|---|---|
| Client-side search | `POST /v1/search` | không |
| Server-side tool loop | `web_search: true` trong completion | không |
| Hosted tool của Responses | `tools: [{"type": "web_search"}]` trên `/v1/responses` | không |

Đường DuckDuckGo không chạm vào Gemini key nào, nên agent loop nhiều search gần như không tốn hạn mức key.

Engine nào phục vụ một lần search **do dialect quyết định, không phải do toggle trên dashboard**:

- Mọi dialect đều dùng search Router-owned qua DuckDuckGo.
- `search_engine` vẫn được chấp nhận để tương thích wire, nhưng không gọi model search trung gian.

App tự điều phối vòng lặt riêng thì nên dùng `POST /v1/search`: không tốn quota Gemini, và
không phải tốn thêm một lượt gọi model để đọc lại toàn bộ conversation. Tool trong chat tồn
tại cho client không tự điều phối được.

---

## Endpoints

| Method | Path | Giao thức |
|---|---|---|
| `POST` | `/v1/messages`, `/messages` | Anthropic Messages (stream + non-stream) |
| `POST` | `/v1/messages/count_tokens` | Anthropic |
| `POST` | `/v1/chat/completions` | OpenAI |
| `POST` | `/opencode/v1/chat/completions` | OpenAI |
| `POST` | `/v1/responses` | OpenAI Responses — `tools`, `instructions`, `stream` đều được map |
| `POST` | `/v1/models/{id}:generateContent` | Gemini native (pass-through) |
| `GET` | `/v1/models` | OpenAI **và** Anthropic schema superset |
| `POST` | `/v1/search`, `/search` | Router |
| `POST`| `/mcp` | MCP Server — tool `search_web`, Bearer token |
| `GET` | `/dashboard/*` | Dashboard API |
| `WS` | `/dashboard/ws` | Live dashboard stream |
| `GET` | `/health`, `/preflight` | Ops |
| `GET` | `/api/help` | API reference — endpoint + curl thật, cũng là 1 tab trên dashboard |

---

## Dashboard

Mở **http://127.0.0.1:58100/stats** sau khi start để xem:

- request trực tiếp và usage theo model;
- health của key, cooldown, lỗi tạm thời và capacity pool;
- budget account, custom endpoint, alias và model assignment;
- ví dụ API được sinh từ các route đang bật.

Dashboard là tuỳ chọn; gateway và toàn bộ giao thức client vẫn chạy bình thường nếu không mở nó.

---

## Cấu hình

Tất cả đều qua env; `.env.example` là file tham chiếu. Các biến quan trọng nhất:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `GEMINI_API_KEY_1..N` | — | Key pool của bạn. Tự động nhận diện. |
| `ROUTER_API_PORT` | `58100` | Cổng lắng nghe |
| `ROUTER_API_MAX_OUTPUT_TOKENS` | `8192` | Trần output cứng |
| `POOL_SWAP_FAILURES` | `5` | Số lỗi tạm thời trước khi swap member |
| `KEY_429_COOLDOWN_SECONDS` | `15` | Thời gian nghỉ sau rate limit |
| `KEY_INVALID_COOLDOWN_SECONDS` | `3600` | Đóng băng sau khi key invalid |
| `*_RPM` / `*_TPM` / `*_RPD` | theo model | Hạn mức từng model |
| `MODEL_CONTEXT_LENGTH` | `185000` | Trần context an toàn báo cho client (Flash/Lite bảo vệ 250k TPM) |
| `GEMMA_CONTEXT_LENGTH` | `14000` | Trần context an toàn cho Gemma 4 (bảo vệ 16k TPM) |

> Thực tế các key dùng chung quota của một project — thêm nhiều key vào cùng project không nhân gấp throughput. Giá trị RPM trong config đã phản ánh điều đó.

---

## Custom endpoint

Bất kỳ provider OpenAI-compatible nào cũng gia nhập pool được:

```bash
python -m src.console.admin_console endpoint add my-provider https://api.example.com/v1
python -m src.console.admin_console endpoint set-model my-provider my-model
python -m src.console.admin_console endpoint assign my-provider pool gemini-flash:flash
```

Model của endpoint trở thành member thật của pool, có slot và concurrency riêng. Nếu model không nằm trong `MODEL_POOLS`, nó chạy standalone (không qua acquire/release).

---

## MCP Server (Model Context Protocol)

Router API tích hợp sẵn một **MCP Server** chuẩn Streamable HTTP tại `/mcp`, cho phép các Agent (Claude Code, OpenCode, Cursor, Windsurf, Trae, Cline, LibreChat...) trực tiếp gọi công cụ tìm kiếm web thời gian thực:

- **Công cụ:** `search_web(query: string)` — Tìm kiếm web, tin tức, dữ liệu thực tế và trích dẫn nguồn.
- **Tiết kiệm 100% Quota:** Chạy trực tiếp qua DuckDuckGo scraper nội bộ (`execute_hybrid_search` in-process), hoàn toàn **không tiêu tốn bất kỳ token nào của key Gemini**.
- **Xác thực an toàn:** Sử dụng Bearer token của Account (`sk-...`) hoặc Master Key.
- **Tùy chọn `mcp_only`:** Có thể cấu hình account chuyên dụng chỉ được phép gọi MCP Server và khóa hoàn toàn các cổng chat model.

### Cấu hình nhanh cho các Client:

#### 1. Claude Code CLI
```bash
claude mcp add --transport http router-search http://127.0.0.1:58100/mcp \
  --header "Authorization: Bearer <TOKEN>"
```

#### 2. OpenCode (`opencode.json`)
```json
{
  "mcp": {
    "router-search": {
      "type": "remote",
      "url": "http://127.0.0.1:58100/mcp",
      "enabled": true,
      "headers": {
        "Authorization": "Bearer <TOKEN>"
      }
    }
  }
}
```

#### 3. Cursor / Windsurf / Cline / Roo / Trae / LibreChat (`mcp.json`)
```json
{
  "mcpServers": {
    "router-search": {
      "type": "http",
      "url": "http://127.0.0.1:58100/mcp",
      "headers": {
        "Authorization": "Bearer <TOKEN>"
      }
    }
  }
}
```

> **Lưu ý triển khai Reverse Proxy / Custom Domain:**  
> MCP SDK mặc định bật cơ chế bảo vệ DNS-rebinding (`421 Misdirected Request`). Khi đưa server ra ngoài Internet qua domain (ví dụ `https://my-domain.com/mcp`), bạn cần thêm biến sau vào `.env`:
> ```env
> MCP_ALLOWED_HOSTS=my-domain.com,127.0.0.1:*,localhost:*
> ```
> Xem chi tiết hướng dẫn và chẩn đoán tại **[docs/mcp_server.md](docs/mcp_server.md)**.

---

## Triển khai

Xem **[DEPLOY_DOMAIN.md](DEPLOY_DOMAIN.md)** để đặt sau domain với Caddy hoặc Nginx — HTTPS, reverse proxy, systemd unit.

Chỉ chạy **một worker**. SQLite locking là process-local, nhiều Uvicorn worker sẽ dính `database is locked`.

---

## Phát triển

```bash
pytest                                    # toàn bộ suite
pytest tests/test_anthropic_protocol.py   # tuân thủ giao thức
```

CI chạy một workflow advisory là [secret scanner](https://github.com/azizu1012/Router-API/actions/workflows/secret_scan.yml). Workflow không chặn build; test suite chạy local — xem `docs/ci_workflow.md`.

Tài liệu chi tiết trong `docs/`:

- **[architecture_overview.md](docs/architecture_overview.md)** — thành phần, luồng dữ liệu, trade-off thiết kế
- **[routing_and_resilience.md](docs/routing_and_resilience.md)** — phân loại lỗi, Double Random, extreme mode
- **[mcp_server.md](docs/mcp_server.md)** — cấu hình `/mcp` cho Claude Code / OpenCode / Cursor, và lỗi 421
- **[frontend_dashboard.md](docs/frontend_dashboard.md)** — kiến trúc dashboard và quản lý state
- **[bug-logs.md](docs/bug-logs.md)** — các bug đã chẩn đoán và lý do sửa

---

## Đóng góp

Issue và PR đều welcome. Đóng góp hữu ích nên đi kèm cơ chế lỗi cụ thể, không phải khớp pattern chung chung.

Nếu bạn sửa request parsing hoặc response formatting, hãy thêm test. `tests/test_anthropic_protocol.py` và `tests/test_anthropic_integration.py` tồn tại chính vì các bug giao thức rất âm thầm — response vẫn parse được, chỉ là sai.

---

## Giấy phép

[MIT](LICENSE) © 2026 azizu1012

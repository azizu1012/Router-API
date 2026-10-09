# Router API

**Anthropic · OpenAI · Gemini → một cổng duy nhất.** Xoay vòng key, giới hạn tốc độ, circuit breaker và dashboard trực tiếp. Cắm vào Claude Code, OpenCode, hay bất kỳ thứ nào nói được Anthropic Messages API.

**[English](README.md)** · [Tiếng Việt](README_VN.md)

[![Trustabl Agent Scanner](https://github.com/azizu1012/Router-API/actions/workflows/trustabl.yml/badge.svg)](https://github.com/azizu1012/Router-API/actions/workflows/trustabl.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

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
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env             # rồi điền GEMINI_API_KEY_1..N
python main.py
```

Dashboard: **http://127.0.0.1:58100/stats**

`main.py` tự kill process cũ đang chiếm port trước khi start, nên chạy lại nhiều lần cũng an toàn.

### Tạo account

```bash
python -m src.console.admin_console create coder
python -m src.console.admin_console list --show-keys
```

Account là lớp auth đặt trước key pool — mỗi account có hạn mức RPM/TPM/RPD riêng, nên một người dùng không thể cạn pool của tất cả.

---

## Cấu hình client

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
| Hosted tool của Responses | `tools: [{"type": "web_search"}]` trên `/v1/responses` | có |

Đường DuckDuckGo không chạm vào Gemini key nào, nên agent loop nhiều search gần như không tốn hạn mức key.

Engine nào phục vụ một lần search **do dialect quyết định, không phải do toggle trên dashboard**:

- **Client Responses** gửi `tools: [{"type": "web_search"}]` đang hỏi đúng năng lực hosted,
  nên nó nhận Google grounding trước, DuckDuckGo phía sau — tức `auto`.
- **Mọi thứ khác** trên các đường chat vẫn nằm ở DuckDuckGo.
- `search_engine` ghi rõ trong body ghi đè cả hai.

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
| `MODEL_CONTEXT_LENGTH` | `220000` | Trần context dùng để tính pool |

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

## Triển khai

Xem **[DEPLOY_DOMAIN.md](DEPLOY_DOMAIN.md)** để đặt sau domain với Caddy hoặc Nginx — HTTPS, reverse proxy, systemd unit.

Chỉ chạy **một worker**. SQLite locking là process-local, nhiều Uvicorn worker sẽ dính `database is locked`.

---

## Phát triển

```bash
pytest                                    # toàn bộ suite
pytest tests/test_anthropic_protocol.py   # tuân thủ giao thức
```

CI chạy hai workflow advisory: [secret scanner](https://github.com/azizu1012/Router-API/actions/workflows/secret_scan.yml) và [Trustabl agent scanner](https://github.com/azizu1012/Router-API/actions/workflows/trustabl.yml). Cả hai không chặn build; test suite chạy local — xem `docs/ci_workflow.md`.

Tài liệu chi tiết trong `docs/`:

- **[architecture_overview.md](docs/architecture_overview.md)** — thành phần, luồng dữ liệu, trade-off thiết kế
- **[routing_and_resilience.md](docs/routing_and_resilience.md)** — phân loại lỗi, Double Random, extreme mode
- **[frontend_dashboard.md](docs/frontend_dashboard.md)** — kiến trúc dashboard và quản lý state
- **[bug-logs.md](docs/bug-logs.md)** — các bug đã chẩn đoán và lý do sửa

---

## Đóng góp

Issue và PR đều welcome. PR thêm CI của Trustabl là ví dụ tốt về một đóng góp hữu ích: phát hiện đi kèm cơ chế lỗi cụ thể, không phải khớp pattern chung chung.

Nếu bạn sửa request parsing hoặc response formatting, hãy thêm test. `tests/test_anthropic_protocol.py` và `tests/test_anthropic_integration.py` tồn tại chính vì các bug giao thức rất âm thầm — response vẫn parse được, chỉ là sai.

---

## Giấy phép

[MIT](LICENSE) © 2026 azizu1012

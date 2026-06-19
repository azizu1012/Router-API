# Router API v2 — Project Snapshot

<!-- AI-READABLE — STRUCTURED FOR LLM PARSING -->
<!-- Updated: 2026-06-20 | Python 3.13 | win32 -->

---

## PROJECT TREE

```text
d:\AI_Projects\router_api/
├── .env                          # Gemini keys + model config
├── .env.example                  # Template for env file config
├── README.md                     # Quick start + client config
├── project_snapshot.md           # Chi tiết kiến trúc dự án (file này)
├── DEPLOY_DOMAIN.md              # Caddy và Nginx reverse proxy deployment guide
├── AGENTS.md                     # OpenCode agent task management instructions
├── CLAUDE.md                     # Claude Code developer instructions
├── opencode.json                 # OpenCode configuration
├── requirements.txt              # Project Python dependencies
├── banned-keys.txt               # Key tracking (legacy, không còn dùng cho logic chính)
│
├── main.py                       # Uvicorn startup script với auto port-freeing
│
├── usage.db                      # SQLite config DB (accounts, endpoints, key_status, key_penalties)
├── usage_logs.db                 # SQLite telemetry DB for token tracking
│
├── logs/                         # Rotating file logs (daily auto-clean)
│
└── src/                          # 115 Python files
    ├── api/
    │   ├── claude_proxy/         # Anthropic→Gemini proxy
    │   │   ├── stream.py         #   Anthropic SSE converter: thinking_delta + signature_delta
    │   │   └── handler/
    │   │       ├── proxy.py              # ClaudeProxy singleton (26 lines) — pure format, delegates to PoolManager
    │   │       ├── proxy_stream.py       # Streaming call mixer với keepalive
    │   │       ├── proxy_nonstream.py    # Non-streaming call mixer
    │   │       ├── stream_executor.py    # WebSearch intercept + SSE streaming loop
    │   │       ├── nonstream_executor.py # WebSearch intercept + thinking extraction
    │   │       ├── compaction.py         # Context compaction gate
    │   │       └── helpers.py            # Error messages + system status
    │   │
    │   └── opencode_proxy/       # OpenCode→Gemini proxy (OpenAI-compatible)
    │       ├── sse.py            #   SSE event builder
    │       └── handler/
    │           ├── proxy.py              # OpenCodeProxy (204 lines) — pure format, delegates to PoolManager
    │           ├── stream_executor.py    # SSE format, keepalive, XML thinking, WebSearch progress
    │           ├── nonstream_executor.py # Non-streaming với tool recursion
    │           ├── search.py             # Sub-agent web search execution
    │           ├── websearch.py          # Search intent detection & injection
    │           ├── detection.py          # Sub-agent override detector
    │           ├── response.py           # Response builders + cost estimation
    │           ├── sse.py                # OpenAI SSE chunk formatter
    │           └── error.py              # Error classification
    │
    ├── logical_HQ_translator/    # Shared converters & utilities giữa các proxy
    │   ├── __init__.py           #   Re-exports: _resolve_model, _retry_delay, StreamingTextNormalizer, XMLThinkingExtractor, ...
    │   ├── format_normalizer.py  #   StreamingTextNormalizer + XMLThinkingExtractor
    │   ├── sse_cache_agent.py    #   Cache simulator, sub-agent detection, SSE helpers
    │   ├── message_converter.py  #   Claude→OpenAI schema converter
    │   ├── model_resolver.py     #   Model alias resolution + key concurrency
    │   ├── truncation.py         #   Emergency message truncation
    │   └── rtk.py                #   Tool output filter/formatter (git diff, status, ls, grep) cho AI agent
    │
    ├── core/
    │   ├── api_config.py         #   AVAILABLE_MODELS, MODEL_POOLS, MODEL_PRIORITY — định nghĩa toàn bộ model
    │   ├── preflight.py          #   Port diagnostics
    │   ├── usage_logger.py       #   Async telemetry batch flusher → usage_logs.db
    │   ├── pool_manager.py       #   PoolManager — TRUNG TÂM: pool loop, key rotation, quota, retry (503 lines)
    │   ├── config_n_logg/
    │   │   ├── config.py         #     RouterApiConfig dataclass from .env
    │   │   └── logger.py         #     6 rotating file handlers + console
    │   ├── accounts/
    │   │   └── account_manager.py #    Account CRUD facade với 10s cache
    │   ├── limits/
    │   │   ├── gemini_rate_limiter.py # Per-model RPM/TPM/RPD sliding window + penalty system
    │   │   └── account_limiter/
    │   │       ├── limiter.py         # Per-account sliding window
    │   │       ├── capacity.py        # Pool capacity by tier
    │   │       └── effective_limits.py# Effective limits after sharing
    │   ├── providers/
    │   │   ├── gemini_facade.py       # FACADE CHÍNH (353 lines): GenAI SDK hoặc Custom endpoint → OpenAI-compatible output
    │   │   ├── gemini_format.py       # OpenAI messages → Gemini native format (contents, systemInstruction)
    │   │   ├── gemini_response.py     # Gemini response → OpenAI-compatible output
    │   │   ├── gemini_thinking.py     # Thinking config builder cho Gemini (V3 vs V2)
    │   │   ├── gemini_api_manager.py  # Thin re-export → gemini/ sub-package
    │   │   ├── gemini_api_helpers.py  # Error classification mixin
    │   │   ├── custom_endpoint_manager.py # Non-Gemini endpoints CRUD + pool + health
    │   │   ├── custom_endpoint_client.py  # HTTP client gọi custom endpoint (OpenAI SDK)
    │   │   ├── custom_endpoint_genai_adapter.py # Adapter GenAI format → custom endpoint
    │   │   ├── search_manager.py      # Search intent + Google grounding
    │   │   ├── genai_types.py         # Re-exports google.genai types
    │   │   └── gemini/
    │   │       ├── manager.py         # GeminiAPIManager: SDK pipeline, semaphore, client pool
    │   │       ├── caller.py          # Gemini SDK caller với safety settings
    │   │       ├── pool.py            # ClientPool với health tracking
    │   │       ├── error.py           # Pure error classification functions
    │   │       ├── thinking_config.py # Builds ThinkingConfig cho Gemini API
    │   │       └── utils.py           # Extracted helpers: error handling, tools, backoff
    │   └── router/
    │       ├── pool.py           #     ModelPool failure state machine
    │       └── core/
    │           ├── router.py         # Singleton APIRouter: key registry, scoring, pool selection
    │           └── key_resolver.py   # Circuit breaker, adaptive cooldown
    │
    ├── backend/                  # SQLite DB layer
    │   ├── _db.py                #   Shared connection + WAL + RLock
    │   ├── schema.py             #   DDL + migration (6 bảng chính)
    │   ├── accounts.py           #   Account CRUD
    │   ├── endpoints.py          #   Custom endpoint CRUD
    │   ├── key_status.py         #   Key circuit breaker + penalty atomic ops
    │   └── model_prices.py       #   Model price lookup
    │
    ├── console/                  # CLI admin
    │   ├── admin_console.py      #   Main shell (cmd.Cmd)
    │   ├── console_endpoint.py   #   Endpoint wizard
    │   └── console_helpers.py    #   Helpers + selectors
    │
    ├── server/
    │   ├── websocket_manager.py  #   WebSocket connection manager
    │   ├── log_watcher.py        #   Async log tail + ring buffer
    │   ├── stats_pusher.py       #   Real-time stats pusher via WebSocket
    │   ├── openai_server/
    │   │   ├── auth.py               # Bearer token + account rate limiter + reaper
    │   │   ├── security.py           # Rate limit + brute force protection
    │   │   └── routes/
    │   │       ├── app_init.py        # FastAPI app factory + lifespan + .env watcher
    │   │       ├── standard_routes.py # Health, models, MCP endpoints
    │   │       ├── completions_routes.py # /v1/chat/completions + /v1/messages
    │   │       ├── opencode_routes.py # /opencode/v1/chat/completions
    │   │       ├── dashboard_routes.py # Stats dashboard HTML + JSON APIs
    │   │       ├── ws_routes.py       # Dashboard WebSocket
    │   │       ├── auth_session.py    # Dashboard JWT sessions
    │   │       └── admin/
    │   │           ├── accounts.py    # Admin REST: account CRUD
    │   │           ├── endpoints.py   # Admin REST: endpoints CRUD
    │   │           ├── keys.py        # Admin REST: Gemini key mgmt
    │   │           ├── settings.py    # Admin REST: server settings
    │   │           └── helpers.py     # Shared .env helpers
    │   └── pass_through_server/
    │       └── routes/
    │           ├── gemini_routes.py     # Native Gemini routes
    │           ├── gemini_handlers.py   # Main pass-through handler với grounding
    │           ├── gemini_parsers.py    # Auth + content/tool parsing
    │           └── gemini_streaming.py  # Streaming + custom endpoint streaming
    │
    └── tools/                    # Web search engine
        ├── duckduckgo.py         #   AdvancedSearchManager: DuckDuckGo + crawling + cache
        ├── ddg_ranking.py        #   Consensus ranking + topic classification
        ├── ddg_utils.py          #   URL normalization + dedup
        └── ddg_data.py           #   Topic data + cache
```

---

## FILE METRICS (top files theo số dòng)

| File | Lines | Role |
|------|-------|------|
| `logical_HQ_translator/rtk.py` | 577 | Tool output filter cho AI agent (git diff, status, grep, ls) |
| `core/pool_manager.py` | 503 | PoolManager: pool loop, key rotation, quota check, retry |
| `api/opencode_proxy/handler/stream_executor.py` | 495 | OpenCode streaming: SSE format, keepalive, WebSearch, thinking |
| `server/openai_server/routes/dashboard_routes.py` | 446 | Dashboard login + stats HTML/JSON APIs |
| `core/router/core/router.py` | 391 | APIRouter: key registry, scoring, pool selection |
| `core/providers/custom_endpoint_manager.py` | 388 | Custom endpoint CRUD + pool + health |
| `tools/duckduckgo.py` | 380 | WebSearch engine: DuckDuckGo, crawling, cache |
| `server/openai_server/auth.py` | 375 | Bearer token auth + account limiter + reaper |
| `api/claude_proxy/handler/nonstream_executor.py` | 366 | Non-stream: WebSearch intercept, thinking extraction |
| `core/limits/gemini_rate_limiter.py` | 365 | Per-model RPM/TPM/RPD sliding window + penalty system |
| `server/openai_server/routes/completions_routes.py` | 360 | /v1/chat/completions + /v1/messages routes |
| `core/providers/gemini_facade.py` | 353 | Facade: GenAI SDK hoặc Custom endpoint → OpenAI output |
| `core/router/core/key_resolver.py` | 352 | Circuit breaker, adaptive cooldown, key caching |
| `console/admin_console.py` | 345 | Main CLI shell (cmd.Cmd) |
| `logical_HQ_translator/format_normalizer.py` | 343 | StreamingTextNormalizer + XMLThinkingExtractor |
| `api/claude_proxy/stream.py` | 339 | Anthropic SSE: thinking_delta + signature_delta |
| `core/providers/search_manager.py` | 328 | Search intent + Google grounding + hybrid search |
| `logical_HQ_translator/sse_cache_agent.py` | 327 | Cache simulator, sub-agent detection, SSE helpers |
| `core/providers/gemini/manager.py` | 324 | GeminiAPIManager: SDK pipeline, client pool, semaphore |
| `core/limits/account_limiter/capacity.py` | 295 | Pool capacity by tier calculations |
| `api/claude_proxy/handler/stream_executor.py` | 294 | Streaming: WebSearch intercept, SSE yield |
| `api/opencode_proxy/handler/nonstream_executor.py` | 293 | Non-streaming với tool recursion |
| `backend/key_status.py` | 282 | Key circuit breaker + penalty atomic ops |
| `core/providers/custom_endpoint_client.py` | 281 | HTTP client gọi custom endpoint (OpenAI SDK) |
| `server/pass_through_server/routes/gemini_handlers.py` | 273 | Main pass-through handler với grounding |
| `backend/schema.py` | 269 | DDL definitions + migrations |
| `core/usage_logger.py` | 269 | Async telemetry batch flusher |
| `tools/ddg_ranking.py` | 259 | Consensus ranking, topic classification |
| `logical_HQ_translator/message_converter.py` | 251 | Claude→OpenAI schema converter |
| `core/limits/account_limiter/effective_limits.py` | 240 | Effective limits after pool sharing |
| `api/claude_proxy/handler/proxy_stream.py` | 212 | Streaming call mixer với keepalive |
| `api/opencode_proxy/handler/proxy.py` | 204 | OpenCodeProxy: pure format converter, delegates pool logic |
| `tools/ddg_utils.py` | 197 | URL normalization, dedup, page crawling |
| `core/providers/gemini_format.py` | 192 | OpenAI messages → Gemini native format |
| `api/opencode_proxy/handler/search.py` | 178 | OpenCode web search via Gemini sub-agent |
| `core/config_n_logg/config.py` | 163 | RouterApiConfig dataclass from .env |
| `core/providers/gemini/error.py` | 153 | Pure error classification functions |
| `core/providers/gemini_api_helpers.py` | 149 | Error classification mixin + retry helpers |
| `api/opencode_proxy/handler/detection.py` | 148 | Sub-agent keyword detection |
| `api/opencode_proxy/handler/websearch.py` | 146 | Search intent detection & injection |
| `api/claude_proxy/handler/proxy_nonstream.py` | 145 | Non-streaming call mixer |
| `logical_HQ_translator/model_resolver.py` | 144 | Model alias resolution + key concurrency |
| `core/providers/gemini/caller.py` | 136 | Gemini SDK caller với safety settings |
| `core/providers/gemini_response.py` | 136 | Gemini response → OpenAI-compatible output |
| `core/providers/custom_endpoint_genai_adapter.py` | 129 | Adapter GenAI format → custom endpoint |
| `console/console_endpoint.py` | 128 | Interactive endpoint wizard |
| `api/opencode_proxy/handler/error.py` | 123 | Error classification |
| `backend/accounts.py` | 118 | Account CRUD |
| `server/openai_server/routes/app_init.py` | 114 | FastAPI app factory + lifespan |
| `core/providers/gemini/utils.py` | 112 | Extracted helpers: error handling, tools, backoff |
| `core/providers/gemini/pool.py` | 110 | ClientPool với health tracking |
| `server/stats_pusher.py` | 102 | Real-time stats pusher via WebSocket |

---

## ENTRY POINT: main.py

| Line | Function | Description |
|------|----------|-------------|
| 36 | `_free_port` | Kill process đang dùng PORT bằng PowerShell / fuser |
| 94 | `main` | Register models, kiểm tra sunset, chạy uvicorn |

---

## SERVER LAYER

**App:** `FastAPI(title="Router API v2", version="2.0.0")` trong `routes/app_init.py`

### Middleware & Events
| Line | File | Description |
|------|------|-------------|
| 15 | `app_init.py` | CORS headers middleware + OPTIONS handler |
| 84 | `app_init.py` | Startup: DB setup, log flusher, .env watcher |

### HTTP Routes
| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| GET | `/` | `standard_routes.root` | System status |
| GET | `/health` | `standard_routes.health` | Core model + key counts |
| GET | `/preflight` | `standard_routes.preflight` | Port diagnostics |
| GET | `/v1/models` | `standard_routes.list_models` | Active model list |
| GET | `/account` | `standard_routes.current_account` | API user metrics |
| GET | `/stats` | `dashboard_routes.stats_page` | Admin dashboard HTML |
| GET | `/api/stats` | `dashboard_routes.json_stats` | Dashboard JSON |
| GET | `/dashboard/me` | `dashboard_routes.dashboard_me` | Account info + pool quota |
| GET | `/dashboard/penalties` | `dashboard_routes.dashboard_penalties` | Active key penalties |
| GET | `/api/model-pools` | `dashboard_routes.api_model_pools` | Pool list |
| GET | `/api/model-pools-detail` | `dashboard_routes.get_model_pools_api` | Pool detail + rate limiter values |
| POST | `/dashboard/login` | `dashboard_routes.dashboard_login` | Dashboard auth |
| POST | `/v1/chat/completions` | `completions_routes.chat_completions` | OpenAI completions |
| POST | `/v1/messages` | `completions_routes.anthropic_messages` | Anthropic messages |
| POST | `/opencode/v1/chat/completions` | `opencode_routes.opencode_chat_completions` | OpenCode proxy |
| POST | `/v1/{version}/models/{model}:generateContent` | `gemini_routes` | Gemini pass-through |
| POST | `/v1/{version}/models/{model}:streamGenerateContent` | `gemini_routes` | Gemini streaming |
| POST | `/dashboard/admin/keys/add` | `admin/keys.add_key` | Add Gemini key |
| POST | `/dashboard/admin/keys/delete` | `admin/keys.delete_key` | Delete Gemini key |
| POST | `/dashboard/admin/accounts/*` | `admin/accounts.*` | Account CRUD |
| POST | `/dashboard/admin/endpoints/*` | `admin/endpoints.*` | Endpoint CRUD |

---

## PROXY LAYER

### Kiến trúc tổng quan (sau refactor)

```
Client Request
      │
      ▼
ClaudeProxy / OpenCodeProxy
  (pure format converter — KHÔNG có pool/retry logic)
      │
      ▼
PoolManager (core/pool_manager.py)
  (TRUNG TÂM: pool loop, key rotation, quota, retry, error handling)
      │
      ├── Gemini key pool → gemini_facade.acompletion()
      │       ├── PATH 1 (GenAI SDK): gemini_format → SDK call → gemini_response
      │       └── PATH 2 (Custom endpoint): custom_endpoint_client → OpenAI HTTP
      │
      └── Custom endpoint pool → endpoint_manager
```

### Claude Proxy (`src/api/claude_proxy/`)

**Singleton:** `claude_proxy = ClaudeProxy()` trong `handler/proxy.py` (26 lines)

ClaudeProxy chỉ còn 26 dòng — toàn bộ retry/pool/quota đã được chuyển sang `PoolManager`.

#### SSE Streaming Flow với Thinking
```
Client → ClaudeProxy.stream() → PoolManager.call_stream()
    → gemini_facade → GenAI SDK stream chunks
    → _process_anthropic_stream (stream.py)
        ├── reasoning_content → thinking_delta SSE
        ├── content → XMLThinkingExtractor → text/thinking blocks
        └── finish_reason → close blocks → signature_delta → content_block_stop
    → Anthropic SSE events to client
```

### OpenCode Proxy (`src/api/opencode_proxy/`)

**Singleton:** `opencode_proxy = OpenCodeProxy()` trong `handler/proxy.py` (204 lines)

OpenCodeProxy delegates pool/retry sang PoolManager. Chỉ xử lý: message prep, web search injection, response formatting.

#### stream_executor.py — SSE-only module
- SSE chunk formatting
- Keepalive pings (asyncio.shield + wait_for 4s)
- XML thinking extraction
- WebSearch tool execution + progress reporting
- Usage logging sau khi stream hoàn thành

---

## DB SCHEMA: usage.db

### Bảng chính

**accounts** — API user accounts
| Column | Type | Default | Description |
|--------|------|---------|-------------|
| account_id | TEXT PK | — | UUID |
| name | TEXT UNIQUE | — | Account name |
| auth_key | TEXT | — | Bearer token (sk-...) |
| enabled | INTEGER | 1 | 0 = disabled |
| tier | TEXT | 'free' | free/premium/admin |
| rpm | INTEGER | 300 | Requests per minute |
| tpm | INTEGER | 6000000 | Tokens per minute |
| rpd | INTEGER | 20000 | Requests per day |
| web_search_enabled | INTEGER | 0 | Web search mặc định |
| search_engine | TEXT | 'auto' | auto/google_grounding/duckduckgo/disabled |
| created_at | INTEGER | — | Unix timestamp |
| updated_at | INTEGER | — | Unix timestamp |

**custom_endpoints** — Non-Gemini backends
| Column | Type | Default | Description |
|--------|------|---------|-------------|
| name | TEXT PK | — | Endpoint alias |
| base_url | TEXT | — | API base URL |
| auth_key | TEXT | — | API key |
| enabled | INTEGER | 1 | 0 = disabled |
| models | TEXT | '[]' | JSON list of model names |
| disabled_models | TEXT | '[]' | Disabled model list |
| enabled_models | TEXT | '[]' | Whitelisted models |
| account_id | TEXT | '' | Assigned account |
| fallback | INTEGER | 0 | Use as pool fallback |
| pool_assignments | TEXT | '{}' | JSON pool→model mapping |
| updated_at | TEXT | — | ISO timestamp |

**key_status** — Per-key circuit breaker
| Column | Type | Default | Description |
|--------|------|---------|-------------|
| key | TEXT PK | — | Gemini API key |
| enabled | INTEGER | 1 | 0 = disabled |
| usage | INTEGER | 0 | Total usage count |
| active_requests | INTEGER | 0 | Concurrent request count |
| frozen_until | REAL | 0.0 | Top-level cooldown expiry |
| consecutive_failures | INTEGER | 0 | Failure streak counter |
| last_success | REAL | 0.0 | Last success timestamp |
| date | TEXT | '' | Today's date string |
| today | INTEGER | 0 | Today's request count |
| per_model | TEXT | '{}' | Per-model freeze/failures JSON |
| tier | TEXT | 'free' | Key tier assignment |

**key_penalties** — Client-side score penalty (hiển thị trên /stats/penalties)
| Column | Type | Description |
|--------|------|-------------|
| pkey | TEXT PK | `{api_key}::{model_id}` |
| api_key | TEXT | Gemini API key |
| model_id | TEXT | Model ID bị penalty |
| reason | TEXT | Lý do (rate_limit, project_quota_429, ...) |
| expires | REAL | Unix timestamp hết hạn |
| score_reduction | INTEGER | Số điểm bị trừ khỏi priority score |

> **Lưu ý quan trọng:** `key_penalties` là bảng **client-side penalty** — không phải ban thực từ Google. Khi Google trả về 429/quota, Router API tự áp penalty giảm priority score của key để tránh gọi lại key đó trong thời gian cooldown. Xóa khỏi DB chỉ đủ nếu server được restart; nếu không cần restart server phải gọi `load_penalties_from_db()` để sync vào RAM.

**key_usage** — Per-key daily usage tracking
| Column | Type | Description |
|--------|------|-------------|
| key | TEXT PK | Gemini API key |
| data | TEXT | JSON: today count, per-model today, total |

**model_prices** — Cost lookup table
| Column | Type | Description |
|--------|------|-------------|
| model_name | TEXT PK | Model alias hoặc model_id |
| input_rate_per_1k | REAL | Giá per 1K input tokens |
| output_rate_per_1k | REAL | Giá per 1K output tokens |
| response_model_name | TEXT | Canonical model name |

---

## MODEL CONFIG (core/api_config.py)

### AVAILABLE_MODELS
| Alias | model_id (default) | RPM | RPD | Notes |
|-------|--------------------|-----|-----|-------|
| `gemini-flash-35` | `gemini-3.5-flash` | 2 | 50 | hidden — pool member |
| `gemini-flash-30` | `gemini-3-flash-preview` | 2 | 50 | hidden — pool member |
| `gemini-flash-25` | `gemini-2.5-flash` | 5 | 20 | hidden — pool member |
| `gemini-flash` | `gemini-flash-pool` | sum | sum | PUBLIC — pool aggregate |
| `gemini-flash-lite` | `gemini-3.1-flash-lite` | 6 | 500+ | PUBLIC — lite pool |
| `gemini-flash-25-lite` | `gemini-2.5-flash-lite` | 3 | 20 | hidden — lite pool member |

### MODEL_POOLS
```python
"gemini-flash": members = ["gemini-flash-35", "gemini-flash-30", "gemini-flash-25"]
"gemini-flash-lite": members = ["gemini-flash-lite", "gemini-flash-25-lite"]
```

---

## KEY ARCHITECTURE DECISIONS

1. **PoolManager là trung tâm duy nhất:** Tất cả pool loop, key rotation, quota check, retry, error handling đều ở `core/pool_manager.py`. ClaudeProxy và OpenCodeProxy chỉ là format converter, delegate toàn bộ sang PoolManager.

2. **gemini_facade.py — 2 path:** PATH 1: key pool → GenAI SDK (`gemini_format` → SDK → `gemini_response`). PATH 2: custom endpoint → OpenAI HTTP (`custom_endpoint_client`). PoolManager gọi `gemini_facade.acompletion()` mà không cần biết path nào.

3. **rtk.py — Tool output filter:** Module mới trong `logical_HQ_translator/`, lọc và rút gọn output từ git diff, git status, ls, grep cho AI agent dễ đọc hơn.

4. **Client-side Penalty System (không phải ban thật từ Google):** Khi Google trả về 429 `project_quota_429`, Router API gọi `apply_error_penalty(key, reason, model_id)` → ghi vào `_score_penalties` (RAM) và `key_penalties` (DB). Penalty giảm priority score của key. Khi score ≤ 0, key bị skip. Xóa khỏi DB không đủ nếu server đang chạy vì in-memory vẫn còn penalty — cần restart server hoặc có endpoint unban.

5. **In-memory vs DB cho key status:** `router._key_status` là in-memory dict, được load từ DB lúc startup và `refresh_keys()`. Sửa DB trực tiếp không ảnh hưởng in-memory cho đến khi `router.refresh_keys()` được gọi. Tuy nhiên `refresh_keys()` giữ lại `existing` entry nếu key đã có trong memory — cần restart để reset hoàn toàn.

6. **Search Status Streaming:** Proxy intercept WebSearch call, chạy DuckDuckGo + page crawler, stream progress emoji (`🔍 Searching...`, `📡 Querying...`, `📄 Reading results...`, `⚡ Synthesizing...`) dưới dạng SSE text deltas mỗi 3s.

7. **asyncio.shield bắt buộc cho keepalive:** `wait_for(asyncio.shield(it.__anext__()), timeout=4.0)` — shield ngăn generator bị cancel khi timeout, tránh response rỗng.

8. **Sub-agent check TRƯỚC body thinking:** `is_sub_agent_body` phải ở đầu hàm thinking config builder, trước khi đọc `body.get("thinking")`.

9. **Pool name (không phải member alias) cho reserve_key:** `reserve_key("gemini-flash")` → pool path với aggregate RPM. `reserve_key("gemini-flash-35")` → non-pool path với single-key RPM → quota mismatch.

10. **429/503 unified soft handling:** Không freeze key, không record failure — chỉ backoff + retry. Freeze và failure record chỉ cho lỗi vĩnh viễn: `bad_request`, `billing_error`, `invalid_key`.

11. **Per-Tier Semaphore:** admin=6, premium=4, free=2 concurrent requests — independent semaphores.

12. **Env watcher auto-reload:** `app_init.py` watch `.env` mtime mỗi 3s. Khi thay đổi → `reload_config()` + `register_keys_in_db()` + `router.refresh_keys()` + `api_manager.refresh_pool_size()` + `clear_rate_limiters()`.

13. **Sunset logic:** `SUNSET_DATE_25 = 2026-10-16` — sau ngày đó, `gemini-flash-25` và `gemini-flash-25-lite` bị block khỏi pool.

14. **Custom endpoint tool strip:** Khi backend là custom endpoint, `WebSearch`/`WebFetch` tools bị strip khỏi request để tránh tool calls không mong muốn.

---

## THINKING CONFIG

### OpenCode proxy (`opencode_proxy/handler/proxy.py:_resolve_thinking_config`)
- Lite models → `{}` (thinking disabled)
- V3 models (`gemini-3.*`): dùng `thinking_level` (low/medium/high) + `include_thoughts`
- V2.x models: dùng `thinking_budget` (token count) + `include_thoughts`
- Sub-agent → `{}` (thinking disabled)
- Mặc định main agent: V3 = `low/medium`, V2 = budget 8192/16384

### Claude proxy
- Thinking auto-enable cho main agent, tắt cho sub-agent
- `type: "adaptive"` → budget 4096 (flash) hoặc 8192 (pro)
- Strip `display` field từ Claude Code request

---

## WEB SEARCH ENGINE

Router API có 2 backend search: Google Grounding (Gemini built-in) và DuckDuckGo (tự crawl + ranking).

| `search_engine` | Hành vi |
|-----------------|---------|
| `auto` | Google Grounding trước, fail → DuckDuckGo (mặc định) |
| `google_grounding` | Chỉ Google Grounding |
| `duckduckgo` | Chỉ DuckDuckGo |
| `disabled` | Tắt web search hoàn toàn |

**Ưu tiên:** `request body` → `account config` → `auto`

---

## ENDPOINTS TABLE

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
| POST | `/opencode/v1/chat/completions` | OpenCode proxy routing |
| POST | `/v1/{version}/models/{model}:generateContent` | Gemini pass-through (non-stream) |
| POST | `/v1/{version}/models/{model}:streamGenerateContent` | Gemini pass-through (stream) |
| POST | `/dashboard/admin/keys/add` | Add Gemini key |
| POST | `/dashboard/admin/keys/delete` | Delete Gemini key |
| POST | `/dashboard/admin/accounts/*` | Account CRUD |
| POST | `/dashboard/admin/endpoints/*` | Endpoint CRUD |

---

## ENV VARIABLES CHÍNH

| Variable | Default | Mô tả |
|----------|---------|-------|
| `GEMINI_API_KEY_1..N` | — | Gemini keys |
| `ROUTER_API_PORT` | `58100` | Server port |
| `ROUTER_API_MAX_RETRIES` | `5` | Max retry standalone mode |
| `POOL_SWAP_FAILURES` | `5` | Transient errors trước khi swap pool member |
| `POOL_MAX_ATTEMPTS` | `15` | Max pool loop attempts |
| `KEY_429_COOLDOWN_SECONDS` | `15` | Freeze key sau rate-limit |
| `KEY_INVALID_COOLDOWN_SECONDS` | `3600` | Freeze key sau invalid/billing error |
| `FREE_KEY_END` | — | Index cuối của free tier keys |
| `PREMIUM_KEY_END` | — | Index cuối của premium tier keys |
| `GEMINI_FLASH_35_MODEL` | `gemini-3.5-flash` | Backing model ID cho flash-35 |
| `GEMINI_FLASH_30_MODEL` | `gemini-3-flash-preview` | Backing model ID cho flash-30 |
| `GEMINI_FLASH_25_MODEL` | `gemini-2.5-flash` | Backing model ID cho flash-25 |
| `OPENCODE_SUB_AGENT_MODEL` | `gemini-flash-lite` | Sub-agent model override |
| `SUB_AGENT_MODEL` | `gemini-flash-lite` | Sub-agent model override (fallback) |
| `MODEL_CONTEXT_LENGTH` | `220000` | Max context length cho pool capacity calc |

---

## FILE & FUNCTION MAPPING (CRITICAL)

| File | Function | Vai trò |
|------|----------|---------|
| `core/pool_manager.py` | `PoolManager.call_nonstream` | Non-stream pool loop entry |
| `core/pool_manager.py` | `PoolManager.call_stream` | Stream pool loop entry |
| `core/providers/gemini_facade.py` | `acompletion` | Unified facade: SDK vs custom endpoint |
| `core/providers/gemini_format.py` | `build_gemini_body` | OpenAI → Gemini native format |
| `core/providers/gemini_response.py` | `parse_gemini_*` | Gemini → OpenAI-compatible output |
| `core/router/core/router.py` | `router.resolve_pool` | Tìm pool config cho model alias |
| `core/router/core/router.py` | `router.freeze_key` | Freeze key in-memory + DB |
| `core/router/core/key_resolver.py` | `reserve_key` | Pool path: iterate members, chọn key |
| `core/limits/gemini_rate_limiter.py` | `apply_error_penalty` | Ghi penalty vào RAM + DB |
| `core/limits/gemini_rate_limiter.py` | `load_penalties_from_db` | Load penalties từ DB vào RAM (startup) |
| `core/limits/gemini_rate_limiter.py` | `get_key_priority` | Tính priority score cho key |
| `server/openai_server/routes/dashboard_routes.py` | `get_model_pools_api` | Pool detail API với rate limiter values |
| `server/stats_pusher.py` | `StatsPusher._snapshot` | Push real-time RPM/TPM per model alias |
| `logical_HQ_translator/rtk.py` | `filter_git_diff/status/ls/grep` | Tool output filter cho AI agent |

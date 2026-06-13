# Router API v2 — Project Snapshot

<!-- AI-READABLE — STRUCTURED FOR LLM PARSING -->
<!-- Generated: 2026-06-14 | Python 3.13 | win32 -->

---

## PROJECT TREE

```text
d:\AI_Projects\router_api/
├── .env                          # 63 Gemini keys + model config
├── .env.example                  # Template for env file config
├── README.md                     # Project configuration and usage documentation
├── DEPLOY_DOMAIN.md              # Caddy and Nginx reverse proxy deployment guide
├── AGENTS.md                     # OpenCode agent task management instructions
├── CLAUDE.md                     # Claude Code developer instructions
├── opencode.json                 # OpenCode configuration
├── requirements.txt              # Project Python dependencies
├── banned-keys.txt               # Cooldown and banned key tracking
│
├── main.py                       # Uvicorn startup script with auto port-freeing
│
├── usage.db                      # SQLite config DB (accounts, endpoints, status)
├── usage_logs.db                 # SQLite telemetry DB for token tracking
│
├── logs/                         # Rotating file logs (daily auto-clean)
│
├── src/                          # 100 Python files, ~14,056 lines total
│   ├── api/                      #   ~4,200 lines — proxy layers
│   │   ├── claude_proxy/         #     Anthropic→Gemini proxy (stream + non-stream)
│   │   │   ├── stream.py         #       Anthropic SSE converter: thinking_delta + signature_delta
│   │   │   ├── handler/
│   │   │   │   ├── proxy.py              # ClaudeProxy singleton: LiteLLM kwargs, thinking, retry
│   │   │   │   ├── proxy_stream.py       # Streaming call mixer with keepalive pings
│   │   │   │   ├── proxy_nonstream.py    # Non-streaming call mixer
│   │   │   │   ├── stream_executor.py    # WebSearch intercept + search status streaming
│   │   │   │   ├── nonstream_executor.py # WebSearch intercept & thinking extraction
│   │   │   │   ├── compaction.py         # Context compaction gate
│   │   │   │   └── helpers.py            # Error messages & system status
│   │   │   └── utils/
│   │   │       ├── format_normalizer.py  # StreamingTextNormalizer + XMLThinkingExtractor
│   │   │       ├── sse_cache_agent.py    # Cache simulator, sub-agent detection, SSE helpers
│   │   │       ├── message_converter.py  # Claude→OpenAI schema converter
│   │   │       ├── model_resolver.py     # Model alias resolution & key concurrency
│   │   │       └── truncation.py         # Emergency message truncation
│   │   │
│   │   └── opencode_proxy/       #     OpenCode→Gemini proxy (OpenAI-compatible)
│   │       ├── sse.py            #       SSE event builder for OpenCode streaming
│   │       └── handler/
│   │           ├── proxy.py              # Orchestrator: model resolution, web search, retry
│   │           ├── stream_executor.py    # Core streaming: thinking, search status, pool retry
│   │           ├── nonstream_executor.py # Non-streaming with tool recursion
│   │           ├── search.py             # Sub-agent web search execution
│   │           ├── websearch.py          # Search intent detection & injection
│   │           ├── detection.py          # Sub-agent override detector
│   │           ├── response.py           # Response builders & cost estimation
│   │           ├── sse.py                # OpenAI SSE chunk formatter
│   │           └── error.py              # Error classification & pool retry
│   │
│   ├── backend/                   #   ~1,000 lines — SQLite DB layer
│   │   ├── _db.py                #     Shared connection + WAL + RLock
│   │   ├── schema.py             #     DDL + migration from JSON
│   │   ├── accounts.py           #     Account CRUD
│   │   ├── endpoints.py          #     Custom endpoint CRUD
│   │   ├── key_status.py         #     Key status + usage atomic ops
│   │   └── model_prices.py       #     Model price lookup
│   │
│   ├── console/                   #   ~637 lines — CLI admin
│   │   ├── admin_console.py      #     Main CLI shell (account/endpoint/key commands)
│   │   ├── console_endpoint.py   #     Interactive endpoint wizard
│   │   └── console_helpers.py    #     Formatting, prompts, selectors
│   │
│   ├── core/                      #   ~3,700 lines — routing, limits, providers
│   │   ├── api_config.py         #     Model pools & sunset logic
│   │   ├── preflight.py          #     Health check diagnostics
│   │   ├── usage_logger.py       #     Async telemetry batch flusher
│   │   ├── auth/
│   │   ├── config_n_logg/
│   │   │   ├── config.py         #     RouterApiConfig dataclass
│   │   │   └── logger.py         #     6 rotating file handlers + console
│   │   ├── accounts/
│   │   │   └── account_manager.py #    Account CRUD facade with 10s cache
│   │   ├── limits/
│   │   │   ├── gemini_rate_limiter.py # Per-model RPM/TPM/RPD sliding window
│   │   │   └── account_limiter/
│   │   │       ├── limiter.py         # Per-account sliding window
│   │   │       ├── capacity.py        # Pool capacity by tier
│   │   │       └── effective_limits.py# Effective limits after sharing
│   │   ├── providers/
│   │   │   ├── litellm_wrapper.py     # LiteLLM acompletion + token_counter
│   │   │   ├── gemini_api_manager.py  # Thin facade → gemini/ sub-package
│   │   │   ├── gemini_api_helpers.py  # Error classification mixin
│   │   │   ├── genai_types.py         # Re-exports google.genai types
│   │   │   ├── custom_endpoint_manager.py # Non-Gemini endpoints CRUD + pool
│   │   │   ├── search_manager.py      # Search intent + Google grounding
│   │   │   └── gemini/
│   │   │       ├── manager.py         # GeminiAPIManager: SDK pipeline, semaphore
│   │   │       ├── caller.py          # Gemini SDK caller with safety settings
│   │   │       ├── pool.py            # ClientPool with health tracking
│   │   │       ├── error.py           # Pure error classification functions
│   │   │       └── thinking_config.py # Builds ThinkingConfig for Gemini API
│   │   └── router/
│   │       ├── pool.py           #     ModelPool failure state machine
│   │       └── core/
│   │           ├── router.py         # Singleton APIRouter: key registry, scoring
│   │           └── key_resolver.py   # Circuit breaker, adaptive cooldown
│   │
│   ├── server/                     #   ~2,700 lines — HTTP server
│   │   ├── openai_server/
│   │   │   ├── handler.py        #     OpenAI chat completions executor + thinking
│   │   │   ├── auth.py           #     Bearer token + account rate limiter
│   │   │   ├── security.py       #     Rate limit + brute force protection
│   │   │   └── routes/
│   │   │       ├── app_init.py          # FastAPI app factory + lifespan
│   │   │       ├── standard_routes.py   # Health, models, MCP endpoints
│   │   │       ├── completions_routes.py# /v1/chat/completions + /v1/messages
│   │   │       ├── opencode_routes.py   # /opencode/v1/chat/completions
│   │   │       ├── dashboard_routes.py  # Stats dashboard HTML + JSON
│   │   │       ├── auth_session.py      # Dashboard JWT sessions
│   │   │       └── admin/
│   │   │           ├── accounts.py      # Admin REST: account CRUD
│   │   │           ├── endpoints.py     # Admin REST: endpoints CRUD
│   │   │           ├── keys.py          # Admin REST: Gemini key mgmt
│   │   │           └── helpers.py       # Shared .env helpers
│   │   │
│   │   └── pass_through_server/
│   │       └── routes/
│   │           └── gemini_routes.py # Native Gemini API proxy (647 lines)
│   │
│   └── tools/
│       ├── duckduckgo.py         #   WebSearch engine + page crawler (379 lines)
│       ├── ddg_ranking.py         #   Consensus ranking + topic classification (258 lines)
│       ├── ddg_utils.py           #   URL normalization + dedup (196 lines)
│       └── ddg_data.py            #   Cache + topic data (159 lines)
```

---

## FILE METRICS

| File | Lines | Role |
|------|-------|------|
| `api/opencode_proxy/handler/stream_executor.py` | 673 | OpenCode streaming: search status, thinking, pool retry |
| `server/pass_through_server/routes/gemini_routes.py` | 647 | Native Gemini API pass-through proxy |
| `server/openai_server/handler.py` | 449 | OpenAI chat completions executor, grounding |
| `core/providers/gemini/manager.py` | 425 | GeminiAPIManager: SDK pipeline, semaphore |
| `api/claude_proxy/handler/stream_executor.py` | 418 | Streaming: search status, WebSearch intercept, SSE |
| `tools/duckduckgo.py` | 379 | WebSearch engine: DuckDuckGo, crawling, cache |
| `core/router/core/key_resolver.py` | 348 | Circuit breaker, adaptive cooldown, key caching |
| `server/openai_server/routes/dashboard_routes.py` | 346 | Dashboard login + stats HTML/JSON |
| `console/admin_console.py` | 345 | Interactive CLI admin shell |
| `api/claude_proxy/utils/format_normalizer.py` | 343 | StreamingTextNormalizer + XMLThinkingExtractor |
| `server/openai_server/routes/completions_routes.py` | 334 | /v1/chat/completions + /v1/messages routes |
| `core/providers/custom_endpoint_manager.py` | 313 | Custom endpoint CRUD + pool + health |
| `api/claude_proxy/stream.py` | 311 | Anthropic SSE: thinking_delta + signature_delta |
| `core/providers/search_manager.py` | 310 | Search intent + Google grounding + hybrid search |
| `api/claude_proxy/handler/proxy_stream.py` | 308 | Streaming call mixer with keepalive pings |
| `core/limits/gemini_rate_limiter.py` | 308 | Per-model RPM/TPM/RPD sliding window |
| `api/claude_proxy/utils/sse_cache_agent.py` | 306 | Cache simulator, sub-agent detection, SSE helpers |
| `api/opencode_proxy/handler/proxy.py` | 300 | OpenCode proxy orchestrator |
| `core/router/core/router.py` | 295 | APIRouter: key registry, scoring, pool selection |
| `core/limits/account_limiter/capacity.py` | 295 | Pool capacity by tier calculations |
| `api/claude_proxy/handler/proxy.py` | 289 | ClaudeProxy singleton: thinking, retry loop |
| `backend/key_status.py` | 274 | Key circuit breaker, freeze/cooldown DB ops |
| `backend/schema.py` | 264 | DDL definitions + JSON→SQLite migration |
| `tools/ddg_ranking.py` | 258 | Consensus ranking, topic classification |
| `core/limits/account_limiter/effective_limits.py` | 240 | Effective limits after pool sharing |
| `api/claude_proxy/handler/proxy_nonstream.py` | 239 | Non-streaming call mixer |
| `api/claude_proxy/handler/nonstream_executor.py` | 213 | Non-stream: WebSearch, thinking extraction |
| `tools/ddg_utils.py` | 196 | URL normalization, dedup, page crawling |
| `server/openai_server/auth.py` | 195 | Bearer token auth + account limiter + reaper |
| `backend/endpoints.py` | 169 | Custom endpoint CRUD |
| `core/usage_logger.py` | 167 | Async telemetry batch flusher |
| `api/opencode_proxy/handler/search.py` | 165 | OpenCode web search via Gemini sub-agent |
| `console/console_helpers.py` | 164 | CLI formatting, prompts, selectors |
| `core/config_n_logg/config.py` | 163 | RouterApiConfig dataclass from .env |
| `tools/ddg_data.py` | 159 | Search cache + topic classification data |
| `api/opencode_proxy/handler/nonstream_executor.py` | 158 | Non-stream OpenCode with tool recursion |
| `core/limits/account_limiter/limiter.py` | 154 | Per-account sliding window limiter |
| `core/providers/gemini/error.py` | 153 | Pure error classification functions |
| `core/providers/gemini_api_helpers.py` | 148 | Error classification mixin + retry helpers |
| `api/opencode_proxy/handler/detection.py` | 148 | Sub-agent keyword detection |
| `server/openai_server/routes/admin/accounts.py` | 142 | Admin REST: account CRUD |
| `core/providers/gemini/caller.py` | 136 | Gemini SDK caller with safety settings |
| `api/claude_proxy/utils/model_resolver.py` | 132 | Model alias resolution + key concurrency |
| `api/opencode_proxy/handler/websearch.py` | 128 | Search intent detection & injection |
| `console/console_endpoint.py` | 128 | Interactive endpoint wizard |
| `api/claude_proxy/utils/message_converter.py` | 119 | Claude→OpenAI schema converter |
| `api/opencode_proxy/handler/error.py` | 114 | Error classification for OpenCode proxy |
| `backend/accounts.py` | 114 | Account CRUD |
| `server/openai_server/routes/admin/endpoints.py` | 118 | Admin REST: endpoint CRUD |
| `server/openai_server/routes/app_init.py` | 105 | FastAPI app factory + lifespan |
| `server/openai_server/routes/standard_routes.py` | 100 | Root, health, models, MCP endpoints |
| `api/opencode_proxy/sse.py` | 97 | SSE event builder |
| `api/claude_proxy/handler/helpers.py` | 91 | Error messages & system status |
| `api/opencode_proxy/handler/response.py` | 88 | Response builders + cost estimation |
| `core/accounts/account_manager.py` | 81 | Account CRUD facade with 10s cache |
| `server/openai_server/routes/admin/keys.py` | 81 | Admin REST: Gemini key mgmt |
| `core/api_config.py` | 84 | Model definitions, pools, sunset logic |
| `core/router/pool.py` | 83 | ModelPool failure state machine |
| `server/openai_server/routes/opencode_routes.py` | 78 | /opencode/v1/chat/completions route |
| `server/openai_server/security.py` | 66 | Rate limit + brute force protection |
| `backend/model_prices.py` | 52 | Model price lookup from DB |
| `server/openai_server/routes/auth_session.py` | 50 | Dashboard JWT sessions |
| `api/opencode_proxy/handler/sse.py` | 49 | OpenAI SSE chunk formatter |
| `api/claude_proxy/utils/truncation.py` | 49 | Emergency message truncation |
| `core/config_n_logg/logger.py` | 48 | 6 rotating file handlers + console |
| `server/openai_server/routes/admin/helpers.py` | 45 | Shared .env helpers |
| `core/providers/gemini/thinking_config.py` | 43 | Builds ThinkingConfig for Gemini |
| `api/claude_proxy/utils/__init__.py` | 33 | Re-exports |
| `core/providers/litellm_wrapper.py` | 19 | LiteLLM acompletion + token_counter |
| `core/preflight.py` | 18 | Port listening diagnostics |
| `server/openai_server/routes/__init__.py` | 13 | Route registration + frontend mount |
| `core/limits/account_limiter/__init__.py` | 11 | Re-exports |
| `backend/_db.py` | 11 | Shared SQLite connection (WAL + RLock) |
| `api/claude_proxy/handler/compaction.py` | 10 | Context compaction gate |
| `core/limits/__init__.py` | 7 | Re-exports |
| `core/config_n_logg/__init__.py` | 6 | Re-exports config + loggers |
| `server/openai_server/routes/admin/__init__.py` | 4 | Package init |
| `(18 stub files < 3 lines)` | 1-3 | Package markers/re-exports |
| **Total (src/)** | **~14,056** | 100 files |

---

## ENTRY POINT: main.py

```text
main()
└── _free_port(host, port)
```

| Line | Function | Description |
|------|----------|-------------|
| 19 | `_free_port` | Kills listening processes on PORT using PowerShell / fuser |
| 48 | `main` | Registers models, checks sunset list, runs uvicorn server |

---

## SERVER LAYER

**App:** `FastAPI(title="Router API v2", version="2.0.0")` in `routes/app_init.py`

### Middleware & Events
| Line | File | Description |
|------|------|-------------|
| 15 | `app_init.py` | CORS headers middleware & OPTIONS handler |
| 84 | `app_init.py` | Startup: DB setup, log flusher, .env watcher tasks |

### HTTP Routes
| Method | Path | Handler (file) | Description |
|--------|------|---------------|-------------|
| GET | `/` | `standard_routes.root` | System status |
| GET | `/health` | `standard_routes.health` | Core model + key counts |
| GET | `/preflight` | `standard_routes.preflight` | Port diagnostics |
| GET | `/v1/models` | `standard_routes.list_models` | Active model list |
| GET | `/v1/models/{model_id}` | `standard_routes.retrieve_model` | Single model config |
| GET | `/account` | `standard_routes.current_account` | API user metrics |
| GET | `/stats` | `dashboard_routes.stats_page` | Admin dashboard HTML |
| GET | `/api/stats` | `dashboard_routes.json_stats` | Dashboard JSON |
| POST | `/v1/chat/completions` | `completions_routes.chat_completions` | OpenAI completions |
| POST | `/v1/messages` | `completions_routes.anthropic_messages` | Anthropic messages |
| POST | `/opencode/v1/chat/completions` | `opencode_routes.opencode_chat_completions` | OpenCode proxy |
| POST | `/v1/{version}/models/{model}:generateContent` | `gemini_routes.generate_content` | Gemini pass-through |
| POST | `/v1/{version}/models/{model}:streamGenerateContent` | `gemini_routes.stream_generate_content` | Gemini streaming |
| POST | `/dashboard/admin/keys/add` | `admin/keys.add_key` | Add Gemini key |
| POST | `/dashboard/admin/accounts/*` | `admin/accounts.*` | Account CRUD |
| POST | `/dashboard/admin/endpoints/*` | `admin/endpoints.*` | Endpoint CRUD |

---

## PROXY LAYER

### Claude Proxy (`src/api/claude_proxy/`)

**Singleton:** `claude_proxy = ClaudeProxy()` defined in `handler/proxy.py`

#### Key Functions
| Line | Function | Description |
|------|----------|-------------|
| 48 | `_build_litellm_thinking` | Auto-enables thinking (budget 24576/32768), translates to Gemini config |
| 41 | `_model_supports_thinking` | Checks model support (Gemini 2, 2.5, 3 series) |
| 127 | `_prepare_litellm_kwargs` | Builds LiteLLM kwargs with thinking params + tools |
| 158 | `_call_lm_with_retry` | Retry loop with pool swap, thinking-aware error handling |

#### SSE Streaming Flow with Thinking
```text
Client request → [Auto-enable thinking] → LiteLLM kwargs with thinkingConfig
                                                   │
                                                   ▼
LiteLLM Gemini stream chunks (delta.reasoning_content + delta.content)
                                                   │
                                                   ▼
_process_anthropic_stream (stream.py:18)
  ├── reasoning_content != None? → emit thinking_delta SSE event
  ├── content != None? → feed XMLThinkingExtractor → emit text/thinking blocks
  └── finish_reason? → close open blocks → signature_delta → content_block_stop
                                                   │
                                                   ▼
Anthropic SSE events to client:
  message_start → [thinking_delta* → signature_delta* → content_block_stop]*
               → [text_delta* → content_block_stop]*
               → [tool_use blocks]*
               → message_delta → message_stop
```

#### Search Status Streaming (WebSearch intercept)
```text
Client request → [WebSearch tool detected]
  → message_start
  → content_block_start index=0 (text block)
  → 🔍 Searching...          (text_delta @ 0s)
  → 📡 Querying...           (text_delta @ 3s)
  → 📄 Reading results...    (text_delta @ 6s)
  → ⚡ Synthesizing...       (text_delta @ 9s)
  → content_block_stop index=0
  → [actual thinking/text/tool blocks starting at index=1]
  → message_delta → message_stop
```

### OpenCode Proxy (`src/api/opencode_proxy/`)

**Singleton:** `opencode_proxy = OpenCodeProxy()` in `handler/proxy.py`

#### Key Functions
| Line | Function | Description |
|------|----------|-------------|
| 50 | `_build_litellm_thinking` | Translates thinking_level/budget → LiteLLM kwargs |
| 111 | `chat_completion` | Non-streaming entry: alias resolve, web search, dispatch |
| 124 | `stream_chat_completion` | Streaming entry: yields SSE chunks via pool/standalone |
| 226 | `_prepare_litellm_kwargs` | Builds full LiteLLM kwargs dict |

#### Streaming with Search Status
```text
Client request → [WebSearch interceptor]
  → data: {"delta":{"content":""}}         ← initial chunk
  → data: {"delta":{"content":"🔍 Searching...\n"}}     @ 0s
  → data: {"delta":{"content":"📡 Querying DuckDuckGo...\n"}}  @ 3s
  → data: {"delta":{"content":"📄 Reading results...\n"}}     @ 6s
  → data: {"delta":{"content":"⚡ Synthesizing...\n"}}        @ 9s
  → [actual reasoning_content/text content chunks]
  → data: {"choices":[{"delta":{},"finish_reason":"stop"}]}
  → data: [DONE]
```

---

## DB SCHEMA: usage.db

### Tables

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
| subagent_model | TEXT | NULL | Custom sub-agent model override |
| created_at | INTEGER | — | Unix timestamp |
| updated_at | INTEGER | — | Unix timestamp |

**custom_endpoints** — Non-Gemini backends
| Column | Type | Default | Description |
|--------|------|---------|-------------|
| name | TEXT PK | — | Endpoint alias |
| base_url | TEXT | — | API base URL |
| auth_key | TEXT | — | API key for endpoint |
| enabled | INTEGER | 1 | 0 = disabled |
| models | TEXT | '[]' | JSON list of model names |
| pool_assignments | TEXT | '{}' | JSON pool→model mapping |
| fallback | INTEGER | 0 | Use as pool fallback |
| updated_at | TEXT | — | ISO timestamp |

**key_status** — Per-key circuit breaker
| Column | Type | Default | Description |
|--------|------|---------|-------------|
| key | TEXT PK | — | Gemini API key hash |
| usage | INTEGER | 0 | Total usage count |
| active_requests | INTEGER | 0 | Concurrent request count |
| frozen_until | REAL | 0.0 | Cooldown expiry timestamp |
| consecutive_failures | INTEGER | 0 | Failure streak counter |
| last_success | REAL | 0.0 | Last success timestamp |
| date | TEXT | '' | Today's date string |
| today | INTEGER | 0 | Today's request count |
| per_model | TEXT | '{}' | Per-model usage JSON |
| tier | TEXT | 'free' | Key tier assignment |

**model_prices** — Cost lookup table
| Column | Type | Description |
|--------|------|-------------|
| model_id | TEXT PK | Model identifier |
| input_price | REAL | Price per 1K input tokens |
| output_price | REAL | Price per 1K output tokens |

---

## KEY ARCHITECTURE DECISIONS

1. **Search Status Streaming**: Proxy intercepts WebSearch calls, runs DuckDuckGo + page crawler, streams progress emoji as SSE text deltas every 2s instead of silent keepalives.
2. **OpenCode Proxy Feeding All OpenAI Streaming**: Every `/v1/chat/completions` with `stream=True` routes through `OpenCodeProxy` — non-stream goes direct.
3. **Claude Proxy & OpenCode Proxy Separate**: Anthropic and OpenAI protocols each have dedicated proxy with their own SSE format converters.
4. **Pass-through Gemini**: Native format untouched — streaming search status not possible without breaking GenAI SDK client expectations.
5. **Serial Search**: Sub-agent search queries run sequentially (1 per turn), not parallel — eliminates 429 cascade from fan-out.
6. **Per-Tier Semaphore**: Concurrency capped per account tier: admin=6, premium=4, free=2 — independent semaphores, not global.
7. **In-Memory Rate Limits**: All RPM/TPM/RPD tracking via `deque` sliding windows — zero DB reads on hot path. Account lookup cached 10s TTL.
8. **Throttle Pacing 1–2.6s**: Global + per-key minimum intervals enforced with jitter before every API call.
9. **Key Caching Strategy (v2.2)**: Key resolver caches top 50% of available keys, refreshing every 10 requests for CPU optimization.
10. **Paced Multi-attempt Pool Swap**: On rate limit, swaps to another model in the pool (e.g. flash-35 → flash-30), retry-spacing up to 13 times.
11. **Adaptive Cooldown & Penalty Jitter**: Cooldowns apply randomized jitter (0-15%) plus gaussian margins to avoid key starvation.
12. **Strict Concurrency Cap**: Restricts each API key to exactly 1 active request. Keys with `active_requests > 0` are skipped during reservation.
13. **Customizable Agent Models**: Sub-agent models overrideable per account (`subagent_model` in DB) or globally via env vars, fallback `gemini-flash-lite`.
14. **Thinking Auto-Enable & SSE Compliance (v2.3)**: Proxy auto-enables extended thinking for main agent requests on Gemini 2/2.5/3 models. Claude proxy SSE converter emits spec-compliant `signature_delta`. OpenCode proxy applies sentence-boundary + 80-char chunking for progressive thinking streaming.
15. **Gemini SDK Refactoring (v2.3)**: `gemini_api_manager.py` refactored into `gemini/` sub-package with separate modules for manager, caller, pool, error classification, and thinking config.

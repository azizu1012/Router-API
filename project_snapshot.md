# Router API v2 — Project Snapshot

<!-- AI-READABLE — STRUCTURED FOR LLM PARSING -->
<!-- Generated: 2026-06-13 | Python 3.13 | win32 -->

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
├── src/                          # 100 Python files, ~15,835 lines total
│   ├── api/                      #   ~4,261 lines — proxy layers
│   │   ├── claude_proxy/         #     Anthropic→Gemini proxy (stream + non-stream)
│   │   │   ├── stream.py         #       Anthropic SSE converter: thinking_delta + signature_delta
│   │   │   ├── handler/
│   │   │   │   ├── proxy.py              # ClaudeProxy singleton: LiteLLM kwargs, thinking, retry
│   │   │   │   ├── proxy_stream.py       # Streaming call mixer with keepalive pings
│   │   │   │   ├── proxy_nonstream.py    # Non-streaming call mixer
│   │   │   │   ├── stream_executor.py    # WebSearch intercept & streaming handler
│   │   │   │   ├── nonstream_executor.py # WebSearch intercept & thinking extraction
│   │   │   │   ├── compaction.py         # Context compaction gate
│   │   │   │   └── helpers.py            # Error messages & system status
│   │   │   └── utils/
│   │   │       ├── format_normalizer.py  # StreamingTextNormalizer + XMLThinkingExtractor
│   │   │       ├── sse_cache_agent.py    # Cache simulator, sub-agent detection, SSE helpers
│   │   │       ├── message_converter.py  # Claude→OpenAI schema converter
│   │   │       ├── model_resolver.py     # Model alias resolution & key concurrency
│   │   │       ├── truncation.py         # Emergency message truncation
│   │   │       └── __init__.py           # Re-exports
│   │   │
│   │   └── opencode_proxy/       #     OpenCode→Gemini proxy (OpenAI-compatible)
│   │       ├── sse.py            #       SSE event builder for OpenCode streaming
│   │       └── handler/
│   │           ├── proxy.py              # Orchestrator: model resolution, web search, retry
│   │           ├── stream_executor.py    # Core streaming: thinking, websearch, pool retry
│   │           ├── nonstream_executor.py # Non-streaming with tool recursion
│   │           ├── search.py             # Sub-agent web search execution
│   │           ├── websearch.py          # Search intent detection & injection
│   │           ├── detection.py          # Sub-agent override detector
│   │           ├── response.py           # Response builders & cost estimation
│   │           ├── sse.py                # OpenAI SSE chunk formatter
│   │           └── error.py              # Error classification & pool retry
│   │
│   ├── backend/                   #   ~989 lines — SQLite DB layer
│   │   ├── _db.py                #     Shared connection + WAL + RLock
│   │   ├── schema.py             #     DDL + migration from JSON
│   │   ├── accounts.py           #     Account CRUD
│   │   ├── endpoints.py          #     Custom endpoint CRUD
│   │   ├── key_status.py         #     Key status + usage atomic ops
│   │   └── model_prices.py       #     Model price lookup
│   │
│   ├── console/                   #   ~732 lines — CLI admin
│   │   ├── admin_console.py      #     Main CLI shell (account/endpoint/key commands)
│   │   ├── console_endpoint.py   #     Interactive endpoint wizard
│   │   └── console_helpers.py    #     Formatting, prompts, selectors
│   │
│   ├── core/                      #   ~4,195 lines — routing, limits, providers
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
│   ├── server/                     #   ~2,858 lines — HTTP server
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
│       ├── duckduckgo.py         #   WebSearch engine + page crawler (444 lines)
│       ├── ddg_ranking.py         #   Consensus ranking + topic classification (319 lines)
│       ├── ddg_utils.py           #   URL normalization + dedup (233 lines)
│       └── ddg_data.py            #   Cache + topic data (165 lines)
│
├── tests/
│   ├── check-keys.py             # Ping all keys via router
│   ├── test-api.py               # OpenAI completions endpoint
│   ├── test_proxy.py             # Anthropic messages endpoint
│   ├── test_all_models.py        # Query all 6 model pools
│   ├── test_all_flash_features.py# Grounding & fallback integration
│   ├── test_compaction.py        # Compaction logic checks
│   ├── test_concurrent_400k.py   # Multi-connection load check
│   ├── test_genai_compatibility.py# GenAI SDK translation checks
│   ├── test_hybrid_search.py     # Web search pipeline
│   ├── test_thinking.py          # Thinking in streaming responses
│   ├── test_thinking_router.py   # Thinking routing correctness
│   ├── test_pool.py              # Pool swap & failover logic
│   ├── test_opencode_proxy.py    # OpenCode proxy integration
│   ├── test_vietnamese_query.py  # Vietnamese language test
│   ├── test_verbose_400k.py      # Large context input test
│   └── test_lite_grounding_comparison.py # Lite vs standard grounding

```

---

## FILE METRICS

| File | Lines | Role |
|------|-------|------|
| `api/opencode_proxy/handler/stream_executor.py` | 749 | OpenCode streaming: thinking chunking, websearch, pool retry |
| `server/pass_through_server/routes/gemini_routes.py` | 647 | Native Gemini API pass-through proxy |
| `server/openai_server/handler.py` | 503 | OpenAI chat completions executor, thinking passthrough |
| `core/providers/gemini/manager.py` | 499 | GeminiAPIManager: SDK pipeline, semaphore, retry |
| `tools/duckduckgo.py` | 444 | WebSearch engine: DuckDuckGo, page crawler, ranking |
| `api/claude_proxy/handler/stream_executor.py` | 432 | Streaming execution, WebSearch intercept, thinking SSE |
| `console/admin_console.py` | 398 | Interactive CLI admin shell |
| `server/openai_server/routes/dashboard_routes.py` | 383 | Dashboard login + stats HTML/JSON |
| `api/claude_proxy/utils/format_normalizer.py` | 375 | StreamingTextNormalizer + XMLThinkingExtractor |
| `core/limits/gemini_rate_limiter.py` | 375 | Per-model RPM/TPM/RPD sliding window |
| `core/router/core/key_resolver.py` | 368 | Circuit breaker, adaptive cooldown, key caching |
| `server/openai_server/routes/completions_routes.py` | 366 | /v1/chat/completions + /v1/messages routes |
| `core/providers/custom_endpoint_manager.py` | 357 | Custom endpoint CRUD + pool + health |
| `api/opencode_proxy/handler/proxy.py` | 350 | OpenCode proxy orchestrator |
| `core/providers/search_manager.py` | 349 | Search intent + Google grounding + hybrid search |
| `api/claude_proxy/stream.py` | 339 | Anthropic SSE: thinking_delta + signature_delta |
| `api/claude_proxy/utils/sse_cache_agent.py` | 336 | Cache simulator, sub-agent detection, SSE helpers |
| `api/claude_proxy/handler/proxy_stream.py` | 335 | Streaming call mixer with keepalive pings |
| `api/claude_proxy/handler/proxy.py` | 329 | ClaudeProxy singleton: thinking config, retry loop |
| `core/router/core/router.py` | 329 | APIRouter: key registry, scoring, pool selection |
| `tools/ddg_ranking.py` | 319 | Consensus ranking, topic classification |
| `backend/key_status.py` | 310 | Key circuit breaker, freeze/cooldown DB ops |
| `core/limits/account_limiter/capacity.py` | 304 | Pool capacity by tier calculations |
| `backend/schema.py` | 286 | DDL definitions + JSON→SQLite migration |
| `api/claude_proxy/handler/proxy_nonstream.py` | 264 | Non-streaming call mixer |
| `core/limits/account_limiter/effective_limits.py` | 243 | Effective limits after pool sharing |
| `api/claude_proxy/handler/nonstream_executor.py` | 239 | Non-stream: WebSearch, thinking extraction |
| `tools/ddg_utils.py` | 233 | URL normalization, dedup, page crawling |
| `server/openai_server/auth.py` | 219 | Bearer token auth + account limiter + reaper |
| `core/config_n_logg/config.py` | 203 | RouterApiConfig dataclass from .env |
| `backend/endpoints.py` | 192 | Custom endpoint CRUD |
| `core/usage_logger.py` | 191 | Async telemetry batch flusher |
| `core/providers/gemini/error.py` | 191 | Pure error classification functions |
| `console/console_helpers.py` | 189 | CLI formatting, prompts, selectors |
| `api/opencode_proxy/handler/search.py` | 186 | OpenCode web search via Gemini sub-agent |
| `api/opencode_proxy/handler/nonstream_executor.py` | 182 | Non-stream OpenCode with tool recursion |
| `core/providers/gemini_api_helpers.py` | 179 | Error classification mixin + retry helpers |
| `core/limits/account_limiter/limiter.py` | 172 | Per-account sliding window limiter |
| `tools/ddg_data.py` | 165 | Search cache + topic classification data |
| `server/openai_server/routes/admin/accounts.py` | 164 | Admin REST: account CRUD |
| `api/opencode_proxy/handler/websearch.py` | 157 | Search intent detection & injection |
| `core/providers/gemini/caller.py` | 156 | Gemini SDK caller with safety settings |
| `api/opencode_proxy/handler/detection.py` | 151 | Sub-agent keyword detection |
| `console/console_endpoint.py` | 145 | Interactive endpoint wizard |
| `api/claude_proxy/utils/model_resolver.py` | 143 | Model alias resolution + key concurrency |
| `api/opencode_proxy/handler/error.py` | 138 | Error classification for OpenCode proxy |
| `server/openai_server/routes/admin/endpoints.py` | 137 | Admin REST: endpoint CRUD |
| `core/providers/gemini/pool.py` | 135 | ClientPool with health tracking |
| `api/claude_proxy/utils/message_converter.py` | 130 | Claude→OpenAI schema converter |
| `backend/accounts.py` | 127 | Account CRUD |
| `server/openai_server/routes/app_init.py` | 122 | FastAPI app factory + lifespan |
| `server/openai_server/routes/standard_routes.py` | 117 | Root, health, models, MCP endpoints |
| `api/opencode_proxy/sse.py` | 113 | SSE event builder |
| `api/opencode_proxy/handler/response.py` | 108 | Response builders + cost estimation |
| `core/accounts/account_manager.py` | 100 | Account CRUD facade with 10s cache |
| `server/openai_server/routes/admin/keys.py` | 99 | Admin REST: Gemini key mgmt |
| `api/claude_proxy/handler/helpers.py` | 97 | Error messages & system status |
| `core/router/pool.py` | 97 | ModelPool failure state machine |
| `core/api_config.py` | 92 | Model definitions, pools, sunset logic |
| `server/openai_server/routes/opencode_routes.py` | 88 | /opencode/v1/chat/completions route |
| `server/openai_server/security.py` | 86 | Rate limit + brute force protection |
| `core/config_n_logg/logger.py` | 65 | 6 rotating file handlers + console |
| `api/claude_proxy/utils/truncation.py` | 64 | Emergency message truncation |
| `api/opencode_proxy/handler/sse.py` | 61 | OpenAI SSE chunk formatter |
| `backend/model_prices.py` | 60 | Model price lookup from DB |
| `server/openai_server/routes/auth_session.py` | 55 | Dashboard JWT sessions |
| `core/providers/gemini/thinking_config.py` | 52 | Builds ThinkingConfig for Gemini |
| `server/openai_server/routes/admin/helpers.py` | 52 | Shared .env helpers |
| `api/claude_proxy/utils/__init__.py` | 40 | Re-exports |
| `core/providers/litellm_wrapper.py` | 29 | LiteLLM acompletion + token_counter |
| `core/preflight.py` | 21 | Port listening diagnostics |
| `server/openai_server/routes/__init__.py` | 16 | Route registration + frontend mount |
| `backend/_db.py` | 14 | Shared SQLite connection (WAL + RLock) |
| `api/claude_proxy/handler/compaction.py` | 13 | Context compaction gate |
| `core/limits/account_limiter/__init__.py` | 12 | Re-exports |
| `core/limits/__init__.py` | 7 | Re-exports |
| `core/config_n_logg/__init__.py` | 6 | Re-exports config + loggers |
| `server/openai_server/routes/admin/__init__.py` | 5 | Package init |
| `core/providers/genai_types.py` | 3 | Re-exports google.genai types |
| `core/providers/gemini/__init__.py` | 3 | Re-exports GeminiAPIManager |
| `api/opencode_proxy/__init__.py` | 3 | Package init |
| `server/openai_server/routes/admin_routes.py` | 2 | Backward-compatible facade |
| `core/providers/gemini_api_manager.py` | 2 | Thin facade → gemini/ package |
| `(26 __init__/stub files < 3 lines)` | 1-3 | Package markers/re-exports |
| **Total (src/)** | **~15,835** | 100 files |

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
  message_start → [thinking_delta* → signature_delta → content_block_stop]*
               → [text_delta* → content_block_stop]*
               → [tool_use blocks]*
               → message_delta → message_stop
```

#### Sub-Agent Detection & Model Override
| Line | Function | Description |
|------|----------|-------------|
| 190 | `is_sub_agent_body` | Returns True for sub-agent prompts (non-interactive, specialist roles) |
| 203 | (main agent check) | `"you are claude code"` returns False (NOT sub-agent) |
| 46 | `_intercept_sub_agent` | Overrides model to `gemini-flash-lite` for detected sub-agents |
| 89-93 | Sub-agent keywords | `general-purpose agent`, `explore agent`, `plan agent`, etc. |

#### Context Compaction Flow
```text
Client request → [Compaction Gate] → Context > 80K? → Yes → History split
                                                                       │
                                                                       ▼
History summary + existing progress_report.md → [Gemini Flash Lite] → Merge
                                                                       │
                                                                       ▼
1. Update project progress_report.md on disk  ←────────────────────────+
2. Ingest merged report as a single prompt block
3. Append ~10 recent messages
4. Send to destination Gemini Flash model
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

#### OpenCode Streaming Thinking Flow
```text
Client request → [Sub-agent detection] → [Web search injection] → LiteLLM dispatch
                                                                         │
                                                                         ▼
LiteLLM streaming chunks → stream_executor.py:76 → [Delta inspection]
  ├── reasoning_content? → _yield_reasoning() → sentence-boundary + 80-char chunking
  ├── content? → _yield_text() → StreamingTextNormalizer → feed XMLThinkingExtractor
  └── finish_reason? → flush remaining buffers
                                                                         │
                                                                         ▼
OpenAI SSE chunks to client:
  {"choices":[{"delta":{"role":"assistant"}}]}
  {"choices":[{"delta":{"reasoning_content":"...", "content":""}}]}  ← progressive thinking
  {"choices":[{"delta":{"content":"..."}}]}                           ← progressive text
  {"choices":[{"delta":{}},"finish_reason":"stop"}]}
  data: [DONE]
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

## CONFIGURATION: .env

| Env Var | Default | Description |
|---------|---------|-------------|
| `GEMINI_API_KEY_1..N` | — | Gemini API keys (63 max) |
| `ROUTER_API_HOST` | `127.0.0.1` | Server binding IP |
| `ROUTER_API_PORT` | `58100` | Server binding port |
| `ROUTER_API_DEFAULT_MODEL_ALIAS`| `gemini-flash` | Default model pool |
| `ROUTER_API_MAX_RETRIES` | `13` | Max key retry attempts |
| `REQUEST_TIMEOUT_SECONDS` | `600` | Per-request timeout |
| `MAX_OUTPUT_TOKENS` | `65536` | Max response tokens |
| `COMPACTION_TOKEN_THRESHOLD` | `160000` | Standard compaction trigger |
| `CLAUDE_CODE_COMPACTION_THRESHOLD`| `80000` | Claude Code compaction trigger |
| `EMERGENCY_MAX_INPUT_TOKENS` | `180000` | Hard truncation limit |
| `LITE_EMERGENCY_MAX_INPUT_TOKENS` | `130000` | Lite model truncation limit |
| `KEY_429_COOLDOWN_SECONDS` | `15` | Freeze time after 429 |
| `KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS` | `3` | Freeze time for unknown errors |
| `POOL_SWAP_FAILURES` | `2` | Failures before pool swap |
| `POOL_MAX_ATTEMPTS` | `6` | Max pool retry attempts |
| `OPENCODE_SUB_AGENT_MODEL` | `gemini-flash-lite` | OpenCode sub-agent model |
| `SUB_AGENT_MODEL` | `gemini-flash-lite` | Claude Code sub-agent model |

---

## KEY ARCHITECTURE DECISIONS

1. **Serial Search**: Sub-agent search queries run sequentially (1 per turn), not parallel — eliminates 429 cascade from fan-out.
2. **Per-Tier Semaphore**: Concurrency capped per account tier: admin=6, premium=4, free=2 — independent semaphores, not global.
3. **In-Memory Rate Limits**: All RPM/TPM/RPD tracking via `deque` sliding windows — zero DB reads on hot path. Account lookup cached 10s TTL.
4. **Throttle Pacing 1–2.6s**: Global + per-key minimum intervals enforced with jitter before every API call.
5. **Key Caching Strategy (v2.2)**: Key resolver caches top 50% of available keys, refreshing every 10 requests for CPU optimization.
6. **Proxy Auto-Managed Progress**: Compaction handles `progress_report.md` lifecycle automatically, merging previous logs via `gemini-flash-lite`.
7. **WebSearch Interception**: Proxy intercepts all client WebSearch calls, runs a localized crawler + consensus ranking, maps findings to structured link citations.
8. **Paced Multi-attempt Pool Swap**: On rate limit, swaps to another model in the pool (e.g. flash-35 → flash-30), retry-spacing up to 13 times.
9. **Adaptive Cooldown & Penalty Jitter**: Cooldowns apply randomized jitter (0-15%) plus gaussian margins to avoid key starvation.
10. **Strict Concurrency Cap**: Restricts each API key to exactly 1 active request. Keys with `active_requests > 0` are skipped during reservation.
11. **Dual Compaction Limits**: Aggressive context thresholds for Claude Code (80K trigger, 45K limit) vs standard chats (160K trigger) to avoid Vertex TPM errors.
12. **OpenCode Proxy Separation**: Dedicated `/opencode/v1/chat/completions` routing auto-identifies OpenCode requests without polluting system prompt strings.
13. **Customizable Agent Models**: Sub-agent models overrideable per account (`subagent_model` in DB) or globally via env vars (`OPENCODE_SUB_AGENT_MODEL`/`SUB_AGENT_MODEL`), fallback `gemini-flash-lite`.
14. **Cost Tracking Completeness (v2.1+)**: All streaming endpoints log usage with actual `model_id` — fixes $0.0000 dashboard bug. Model prices stored in `model_prices` table.
15. **Thinking Auto-Enable & SSE Compliance (v2.3)**: Proxy auto-enables extended thinking for main agent requests on Gemini 2/2.5/3 models with budget 24576 (flash) / 32768 (pro). Claude proxy SSE converter emits spec-compliant `signature_delta` before every `content_block_stop` for thinking blocks — fixes Claude Code missing thinking and "bắn 1 lèo" buffering. OpenCode proxy applies sentence-boundary + 80-char chunking for progressive thinking streaming.
16. **Sub-Agent & Thinking Isolation**: `is_sub_agent_body()` correctly identifies main Claude Code agent (returns `False` for `"you are claude code"` prompt) vs sub-agents, preventing thinking budget drain on cheap sub-agent tasks.
17. **Fake vs Real Streaming**: Two streaming modes exist: (a) **Real streaming** via LiteLLM async generator — thinking progressively streamed as `reasoning_content` deltas; (b) **Fake/buffered streaming** in `handler.py` — full response collected first, then chunked into SSE events with 900-char progressive emission for client perception.
18. **Gemini SDK Refactoring (v2.3)**: `gemini_api_manager.py` refactored into `gemini/` sub-package with separate modules for manager, caller, pool, error classification, and thinking config — improves maintainability and testability.

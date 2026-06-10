# Router API v2 — Project Snapshot

<!-- AI-READABLE — STRUCTURED FOR LLM PARSING -->
<!-- Generated: 2026-06-06 | Python 3.13 | win32 -->

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
├── src/
│   ├── __init__.py
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   │
│   │   ├── claude_proxy/
│   │   │   ├── __init__.py
│   │   │   ├── stream.py         # Anthropic SSE chunk stream converter
│   │   │   ├── handler/          # API proxy execution engines
│   │   │   │   ├── __init__.py
│   │   │   │   ├── compaction.py # Token limits compaction gate middleware
│   │   │   │   ├── helpers.py    # Request reinforcement & error classification
│   │   │   │   ├── nonstream_executor.py # WebSearch intercept & non-stream handler
│   │   │   │   ├── stream_executor.py    # WebSearch intercept & streaming handler
│   │   │   │   ├── proxy.py              # LiteLLM kwargs prep & retry loop
│   │   │   │   ├── proxy_nonstream.py    # Non-streaming call mixer
│   │   │   │   └── proxy_stream.py       # Streaming call mixer with pings
│   │   │   │
│   │   │   └── utils/
│   │   │       ├── __init__.py
│   │   │       ├── compaction_utils.py # Workspace detector & merge engine
│   │   │       ├── message_converter.py# Claude↔OpenAI schema converter
│   │   │       ├── model_resolver.py   # Model alias resolution & backoff
│   │   │       └── sse_cache_agent.py  # Cache simulator & sub-agent interceptor
│   │   │
│   │   └── opencode_proxy/
│   │       ├── __init__.py
│   │       ├── detection.py      # Subagent override detector (backup)
│   │       ├── sse.py            # SSE parser/formatter helper
│   │       └── handler/          # OpenCode proxy execution engines
│   │           ├── detection.py  # Main subagent override detector
│   │           ├── proxy.py      # Request routing entrypoint
│   │           ├── nonstream_executor.py # Non-streaming caller with websearch
│   │           ├── stream_executor.py    # Streaming caller with websearch
│   │           ├── search.py     # duckduckgo search wrapper
│   │           └── sse.py        # SSE stream parser
│   │
│   ├── backend/
│   │   ├── __init__.py
│   │   ├── _db.py                # SQLite connection factory (WAL + RLock)
│   │   ├── schema.py             # DDL definitions & legacy JSON migrator
│   │   ├── accounts.py           # Account CRUD operations
│   │   ├── endpoints.py          # Custom endpoint CRUD operations
│   │   └── key_status.py         # Key circuit breaker & DB operations
│   │
│   ├── console/
│   │   ├── __init__.py
│   │   ├── admin_console.py      # Admin CLI interactive shell
│   │   ├── console_endpoint.py   # Interactive endpoint wizard
│   │   └── console_helpers.py    # CLI formatting utilities
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── api_config.py         # Model pools & Gemini 2026 sunset logic
│   │   ├── preflight.py          # Startup diagnostics check
│   │   ├── usage_logger.py       # Async telemetry flush queue
│   │   ├── auth/                 # Auth sub-package
│   │   │   └── __init__.py
│   │   │
│   │   ├── config_n_logg/
│   │   │   ├── __init__.py       # Re-exports config + loggers
│   │   │   ├── config.py         # RouterApiConfig dataclass loader
│   │   │   └── logger.py         # 6 rotating file + console loggers
│   │   │
│   │   ├── accounts/
│   │   │   ├── __init__.py
│   │   │   └── account_manager.py # Account CRUD facade
│   │   │
│   │   ├── limits/
│   │   │   ├── __init__.py
│   │   │   ├── gemini_rate_limiter.py # Per-model RPM/TPM/RPD sliding window
│   │   │   └── account_limiter/       # Per-account weighted limits
│   │   │       ├── __init__.py
│   │   │       ├── limiter.py
│   │   │       ├── capacity.py
│   │   │       └── effective_limits.py
│   │   │
│   │   ├── providers/
│   │   │   ├── __init__.py
│   │   │   ├── gemini_api_manager.py  # Gemini SDK caller with semaphore
│   │   │   ├── gemini_api_helpers.py  # Error classification mixin
│   │   │   ├── custom_endpoint_manager.py # OpenAI-compatible custom endpoints
│   │   │   └── search_manager.py      # Search intent + Google grounding
│   │   │
│   │   └── router/
│   │       ├── __init__.py
│   │       ├── core/                  # Key resolver engine
│   │       │   ├── __init__.py
│   │       │   ├── router.py         # Singleton APIRouter
│   │       │   └── key_resolver.py   # Circuit breaker + adaptive cooldown
│   │       └── pool.py               # ModelPool failure state machine
│   │
│   ├── server/
│   │   ├── __init__.py
│   │   ├── openai_server/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py             # Bearer token auth middleware
│   │   │   ├── security.py         # Rate limit + brute force protection
│   │   │   ├── handler.py          # OpenAI chat completions executor
│   │   │   └── routes/
│   │   │       ├── __init__.py     # Route registration + static mount
│   │   │       ├── app_init.py     # FastAPI app factory + lifespan
│   │   │       ├── standard_routes.py # Health, models, MCP endpoints
│   │   │       ├── completions_routes.py # /v1/chat/completions & /v1/messages
│   │   │       ├── auth_session.py # Dashboard JWT session
│   │   │       ├── admin_routes.py # Admin REST API (keys, accounts)
│   │   │       └── dashboard_routes.py # Dashboard stats & login
│   │   │
│   │   └── pass_through_server/
│   │       ├── __init__.py
│   │       └── routes/
│   │           ├── __init__.py
│   │           └── gemini_routes.py # Native Gemini API proxy
│   │
│   └── tools/
│       ├── __init__.py
│       └── duckduckgo.py         # Web search with caching & ranking
│
│
├── tests/
│   ├── check-keys.py             # Diagnostic: ping all keys via router
│   ├── test-api.py               # Completions endpoint (OpenAI style)
│   ├── test_proxy.py             # Messages endpoint (Anthropic style)
│   ├── test_all_models.py        # Query all 6 model pools
│   ├── test_all_flash_features.py# Grounding & fallback integration
│   ├── test_compaction.py        # Compaction logic checks
│   ├── test_concurrent_400k.py   # Multi-connection load check
│   ├── test_genai_compatibility.py# GenAI SDK translation checks
│   ├── test_hybrid_search.py     # Web search pipeline
│   ├── test_vietnamese_query.py  # Vietnamese language test
│   └── test_verbose_400k.py      # Large context input test

```

---

## FILE METRICS

| File | Lines | Role |
|------|-------|------|
| `src/tools/duckduckgo.py` | 1343 | Grounded web search cào web, page crawler, consensus ranking |
| `src/api/claude_proxy/utils/compaction_utils.py` | 567 | Workspace detection, progress_report.md merge logic |
| `src/core/router/core/key_resolver.py` | ~330 | Circuit breaker, adaptive cooldown, pool-aware key selection |
| `src/core/router/core/router.py` | ~200 | Singleton APIRouter: key registry, pool-based model selection |
| `src/core/limits/gemini_rate_limiter.py` | ~380 | Per-model sliding window queue & key priority scorer |
| `src/core/limits/account_limiter/limiter.py` | ~50 | Per-account RPM/TPM sliding window limiter |
| `src/core/limits/account_limiter/capacity.py` | ~120 | Pool capacity calculations by tier |
| `src/server/openai_server/routes/admin_routes.py` | 400 | Admin dashboard keys, endpoints, and accounts endpoints |
| `src/api/claude_proxy/handler/stream_executor.py` | 379 | Streaming execution, WebSearch intercept, model swapping |
| `src/api/claude_proxy/handler/proxy_stream.py` | 335 | Streaming request mixer with keepalive pings |
| `src/api/claude_proxy/utils/message_converter.py` | 326 | Converts Anthropic schemas, injects auto progress instructions |
| `src/core/providers/gemini_api_manager.py` | 407 | Gemini SDK caller with per-tier semaphore & retry pacing |
| `src/core/accounts/account_manager.py` | 103 | Account CRUD facade with in-memory cache |
| `src/console/admin_console.py` | 278 | Interactive command shell CLI for admin management |
| `src/api/claude_proxy/utils/sse_cache_agent.py` | 275 | Simulated cache metrics, sub-agent overrides |
| `src/backend/key_status.py` | 272 | Key status records, success/freeze DB updates |
| `src/api/claude_proxy/handler/proxy_nonstream.py` | 265 | Non-streaming request wrapper |
| `src/backend/schema.py` | 198 | SQL schema DDL updates & JSON-to-SQLite migrations |
| `src/api/claude_proxy/handler/proxy.py` | 197 | Proxy request routing orchestrator |
| `src/api/claude_proxy/handler/nonstream_executor.py` | 196 | WebSearch intercept & non-stream executor |
| `src/core/config_n_logg/config.py` | 203 | Configuration properties loaded from .env |
| `src/backend/endpoints.py` | 183 | Custom endpoint database CRUD functions |
| `src/core/usage_logger.py` | 166 | Telemetry flush queue to logging database |
| `src/core/providers/custom_endpoint_manager.py` | 165 | Non-Gemini model custom endpoints facade |
| `src/console/console_endpoint.py` | 164 | CLI interactive endpoint setup utility |
| `src/console/console_helpers.py` | 161 | CLI screen layout formatting utility |
| `src/api/claude_proxy/stream.py` | 160 | Converts OpenAI chunks to Anthropic SSE events |
| `src/backend/accounts.py` | 126 | Account records CRUD operations |
| `src/server/openai_server/routes/standard_routes.py` | 115 | Root, health check, and model listings endpoints |
| `src/core/providers/search_manager.py` | 102 | Search queries parser & grounding manager |
| `src/server/openai_server/routes/app_init.py` | 101 | FastAPI startup, background env watcher |
| `src/api/claude_proxy/utils/model_resolver.py` | 83 | Resolves aliases, manages key concurrency loops |
| `src/server/openai_server/auth.py` | 77 | Token extractor and validator middleware |
| `src/core/api_config.py` | 75 | Pre-sunset pools logic & active models configuration |
| `src/api/claude_proxy/handler/helpers.py` | 70 | Error reasoning classifier & load reinforcement rules |
| `src/core/router/pool.py` | 67 | Swap indexes, pools failures manager |
| `src/server/openai_server/routes/auth_session.py` | 56 | Dashboard JWT session cookies generation/verification |
| `src/core/accounts/account_manager.py` | 51 | Account CRUD facade for endpoints |
| `src/core/config_n_logg/logger.py` | 48 | Main console and rotating file log configuration |
| `src/api/claude_proxy/handler/compaction.py` | 28 | Context compaction gate checker |
| `src/core/preflight.py` | 18 | Internal port listening diagnostics check |
| `src/backend/_db.py` | 11 | Shared database connection factory |
| **Total (src/)** | **~8600** | |

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

## SERVER LAYER: src/server/openai_server/

**App:** `FastAPI(title="Router API v2", version="2.0.0")` initialized in `routes/app_init.py`

### Middleware & Events
| Line | Kind | Description |
|------|------|-------------|
| 15 | `@app.middleware("http")` | CORS headers middleware & OPTIONS interceptor |
| 84 | `@app.on_event("startup")` | Database setup, logs flusher, .env files watcher tasks |

### HTTP Endpoints
| Line | Method | Path | Handler | Description |
|------|--------|------|---------|-------------|
| 12 | GET | `/` | `root` | System online status |
| 28 | GET | `/health` | `health` | Core model & key counts |
| 37 | GET | `/preflight` | `preflight` | Port check diagnostics |
| 57 | GET | `/v1/models` | `list_models` | List valid active models |
| 67 | GET | `/v1/models/{model_id}` | `retrieve_model` | Single model configurations |
| 84 | GET | `/account` | `current_account` | API user metrics summary |
| 104| GET | `/stats` | `stats_page` | Administrative panel HTML |
| 14 | POST | `/v1/chat/completions` | `chat_completions` | OpenAI completions (completions_routes.py) |
| 63 | POST | `/v1/messages` | `anthropic_messages` | Anthropic completions (completions_routes.py) |
| 62 | POST | `/dashboard/admin/keys/add` | `admin_add_key` | Registers new API key (admin_routes.py) |

### Key Internal Functions
| Line | Function | Description |
|------|----------|-------------|
| 33 | `_watch_env_file` | Checks .env file modifications, triggers key hot reload |

---

## PROXY LAYER: src/api/claude_proxy

**Singleton:** `claude_proxy = ClaudeProxy()` defined in `handler/proxy.py`

### Functions / Methods
| Line | Name | Description |
|------|------|-------------|
| 30 | `stream_message` | Stream mixer: handles compaction, runs stream generator |
| 20 | `create_message` | Non-stream mixer: executes request, handles exceptions |
| 121 | `_execute_nonstream` | Non-stream caller: triggers WebSearch intercepts |
| 182 | `_execute_stream` | Streaming caller: delegates chunk streams converter |
| 8 | `_pre_compact_and_truncate`| Gates request tokens, triggers compaction if needed |

### Data Flow: Compaction & Resiliency Merge
```text
Client request -> [Compaction Gate] -> Context > 80K? -> Yes -> History split
                                                                        │
                                                                        ▼
History summary + existing progress_report.md -> [Gemini Flash Lite] -> Merge
                                                                        │
                                                                        ▼
1. Update project progress_report.md on disk   <------------------------+
2. Ingest merged report as a single prompt block 
3. Append 10 recent messages
4. Send to destination Gemini Flash model
```

---

## DB SCHEMA: usage.db

### Tables

**accounts**
| Column | Type | Default |
|--------|------|---------|
| account_id | TEXT PK | — |
| name | TEXT UNIQUE | — |
| auth_key | TEXT | — |
| enabled | INTEGER | 1 |
| tier | TEXT | 'free' |
| rpm | INTEGER | 300 |
| tpm | INTEGER | 6000000 |
| rpd | INTEGER | 20000 |
| created_at | INTEGER | — |
| updated_at | INTEGER | — |

**custom_endpoints**
| Column | Type | Default |
|--------|------|---------|
| name | TEXT PK | — |
| base_url | TEXT | — |
| auth_key | TEXT | — |
| enabled | INTEGER | 1 |
| models | TEXT | '[]' |
| pool_assignments | TEXT | '{}' |
| fallback | INTEGER | 0 |
| updated_at | TEXT | — |

**key_status**
| Column | Type | Default |
|--------|------|---------|
| key | TEXT PK | — |
| usage | INTEGER | 0 |
| active_requests | INTEGER | 0 |
| frozen_until | REAL | 0.0 |
| consecutive_failures | INTEGER | 0 |
| last_success | REAL | 0.0 |
| date | TEXT | '' |
| today | INTEGER | 0 |
| per_model | TEXT | '{}' |
| tier | TEXT | 'free' |
| data | TEXT | NULL |

**key_usage** (legacy compatibility fallback)
| Column | Type | Default |
|--------|------|---------|
| key | TEXT PK | — |
| data | TEXT | — |

---

## DATA FLOW DIAGRAM

### Client Call Pacing & Key Selection
```text
Request input -> Verify Account Quota -> Check Global Cooldown
                                                  │
                                                  ▼
Select Key Candidates (not frozen, active_requests == 0, check limiters)
                                                  │
                                                  ▼
Priority Rank Keys (apply error penalty score, pick random from top 50%)
                                                  │
                                                  ▼
Reserve Key (atomic DB increment) -> Execute Gemini call -> Release Key
```

---

## CONFIGURATION: .env

| Env Var | Default | Description |
|---------|---------|-------------|
| `ROUTER_API_HOST` | `127.0.0.1` | Server binding IP |
| `ROUTER_API_PORT` | `58100` | Server binding port |
| `ROUTER_API_DEFAULT_MODEL_ALIAS`| `gemini-flash` | Pool-based model routing fallback |
| `ROUTER_API_MAX_RETRIES` | `13` | Key attempts limit before swapping pools |
| `COMPACTION_TOKEN_THRESHOLD` | `160000` | Context compaction trigger limit for standard endpoints |
| `CLAUDE_CODE_COMPACTION_THRESHOLD`| `80000` | Context compaction trigger limit for Claude Code client |
| `EMERGENCY_MAX_INPUT_TOKENS` | `180000` | Final hard limit emergency truncation gate |

---

## STANDALONE SCRIPTS

### tests/check-keys.py
| Line | Function | Description |
|------|----------|-------------|
| 27 | `test_proxy` | Sends test requests via key to measure response latency |
| 74 | `main` | Resolves standard key health status listings table |

---

## KEY ARCHITECTURE DECISIONS

1. **Serial Search**: Sub-agent search queries run sequentially (1 per turn), not parallel — eliminates 429 cascade from fan-out.
2. **Per-Tier Semaphore**: Concurrency capped per account tier: admin=6, premium=4, free=2 — independent semaphores, not global.
3. **In-Memory Rate Limits**: All RPM/TPM/RPD tracking via `deque` sliding windows — zero DB reads on hot path. Account lookup cached 10s TTL.
4. **Throttle Pacing 1–2.6s**: Global + per-key minimum intervals enforced with jitter before every API call.
5. **Key Caching Strategy (v2.2)**: Key resolver now caches top 50% of available keys, refreshing every 10 requests to optimize CPU usage while maintaining distribution uniformity.
6. **Proxy Auto-Managed Progress**: Compaction handles the `progress_report.md` lifecycle automatically under the hood without client inputs, merging previous logs on disk using `gemini-flash-lite`.
6. **WebSearch Interception**: Proxy intercepts all client WebSearch calls, runs a localized crawler and consensus ranking logic, and maps findings to structured link citations.
7. **Paced Multi-attempt Pool Swap**: If a reserved key encounters rate limits, the request swaps to another model in the pool (e.g. flash-35 to flash-30), retry-spacing the request up to 13 times.
8. **Adaptive Cooldown & Penalty Jitter**: Model cooldowns apply randomized jitter (0-15%) plus gaussian margins to avoid key starvation and 429 concurrency collisions.
9. **Strict Concurrency Cap**: Restricts each API key to exactly 1 active request. Keys with `active_requests > 0` are skipped during reservation scans.
10. **Dual Compaction Limits**: Implements aggressive context thresholds for Claude Code (80K tokens trigger, 45K limit) versus standard chats to avoid client CLI Vertex TPM errors.
11. **OpenCode Proxy Separation**: Supports dedicated `/opencode/v1/chat/completions` routing to automatically identify OpenCode request contexts and distinguish them from standard chatbot completions without polluting system prompt strings.
12. **Customizable Agent Models**: Supports overriding sub-agent models individually per user account (`subagent_model`, `agent_model`, `sub_agent_model` in accounts database) or globally via env vars (`OPENCODE_SUB_AGENT_MODEL`/`SUB_AGENT_MODEL`), falling back to `gemini-flash-lite` by default.
13. **Cost Tracking Completeness (v2.1+)**: Streaming endpoints (`streamGenerateContent`, SSE) now log usage with actual `model_id` resolved — fixes $0.0000 dashboard bug. All paths (Gemini pass-through, OpenCode proxy, Claude proxy) use resolved `model_id` for accurate pricing lookup against `model_prices` table.


# ROUTER API v2 — ARCHITECTURE & FLOW DIAGRAM

> **File:** `docs/flow_diagram.md`  
> **Target Audience:** Senior Software Engineers & System Architects  
> **Status:** Synchronized 100% with live codebase (Python FastAPI, SQLite WAL, React SPA)  
> **Format:** Pure ASCII diagrams & structured technical analysis  

---

## 1. SYSTEM OVERVIEW

Router API v2 acts as an intelligent, high-availability LLM gateway and proxy. It unifies disparate client SDK protocols (OpenAI Chat, Anthropic Messages, Google Gemini Native) into a resilient routing core that manages multi-tiered accounts, structured API tokens, rate limits, model fallback pools, and real-time telemetry.

```
+--------------------------------------------------------------------------------------------------+
|                                        CLIENT APPLICATIONS                                       |
|               (Claude Code CLI / OpenCode CLI / OpenAI SDK / Web UI Dashboard / cURL)            |
+-------------------------------------------------+------------------------------------------------+
                                                  | HTTP / SSE / WebSocket
                                                  v
+--------------------------------------------------------------------------------------------------+
|                                      SERVER & MIDDLEWARE LAYER                                   |
|                                (src/server/openai_server/routes/app_init.py)                     |
|                                                                                                  |
|   +-----------------------+     +-----------------------+     +------------------------------+   |
|   | TokenLimit Middleware | --> |    CORS Middleware    | --> |      Security Middleware     |   |
|   | (Token concurrency/RP)|     | (Preflight & Headers) |     | (10MB limit / IP rate-limit) |   |
|   +-----------------------+     +-----------------------+     +------------------------------+   |
+-------------------------------------------------+------------------------------------------------+
                                                  |
                                                  v
+--------------------------------------------------------------------------------------------------+
|                                      ROUTING & ADMISSION LAYER                                   |
|   * completions_routes.py (/v1/chat/completions, /v1/messages, /v1/completions, /v1/responses)   |
|   * opencode_routes.py    (/opencode/v1/chat/completions)                                        |
|   * gemini_routes.py      (/v1beta/models/*:generateContent, :streamGenerateContent)             |
|   * dashboard_routes.py   (/dashboard/login, /me, /my/keys, /register, /stats, /endpoints)       |
|   * admin/                (accounts.py, endpoints.py, keys.py, models.py, settings.py)           |
|   * ws_routes.py          (WebSocket /dashboard/ws -> logs & telemetry broadcasting)             |
+-------------------------------------------------+------------------------------------------------+
                                                  |
                                                  v
+--------------------------------------------------------------------------------------------------+
|                                       PROXY FORMAT CONVERTERS                                    |
|   +---------------------------------------------+ +------------------------------------------+   |
|   |         src/api/opencode_proxy/             | |           src/api/claude_proxy/          |   |
|   |  - Prompt sanitization & search injection   | |  - Anthropic <-> OpenAI format convert   |   |
|   |  - Thinking budget normalization            | |  - XML thinking extraction & SSE events  |   |
|   +---------------------------------------------+ +------------------------------------------+   |
|   [Pure Format Converters: NO key management, NO pool retry logic -> Delegate to PoolManager]    |
+-------------------------------------------------+------------------------------------------------+
                                                  |
                                                  v
+--------------------------------------------------------------------------------------------------+
|                                     CENTRALIZED CORE ENGINE                                      |
|                                   (src/core/pool_manager.py)                                     |
|                                                                                                  |
|   +-------------------------+    +--------------------------+    +---------------------------+   |
|   |  Quota Pre-check        |    |  ModelPool State Machine |    |  Error Classifier         |   |
|   |  (Router RPM/TPM limit) | -> |  (Member Acquire & Swap) | -> |  (Transient vs Hard Err)  |   |
|   +-------------------------+    +--------------------------+    +---------------------------+   |
|                                                                                                  |
|   +---------------------------------------------+ +------------------------------------------+   |
|   |         Router & Key Resolver               | |             Rate Limiting Subsystem      |   |
|   |         (src/core/router/core/)             | |             (src/core/limits/)           |   |
|   |  - Double Random key selection algorithm    | |  - TokenLimiter (token concurrency/intv) |   |
|   |  - Adaptive cooldown & circuit breaker      | |  - AccountLimiter (pool sliding-window)  |   |
|   +---------------------------------------------+ +------------------------------------------+   |
+------------------------+------------------------------------------------+------------------------+
                         |                                                |
                         v                                                v
+--------------------------------------------------+ +---------------------------------------------+
|               PROVIDER INTEGRATION               | |              DATABASE & PERSISTENCE         |
|            (src/core/providers/)                 | |                 (src/backend/)              |
|                                                  | |                                             |
|   +------------------------------------------+   | |   +-------------------------------------+   |
|   | gemini_facade (acompletion entrypoint)   |   | |   | usage.db (SQLite WAL Mode, RLock)   |   |
|   |   |                                      |   | |   |  - accounts, account_keys           |   |
|   |   +--> PATH 1: Google GenAI SDK (Native) |   | |   |  - account_credentials, invite_codes|   |
|   |   |    (api_manager client pool & pacing)|   | |   |  - custom_endpoints, model_config  |   |
|   |   |                                      |   | |   |  - key_status, key_penalties        |   |
|   |   +--> PATH 2: Custom Endpoint (OpenAI)  |   | |   +-------------------------------------+   |
|   |        (vLLM / Ollama / DeepSeek / Local)|   | |   +-------------------------------------+   |
|   +------------------------------------------+   | |   | usage_logs.db (Async flush loop)    |   |
+--------------------------------------------------+ +---------------------------------------------+
```

### Explanation:
- **Scope & Direction:** Every inbound request flows from top to bottom: Client -> Server/Middleware -> Admission & Proxies -> PoolManager -> Providers -> SQLite.
- **Central Component:** `PoolManager` (`src/core/pool_manager.py`) is the single orchestrator for retry loops, error recovery, key cooldowns, and member swapping.
- **Key Decoupling:** Proxies (`claude_proxy` and `opencode_proxy`) do not know anything about API keys or retries. They purely translate wire protocols and delegate execution to `PoolManager`.

---

## 2. COMPONENT MAP

| Subsystem | Primary Files | Architectural Responsibility | Key Callers | Key Dependencies | State Mutated |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Server / App Init** | `src/server/openai_server/routes/app_init.py`, `main.py` | FastAPI setup, middleware stack, lifespan events, .env file watcher | Uvicorn | `src.core.config_n_logg`, `src.backend.schema` | Background tasks, active request reset |
| **Middlewares** | `app_init.py`, `src/server/openai_server/security.py` | Token limits enforcement, CORS handling, payload size gating, IP rate limiting | FastAPI pipeline | `token_limiter`, `auth.py` | `request.state.auth_account`, IP attempt counts |
| **Auth Subsystem** | `src/server/openai_server/auth.py`, `src/core/accounts/account_manager.py` | Bearer extraction, token lookup, account caching (10s TTL), sub-agent error simulation | Route handlers | `src.backend.account_keys`, `src.backend.accounts` | In-memory token cache |
| **Proxy Layer** | `src/api/opencode_proxy/`, `src/api/claude_proxy/` | Bi-directional format transformation (Anthropic/OpenAI), search context injection, SSE streaming | Route handlers | `PoolManager`, `logical_HQ_translator` | Temporary message buffers |
| **Pool Manager** | `src/core/pool_manager.py` | Monolithic retry loop, error classification (transient vs hard), member failover | Proxies | `router`, `ModelPool`, `gemini_facade`, `limits` | Key frozen states, penalty scores |
| **Router & Keys** | `src/core/router/core/router.py`, `key_resolver.py` | Double Random key selection, circuit breaker, tier verification, atomic key reservation | `PoolManager` | `src.backend.key_status`, `gemini_rate_limiter` | `key_status` in SQLite and memory |
| **Rate Limiters** | `src/core/limits/token_limiter.py`, `account_limiter/`, `gemini_rate_limiter.py` | Concurrency semaphores, interval enforcement, per-pool sliding-window RPM/TPM/RPD | Middleware, Auth, Router | `src.backend._db` | Token slots, sliding deques, usage records |
| **Providers** | `src/core/providers/gemini_facade.py`, `gemini/`, `custom_endpoint_manager.py` | Google GenAI SDK execution, OpenAI-compatible HTTP client, client pooling and pacing | `PoolManager` | `google-genai` SDK, `httpx` | Active HTTP connections |
| **Database** | `src/backend/_db.py`, `schema.py`, `accounts.py`, `account_keys.py`, `key_status.py` | SQLite WAL connection factory with thread RLock, schema migration, CRUD operations | Auth, Router, Dashboard | SQLite file `usage.db` | Persistent tables on disk |
| **Telemetry** | `src/core/usage_logger.py`, `src/server/stats_pusher.py`, `src/server/log_watcher.py` | Token counting, financial savings metrics, async DB write worker, WebSocket broadcasting | Route handlers, Lifespan | SQLite file `usage_logs.db` | Token metrics log entries |

---

## 3. REQUEST LIFECYCLE

```
[ CLIENT REQUEST ]
        |
        v
+--------------------------------------------------------------------------------------------------+
| STEP 1: TOKEN LIMIT MIDDLEWARE (src/server/openai_server/routes/app_init.py)                     |
| - Calls _resolve_auth() to extract raw token from Authorization: Bearer or x-api-key             |
| - Calls _check_auth() -> looks up token via account_manager cache (or DB fallback)               |
| - Caches account on request.state.auth_account                                                   |
| - IF child token (sk-<name>-<code>):                                                             |
|     token_limiter.acquire() verifies concurrency slots, min_interval_seconds, token RPM/TPM/RPD  |
|     (Rejects immediately with 429 if limit exceeded)                                             |
| - Registers async release hook for middleware exit                                               |
+--------------------------------------------------------------------------------------------------+
        |
        v
+--------------------------------------------------------------------------------------------------+
| STEP 2: SECURITY & CORS MIDDLEWARE                                                               |
| - Checks Content-Length <= 10MB (MAX_BODY_BYTES) -> 413 if exceeded                              |
| - Frontend rate limit check (login 5/min, dashboard 60/min) -> 429 if exceeded                  |
| - Handles OPTIONS preflight immediately (200 OK)                                                 |
| - Injects secure response headers (HSTS, X-Content-Type-Options: nosniff, X-Frame-Options: DENY) |
+--------------------------------------------------------------------------------------------------+
        |
        v
+--------------------------------------------------------------------------------------------------+
| STEP 3: ROUTE ADMISSION & CONTEXT LENGTH VALIDATION (e.g. completions_routes.py)                 |
| - Reads request.json()                                                                           |
| - Estimates input tokens: len(prompt) // 4                                                       |
| - Compares estimated tokens against model's context_length limit (MODEL_CONTEXT_LENGTH)          |
|   -> If exceeded: "Active Reject" with 400 invalid_request_error                                 |
| - Calls _apply_account_limit(account, body) -> acquires sliding-window pool capacity              |
+--------------------------------------------------------------------------------------------------+
        |
        v
+--------------------------------------------------------------------------------------------------+
| STEP 4: PROXY PROTOCOL NORMALIZATION (src/api/claude_proxy/ or src/api/opencode_proxy/)         |
| - Translates client payload into standardized internal structures                               |
| - Injects web search context if account.web_search_enabled == 1                                  |
| - Injects image-disabling system instructions for Claude Code sessions                           |
| - Normalizes thinking configuration (effort words: low/medium/high/max)                          |
+--------------------------------------------------------------------------------------------------+
        |
        v
+--------------------------------------------------------------------------------------------------+
| STEP 5: CENTRAL POOL MANAGER EXECUTION (src/core/pool_manager.py)                                 |
| - Resolves target model alias to ModelPool or Standalone mode                                    |
| - Acquires pool member worker lock (e.g., gemini-flash-35)                                       |
| - Inside execution loop:                                                                         |
|     1. _resolve_model() -> invokes router.reserve_key() via Double Random algorithm              |
|     2. router.acquire_quota() -> checks per-model RPM and TPM limits                             |
|     3. gemini_facade.acompletion() -> dispatches to GenAI SDK or Custom Endpoint                 |
|     4. If error occurs -> _classify_error():                                                     |
|          * Transient Error (429, timeout, overloaded): records 429, freezes key, applies penalty |
|            If consecutive failures >= 3 -> SWAPS member (flash-35 -> flash-30 -> flash-25)      |
|            Else -> sleeps with exponential backoff and retries                                   |
|          * Hard Error (401 invalid, 403 denied, billing): freezes key, swaps member immediately  |
|     5. Always releases API key concurrency in finally block                                      |
+--------------------------------------------------------------------------------------------------+
        |
        v
+--------------------------------------------------------------------------------------------------+
| STEP 6: RESPONSE FORMATTING & STREAMING                                                          |
| - Streaming: yields SSE events (Anthropic spec: message_start, content_block_delta, etc.)        |
|   * Enforces 4.0s ping keepalive during prolonged Gemini thinking phases                         |
|   * CRITICAL: Once first chunk is sent (committed=True), errors CANNOT be retried with new key   |
| - Non-Streaming: constructs OpenAI or Anthropic compliant JSON response                          |
+--------------------------------------------------------------------------------------------------+
        |
        v
+--------------------------------------------------------------------------------------------------+
| STEP 7: TELEMETRY & CONCURRENCY RELEASE                                                          |
| - Dispatches async token counts to usage_logger.log_usage() -> queued for batch insert           |
| - TokenLimit Middleware release hook executes: decrements token concurrency slot                 |
| - Returns final HTTP response to client                                                          |
+--------------------------------------------------------------------------------------------------+
```

### Explanation:
- **Admission Safety:** Active Reject at Step 3 prevents context overflow from reaching external providers, preserving quota.
- **Fail-Safe Sub-Agent Errors:** If an API error hits a sub-agent request (e.g. `explore` / `grep` in Claude Code), Router API catches the error and responds with an HTTP 200 containing a simulated markdown error warning. This prevents the parent CLI tool from crashing abruptly.
- **Stream Commit Rule:** In `pool_manager.py`, the moment `committed = True` (first byte yielded to client), retry loops are terminated. Re-trying across models after headers are flushed would corrupt client SSE streams.

---

## 4. AUTHENTICATION & ACCOUNT FLOW

Router API v2 implements a two-tier token hierarchy, separating administrative root keys from application-level worker tokens.

```
                      INCOMING AUTHENTICATION TOKEN
                                    |
         +--------------------------+--------------------------+
         |                                                     |
         v                                                     v
[ MASTER KEY FORMAT ]                                [ CHILD TOKEN FORMAT ]
  Pattern: sk-<32 random hex>                          Pattern: sk-<account_name>-<6-char code>
  Example: sk-4f9e1b2a3c...                            Example: sk-azuree-x8k2m1
         |                                                     |
         v                                                     v
+---------------------------------+                 +--------------------------------------+
| Look up accounts.auth_key       |                 | Decompose string:                    |
| - Found: Account Root Admin     |                 | - name = "azuree"                    |
| - Bypasses token concurrency    |                 | - token_code = "x8k2m1"              |
| - Bypasses token quota ceilings |                 +------------------+-------------------+
| - Grants account level control  |                                    |
+---------------------------------+                                    v
                                                    +--------------------------------------+
                                                    | Query account_keys table:            |
                                                    | SELECT * FROM account_keys           |
                                                    | WHERE name = ? AND token_code = ?    |
                                                    +------------------+-------------------+
                                                                       |
                                         +-----------------------------+-----------------------+
                                         | Found & enabled == 1                                | Not found / enabled == 0
                                         v                                                     v
                      +--------------------------------------+                      [ HTTP 401 UNAUTHORIZED ]
                      | Query parent account:                |
                      | SELECT enabled FROM accounts         |
                      | WHERE account_id = key.account_id    |
                      +------------------+-------------------+
                                         |
                       +-----------------+-----------------+
                       | enabled == 1                      | enabled == 0 (Account Disabled)
                       v                                   v
        +-------------------------------+       [ HTTP 401 UNAUTHORIZED ]
        | TOKEN LIMIT ADMISSION CHECK   |       (Disabling parent account blocks
        | - active_slots < max_concurr  |        ALL child tokens instantly)
        | - interval >= min_interval_s  |
        | - rpm, tpm, rpd within limits |
        +---------------+---------------+
                        |
            +-----------+-----------+
            | Pass                  | Fail
            v                       v
     [ ADMIT REQUEST ]     [ HTTP 429 RATE LIMIT ]
```

### Web Dashboard & Session Authentication

```
+--------------------------------------------------------------------------------------------------+
| 1. DASHBOARD LOGIN (POST /dashboard/login)                                                       |
|    - Input: username + password (or legacy auth_key)                                             |
|    - Logic: verify_login_db() verifies password against account_credentials                      |
|             (PBKDF2 SHA-256 with per-account salt)                                               |
|    - Output: HMAC-SHA256 signed Session Token (8-hour TTL, containing account_id, name, tier)    |
+--------------------------------------------------------------------------------------------------+
                                                  |
                                                  v
+--------------------------------------------------------------------------------------------------+
| 2. DEFAULT PASSWORD WARNING (must_change flag)                                                   |
|    - If initial password is "1234", must_change is set to 1 in account_credentials              |
|    - Dashboard displays persistent security alert prompting user to update password              |
|    - User calls POST /dashboard/password to update hash and clear must_change                    |
+--------------------------------------------------------------------------------------------------+
                                                  |
                                                  v
+--------------------------------------------------------------------------------------------------+
| 3. SELF-REGISTRATION VIA INVITE CODES (POST /dashboard/register)                                 |
|    - Admin issues single-use invite code via POST /dashboard/admin/invites/issue (TTL 3-15m)     |
|    - Public user submits invite code, username, and password                                     |
|    - consume_invite_db() atomically marks invite as used (used_at = now)                         |
|    - System provisions accounts record, sets credentials, and mints initial child token          |
+--------------------------------------------------------------------------------------------------+
```

### Explanation:
- **Index-Friendly Token Storage:** Auth tokens are stored decomposed (`name` and `token_code`) in `account_keys`. This avoids expensive `LIKE` wildcard SQL scans and enables fast index hits via `idx_account_keys_name_code`.
- **Immediate Invalidation:** When an administrator toggles an account off (`enabled = 0`), `account_manager.invalidate_cache()` is called. The very next request from any of that account's child tokens fails authentication immediately.

---

## 5. API ROUTING FLOW

```
                          FASTAPI APPLICATION (app_init.py)
                                          |
   +-----------------------+--------------+-------------+-----------------------+
   |                       |                            |                       |
   v                       v                            v                       v
[ OPENAI COMPATIBLE ]   [ ANTHROPIC COMPATIBLE ]     [ NATIVE GEMINI ]     [ DASHBOARD & ADMIN ]
   |                       |                            |                       |
   * POST /v1/chat/        * POST /v1/messages          * POST /v1beta/         * POST /dashboard/login
     completions           * (claude_proxy)               models/*:             * GET  /dashboard/me
   * POST /v1/completions  * Image input banned           generateContent       * CRUD /dashboard/my/
   * POST /v1/responses    * Thinking parameter         * POST /v1beta/                keys/*
   * POST /opencode/v1/      translated to XML            models/*:stream       * CRUD /dashboard/admin/
     chat/completions      * 4s keepalive ping            GenerateContent              accounts/*
   * (opencode_proxy)        engine                     * (pass_through)        * WS   /dashboard/ws
   |                       |                            |                       |
   +-----------+-----------+                            |                       |
               |                                        |                       |
               v                                        v                       v
    +--------------------+                     +-----------------+     +-----------------+
    | Proxy Layer        |                     | gemini_handlers |     | Admin / DB      |
    | (Format Converter) |                     | & Parsers       |     | Handlers        |
    +----------+---------+                     +--------+--------+     +--------+--------+
               |                                        |                       |
               v                                        |                       |
    +-----------------------------------------------+   |                       |
    | PoolManager (src/core/pool_manager.py)        |<--+                       |
    | - Quota gate check                            |                           |
    | - Member acquisition & swap loop              |                           |
    | - Error classification & retry                |                           |
    +-----------------------+-----------------------+                           |
                            |                                                   |
                            v                                                   v
    +-----------------------------------------------+                  +-----------------+
    | Provider Execution (GenAI SDK / Custom HTTP)  |                  | SQLite WAL      |
    +-----------------------------------------------+                  | (usage.db)      |
                                                                       +-----------------+
```

### Explanation:
- **Uniform Delegation:** Notice that both the OpenAI routes (`completions_routes.py`, `opencode_routes.py`) and Anthropic routes (`claude_proxy`) funnel directly into `PoolManager`.
- **WebSocket Route (`ws_routes.py`):** Operates alongside REST APIs. Clients authenticate via session token and subscribe to `logs` or `stats` channels. `log_watcher.py` (file tailer) and `stats_pusher.py` (telemetry ticker) broadcast events asynchronously over these channels.

---

## 6. DATABASE & PERSISTENCE FLOW

The system maintains two distinct SQLite databases configured in WAL (Write-Ahead Logging) mode, governed by an in-process threading re-entrant lock (`_LOCK` in `src/backend/_db.py`).

```
+--------------------------------------------------------------------------------------------------+
| DATABASE 1: usage.db (Configuration, Accounts, Keys & Pricing)                                   |
+--------------------------------------------------------------------------------------------------+
|                                                                                                  |
|   accounts                        account_keys                    account_credentials            |
|   +--------------------------+    +--------------------------+    +--------------------------+   |
|   | account_id (PK) TEXT     |<---| account_id TEXT          |    | account_id (PK) TEXT     |   |
|   | name TEXT UNIQUE         |    | key_id (PK) TEXT         |    | password_hash TEXT       |   |
|   | auth_key TEXT (Master)   |    | name TEXT                |    | password_salt TEXT       |   |
|   | enabled INTEGER          |    | token_code TEXT          |    | must_change INTEGER      |   |
|   | rpm, tpm, rpd INTEGER    |    | enabled INTEGER          |    | updated_at INTEGER       |   |
|   | tier TEXT ('free'/'prem')|    | rpm, tpm, rpd INTEGER    |    +--------------------------+   |
|   | web_search_enabled INT   |    | max_concurrency INTEGER  |                                   |
|   | search_engine TEXT       |    | min_interval_seconds REAL|    invite_codes                   |
|   | created_at, updated_at   |    | label TEXT               |    +--------------------------+   |
|   +--------------------------+    +--------------------------+    | code (PK) TEXT (4-6 char)|   |
|                                                                   | created_by TEXT          |   |
|   custom_endpoints                model_config                    | created_at, expires_at   |   |
|   +--------------------------+    +--------------------------+    | used_at, used_by TEXT    |   |
|   | name (PK) TEXT           |    | alias (PK) TEXT          |    +--------------------------+   |
|   | base_url TEXT            |    | account_id (PK) TEXT     |                                   |
|   | auth_key TEXT            |    | model_id TEXT            |    model_prices                   |
|   | enabled INTEGER          |    | rpm, tpm, rpd INTEGER    |    +--------------------------+   |
|   | models, enabled_models   |    | context_length INTEGER   |    | model_name (PK) TEXT     |   |
|   | pool_assignments TEXT    |    | pool_name TEXT           |    | input_rate_per_1k REAL   |   |
|   +--------------------------+    +--------------------------+    | output_rate_per_1k REAL  |   |
|                                                                   +--------------------------+   |
|   key_status                      key_penalties                                                  |
|   +--------------------------+    +--------------------------+                                   |
|   | key (PK) TEXT            |    | pkey (PK) TEXT           |                                   |
|   | enabled INTEGER          |    | api_key TEXT             |                                   |
|   | usage, active_requests   |    | model_id TEXT            |                                   |
|   | frozen_until REAL        |    | reason TEXT              |                                   |
|   | consecutive_failures INT |    | expires REAL             |                                   |
|   | per_model TEXT           |    | score_reduction INTEGER  |                                   |
|   +--------------------------+    +--------------------------+                                   |
+--------------------------------------------------------------------------------------------------+

+--------------------------------------------------------------------------------------------------+
| DATABASE 2: usage_logs.db (High-Throughput Analytics & Telemetry)                                |
+--------------------------------------------------------------------------------------------------+
|                                                                                                  |
|   usage_logs                                                                                     |
|   +------------------------------------------------------------------------------------------+   |
|   | id INTEGER PRIMARY KEY AUTOINCREMENT                                                     |   |
|   | timestamp INTEGER, model TEXT, auth_key_prefix TEXT, key_prefix TEXT                     |   |
|   | prompt_tokens INTEGER, completion_tokens INTEGER                                         |   |
|   | cache_creation_input_tokens INTEGER, cache_read_input_tokens INTEGER                     |   |
|   +------------------------------------------------------------------------------------------+   |
|   (Populated asynchronously via in-memory deque and background flush loop every 5 seconds)       |
+--------------------------------------------------------------------------------------------------+
```

### In-Memory State & Cache Hierarchy

```
[ IN-MEMORY STATE ]                                    [ PERSISTENT SOURCE OF TRUTH ]
* account_manager._cache (TTL 10s)         <-------->  * accounts & account_keys tables
* APIRouter._key_status dict               <-------->  * key_status & key_penalties tables
* APIRouter._model_health dict             <-------->  * Dynamic runtime score (mean reverting)
* custom_endpoint_manager._cache (TTL 5s)  <-------->  * custom_endpoints table
* token_limiter concurrency slots          <-------->  * Ephemeral runtime counters
* usage_logger._buffer deque               --------->  * usage_logs table (usage_logs.db)
```

### Explanation:
- **Separation of Concerns:** High-frequency logging is isolated into `usage_logs.db` so heavy analytical writes never lock `usage.db` transaction locks during key reservation.
- **Thread Safety:** `src/backend/_db.py` exposes a shared `_LOCK` (threading `RLock`). All writers synchronize through this lock before acquiring SQLite connection pointers.

---

## 7. PROVIDER & AI ROUTING FLOW

```
                          REQUEST ENTERING POOL MANAGER
                                        |
                 +----------------------+----------------------+
                 |                                             |
                 v                                             v
          [ POOL MODE ]                                [ STANDALONE MODE ]
     (gemini-flash / lite pool)                        (Direct model call)
                 |                                             |
     pool.acquire(member)                                      |
     (Timeout bounded)                                         |
                 |                                             |
                 +----------------------+----------------------+
                                        |
                                        v
+--------------------------------------------------------------------------------------------------+
| DOUBLE RANDOM KEY SELECTION (src/core/router/core/key_resolver.py)                              |
|                                                                                                  |
|  1. Candidate Filtering:                                                                         |
|     - Key enabled in database                                                                    |
|     - frozen_until < now (not globally frozen)                                                   |
|     - Circuit breaker closed (consecutive_failures < threshold)                                  |
|     - Tier matches account permissions (free / premium / admin)                                  |
|     - allowed_pools matches requested pool                                                       |
|     - active_requests == 0 (idle key)                                                            |
|     - per_model[model_id].frozen_until < now                                                     |
|     - RPM and TPM sliding window checks pass                                                     |
|                                                                                                  |
|  2. Priority Scoring:                                                                            |
|     Priority = Base_Tier_Score - Active_Penalties                                                |
|     Sort candidates: active_requests (asc) -> Priority (desc) -> failures (asc)                 |
|                                                                                                  |
|  3. Double Random Selection:                                                                     |
|     Take TOP 50% healthiest candidates:                                                          |
|     chosen_key = random.choice(candidates[: max(1, len(candidates) // 2)])                       |
|                                                                                                  |
|  4. Atomic Reservation:                                                                          |
|     atomic_reserve_key() updates active_requests++ in SQLite and memory                          |
+--------------------------------------------------------------------------------------------------+
                                        |
                                        v
+--------------------------------------------------------------------------------------------------+
| PROVIDER EXECUTION (src/core/providers/gemini_facade.py)                                         |
|                                                                                                  |
|  IF Provider == "gemini":                                                                        |
|     - api_manager.pool.throttle(api_key) applies pacing (1.0s - 2.6s per key)                    |
|     - client = api_manager.pool.get_client(api_key)                                              |
|     - Non-stream: client.models.generate_content()                                               |
|     - Stream: client.aio.models.generate_content_stream()                                        |
|  IF Provider == "custom":                                                                        |
|     - custom_endpoint_client dispatches HTTP request to OpenAI-compatible base_url               |
+--------------------------------------------------------------------------------------------------+
                                        |
         +------------------------------+------------------------------+
         | Success                                                     | Exception Raised
         v                                                             v
+---------------------------------+                 +--------------------------------------+
| SUCCESS PATH:                   |                 | ERROR CLASSIFICATION (_classify_error|
| - router.release_key(api_key)   |                 | - Transient: 429, timeout, overload, |
| - router.record_success()       |                 |   unavailable, quota_exhausted       |
| - router.update_model_health()  |                 | - Hard: 401 invalid, 403 denied,     |
| - Return payload / stream chunks|                 |   billing_error, bad_request         |
+---------------------------------+                 +------------------+-------------------+
                                                                       |
                                         +-----------------------------+-----------------------+
                                         | Transient Error                                     | Hard Error
                                         v                                                     v
                      +--------------------------------------+              +----------------------+
                      | - router.record_429()                |              | - Freeze key long    |
                      | - router.freeze_key(cooldown)        |              |   or disable         |
                      | - apply_error_penalty(reason)        |              | - Apply heavy penalty|
                      | - consecutive_transient++            |              | - SWAP member        |
                      +------------------+-------------------+              |   immediately        |
                                         |                                  +----------+-----------+
                       +-----------------+-----------------+                           |
                       | consecutive >= 3                  | consecutive < 3           |
                       v                                   v                           |
              [ SWAP POOL MEMBER ]               [ RETRY SAME MEMBER ]                 |
              (flash-35 -> flash-30              (Sleep with exponential               |
               -> flash-25)                       backoff _retry_delay)                |
                       |                                   |                           |
                       +-----------------+-----------------+                           |
                                         |                                             |
                                         v                                             |
                      pool.acquire(skip=exhausted_members) <---------------------------+
                      (Loop continues until pool.max_retry_seconds exhausted)
```

### Explanation:
- **Double Random Prevention of Thundering Herd:** In high-concurrency environments, picking strictly the highest-ranked key causes all workers to hit the same key at the same millisecond, inducing 429 errors. Picking randomly from the top 50% healthy pool distributes the load evenly.
- **Circuit Breaker Mechanics:** If 15 consecutive 429 errors occur across the entire fleet, `APIRouter.record_429()` trips a global breaker, freezing admissions for 10-20 seconds to allow upstream provider quotas to recover.

---

## 8. FRONTEND FLOW

The user interface is a React Single Page Application (SPA) located in `frontend-src/`, built via Vite into `src/frontend/`, and mounted directly by FastAPI.

```
+--------------------------------------------------------------------------------------------------+
| BROWSER RUNTIME (React SPA in frontend-src/)                                                     |
|                                                                                                  |
|  App.jsx + AppContext.jsx (Central State Container)                                              |
|  ├── session_token, user_tier ('admin' / 'premium' / 'free'), active_tab                         |
|  └── Data stores: accounts, user_keys, endpoints, model_pools, live_logs, system_stats           |
+--------------------------------+------------------------------------------------+----------------+
                                 |                                                |
               HTTP REST API     |                                                | WebSocket
               (JSON Payloads)   v                                                v (/dashboard/ws)
+--------------------------------------------------+ +---------------------------------------------+
| REST ENDPOINTS (dashboard_routes.py & admin/)    | | WEBSOCKET PIPELINE (websocket_manager.py)   |
|                                                  | |                                             |
|  * POST /dashboard/login                         | |  1. Client connects with session token      |
|  * POST /dashboard/register                      | |  2. Subscribes to channels:                 |
|  * POST /dashboard/password (must_change warning) | |     - "logs": Real-time stdout stream       |
|  * GET  /dashboard/me (user quota & limits)      | |     - "stats": Periodic system health tick  |
|  * CRUD /dashboard/my/keys/* (self-service)      | |  3. log_watcher.py reads rotating log files |
|  * CRUD /dashboard/admin/accounts/* (admin only) | |  4. stats_pusher.py polls internal telemetry|
|  * CRUD /dashboard/admin/endpoints/*             | |  5. ws_manager broadcasts to subscribers    |
+--------------------------------------------------+ +---------------------------------------------+
                                 |                                                |
                                 +-----------------------+------------------------+
                                                         |
                                                         v
                                           [ UI REACTIVE UPDATE ]
                                           - Re-render metric gauges
                                           - Append virtualized log entries
                                           - Display modal alerts / toast errors
```

### Frontend Tabs Breakdown:
1. **OverviewTab:** Real-time throughput (RPM/TPM), model health indicators, capacity gauges.
2. **MyAccountTab:** User-specific quotas, active personal auth tokens (`sk-<name>-<code>`), self-service token minting/editing, password change modal.
3. **AccountsTab (Admin Only):** User accounts table, invite code generation modal, ability to view and control auth tokens for any account, Master Key copy/rotation controls.
4. **EndpointsTab:** Custom endpoint configuration (Ollama, vLLM, DeepSeek), model discovery ping, and pool binding.
5. **ModelPoolsTab:** Visualization of `gemini-flash` and `gemini-flash-lite` fallback chains.
6. **LiveLogsTab:** Log stream viewer filtering by subsystem (`proxy`, `api`, `keys`, `system`).
7. **SystemStatsTab:** Token consumption metrics, historical charts, and financial cost-savings estimation against commercial Claude 3.7 Sonnet pricing.

---

## 9. TEST ARCHITECTURE

The test suite runs via `pytest` and verifies security, routing resilience, protocol conformity, and database integrity.

```
+--------------------------------------------------------------------------------------------------+
| PYTEST TEST SUITE (tests/)                                                                       |
+--------------------------------------------------------------------------------------------------+
|                                                                                                  |
|  [ AUTH & CREDENTIALS ]               [ POOL & RESILIENCE ]             [ PROTOCOL & PARSING ]   |
|  * test_account_auth.py               * test_pool.py                    * test_anthropic_        |
|    - Token decomposition & indexing     - ModelPool member acquire        integration.py         |
|    - Concurrency slot release           - Member swap on failures       * test_anthropic_        |
|  * test_master_key.py                 * test_retry_counter.py             protocol.py            |
|    - Master key format validation       - Exponential backoff pacing    * test_effort_mapping.py |
|    - Quota exemption verification     * test_transient_penalties.py     * test_message_          |
|  * test_invite_codes.py                 - Penalty scoring on 429          converter_truncation.py|
|    - One-time consumption semantics   * test_custom_pool_resolver.py    * test_rtk_filters.py    |
|  * test_dashboard_tokens.py                                                                      |
|                                                                                                  |
|  [ DATABASE & BOOTSTRAP ]             [ SECURITY & SCRIPTS ]            [ TOOL & SEARCH ]        |
|  * test_schema_bootstrap.py           * test_secret_scanner.py          * test_search_endpoint.py|
|    - Table creation & migration         - Git leak prevention checks    * test_adk_runner.py     |
+--------------------------------------------------------------------------------------------------+
                                          |
                                          v
+--------------------------------------------------------------------------------------------------+
| TEST BOUNDARIES & MOCKING                                                                        |
| - In-Memory SQLite Fixtures: Tests execute against transient in-memory SQLite tables             |
| - Mocked GenAI SDK: External Google API calls are stubbed to test failover deterministically     |
| - Time Travel Mocking: freezegun / unittest.mock used to verify 429 expiration & midnight resets |
+--------------------------------------------------------------------------------------------------+
```

---

## 10. IMPORTANT DEPENDENCY GRAPH

```
src.server.openai_server.routes.app_init
  |
  +---> src.server.openai_server.routes.completions_routes
  |       |
  |       +---> src.api.claude_proxy.claude_proxy
  |       |       |
  |       |       +---> src.logical_HQ_translator
  |       |       |
  |       |       +---> src.core.pool_manager.pool_manager
  |       |
  |       +---> src.api.opencode_proxy.opencode_proxy
  |               |
  |               +---> src.core.pool_manager.pool_manager
  |
  +---> src.server.openai_server.routes.dashboard_routes
  |       |
  |       +---> src.backend.account_keys
  |       +---> src.backend.accounts
  |       +---> src.core.accounts.account_manager
  |
  +---> src.core.pool_manager.pool_manager
          |
          +---> src.core.router.core.router (APIRouter)
          |       |
          |       +---> src.core.router.core.key_resolver (KeyResolverMixin)
          |       +---> src.core.router.pool (ModelPool)
          |       +---> src.backend.key_status
          |
          +---> src.core.providers.gemini_facade
          |       |
          |       +---> src.core.providers.gemini (GeminiAPIManager)
          |       +---> src.core.providers.custom_endpoint_client
          |
          +---> src.core.limits (token_limiter, account_limiter, gemini_rate_limiter)
```

---

## 11. LEGACY & DEPRECATED PATHS

1. **Legacy JSON Migration (`schema.migrate_from_json()`):**
   - *Status:* Maintained for backward compatibility.
   - *Behavior:* If legacy `.json` credential files exist on disk, startup lifespan migrates records into SQLite and renames the JSON files to prevent repeated imports.
2. **Legacy Master Key Login in Web UI:**
   - *Status:* Supported in `POST /dashboard/login`.
   - *Behavior:* If a user inputs an `auth_key` instead of a username/password pair, the endpoint resolves the account by key. New deployments should transition exclusively to username/password credentials.
3. **Sunset Gemini 2.5 Models (`is_sunset_25()` in `src/core/api_config.py`):**
   - *Status:* Dynamic deprecation switch.
   - *Behavior:* Filters out `gemini-2.5-flash` and `gemini-2.5-flash-lite` from pool rotation once the configured sunset date elapses.

---

## 12. CRITICAL COUPLING POINTS & REGRESSION RISKS

1. **Monolithic `PoolManager` (`src/core/pool_manager.py`):**
   - *Design Choice:* Kept intentionally monolithic (600+ lines) to avoid fragmented state management during complex async retries.
   - *Regression Risk:* Modifying error classification or reservation logic in `PoolManager` directly impacts both Claude Code and OpenCode clients simultaneously.
2. **Global SQLite Lock (`_LOCK` in `src/backend/_db.py`):**
   - *Design Choice:* SQLite write operations are serialized with a threading `RLock`.
   - *Regression Risk:* Never execute slow network I/O or `await asyncio.sleep()` while holding `_LOCK` or inside an uncommitted database context.
3. **Streaming State Commitment:**
   - *Design Choice:* Once `committed = True` in `call_stream()`, retries are prohibited.
   - *Regression Risk:* Altering chunk yielding logic to buffer too aggressively could delay client streaming, while failing to catch mid-stream disconnects could leave dangling active requests.
4. **Token Wire String Decomposition:**
   - *Design Choice:* The wire token is strictly `sk-<account_name>-<6-char code>`.
   - *Regression Risk:* Account names must not contain dashes or special characters that collide with delimiter parsing in `compose_token` and `decompose_token`.

---

## 13. ARCHITECTURE SUMMARY

1. **Decoupled Layers:** Client protocol parsing is completely isolated from provider selection and retry logic. Proxies only normalize format; `PoolManager` governs execution.
2. **Resilience by Design:** The system tolerates high failure rates from external LLM APIs via Double Random key load-balancing, sliding-window rate tracking, adaptive key cooldowns, and automatic pool member failover.
3. **Enterprise Token Control:** Organizations can provision distinct child tokens with dedicated rate limits and concurrency ceilings under a single account, while administrators retain master kill-switches.
4. **Zero Code Discrepancies:** All structures, routes, schemas, and algorithms documented herein match the production codebase verbatim.

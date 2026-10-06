# Router API

**Anthropic · OpenAI · Gemini → one gateway.** Key rotation, rate limiting, circuit breaking and a live dashboard. Drop-in for Claude Code, OpenCode, or anything speaking the Anthropic Messages API.

[English](README.md) · **[Tiếng Việt](README_VN.md)**

[![Trustabl Agent Scanner](https://github.com/azizu1012/Router-API/actions/workflows/trustabl.yml/badge.svg)](https://github.com/azizu1012/Router-API/actions/workflows/trustabl.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

## Why

Running a coding agent against a single provider key is fragile. You hit 429s mid-task, a key dies and the whole session stalls, and you have no idea which model actually served yesterday's tokens.

Router API sits in front of a **pool of Gemini keys** and a **pool of interchangeable models**, and hides all of it behind three client-compatible protocols:

- **Anthropic Messages API** — Claude Code talks to it unmodified
- **OpenAI Chat Completions** — OpenCode, Cline, anything
- **Gemini native** — pass-through for `generateContent` / `streamGenerateContent`

When a key gets rate limited, the router cools it down and moves on. When a model in the pool starts failing, it swaps members. Your client sees a normal response.

---

## Quick start

```bash
git clone https://github.com/azizu1012/Router-API.git
cd Router-API

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env             # then fill in GEMINI_API_KEY_1..N
python main.py
```

Dashboard: **http://127.0.0.1:58100/stats**

`main.py` kills any stale process on the port before starting, so re-running is safe.

### Create an account

```bash
python -m src.console.admin_console create coder
python -m src.console.admin_console list --show-keys
```

Accounts are the auth layer in front of the key pool — each gets its own RPM/TPM/RPD budget, so one user cannot exhaust the pool for everyone.

---

## Client setup

### Claude Code (Anthropic Messages API)

Via `settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:58100",
    "ANTHROPIC_AUTH_TOKEN": "sk-<account-key>"
  }
}
```

Or shell:

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

Or `opencode.json`:

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

## How routing works

```
Client ──► Proxy layer      format conversion only, no key/pool logic
        ──► PoolManager     retry loop, error classification, member swap
        ──► APIRouter       model + key resolution
        ──► KeyResolver     circuit breaker, adaptive cooldown, Double Random
        ──► GeminiRateLimiter   sliding-window RPM/TPM per key
        ──► GeminiFacade / Custom endpoint
```

Proxies are deliberately dumb: they translate protocols and delegate everything else. All resilience logic lives in `PoolManager`, so a fix there applies to every client protocol at once.

### Virtual model pools

You request `gemini-flash`. Behind it sits an ordered set of interchangeable members:

| Pool | Members | Notes |
|---|---|---|
| `gemini-flash` | `3.8` · `3.7` · `3.6` · `3.5` · `3.0` · `2.5` | 3.7 is capacity-constrained upstream, so its RPM is throttled |
| `gemini-flash-lite` | `3.5-lite` · `3.1` · `2.5` | cheaper tier for sub-agents |

Each member has its own budget. `ModelPool` gives one member one concurrent request at a time, so a slow model cannot starve the others. After `POOL_SWAP_FAILURES` consecutive failures a member is excluded and the router moves to the next.

Aliases follow `gemini-flash-<version>` → `gemini-<version>-flash`. Override any backing model via env (see `.env.example`).

### Resilience

- **Transient vs permanent errors** — `rate_limit` / `unavailable` / `timeout` trigger a short cooldown and a retry; `invalid_key` / `permission_denied` freeze the key for an hour.
- **Double Random** — key choice is randomised over the healthiest 50%, and retry delays carry ±20% jitter. Without both, concurrent requests stampede onto one key and cascade into 429s.
- **Additive sliding window** — RPM/TPM counted in a deque under an `asyncio.Lock`, so the check-and-update is atomic.
- **Extreme mode** — after repeated failures, quotas tighten to 70% and only fully idle keys are used, as a deliberate thermal cutoff.

Custom endpoints (any OpenAI-compatible provider) are first-class pool members via `pool_assignments`, not a side channel.

---

## Web search

Three ways in, all optional:

| Mode | Endpoint | Uses Gemini quota? |
|---|---|---|
| Client-side search | `POST /v1/search` | no |
| Server-side tool loop | `web_search: true` on a completion | yes |
| DuckDuckGo scraper | `search_engine: "duckduckgo"` | **no** |

The DuckDuckGo path never touches a Gemini key, so search-heavy agent loops cost nothing against your key budget.

---

## Endpoints

| Method | Path | Protocol |
|---|---|---|
| `POST` | `/v1/messages`, `/messages` | Anthropic Messages (stream + non-stream) |
| `POST` | `/v1/messages/count_tokens` | Anthropic |
| `POST` | `/v1/chat/completions` | OpenAI |
| `POST` | `/opencode/v1/chat/completions` | OpenAI |
| `POST` | `/v1/responses` | OpenAI Responses |
| `POST` | `/v1/models/{id}:generateContent` | Gemini native (pass-through) |
| `GET` | `/v1/models` | OpenAI **and** Anthropic schema superset |
| `POST` | `/v1/search`, `/search` | Router |
| `GET` | `/dashboard/*` | Dashboard API |
| `WS` | `/dashboard/ws` | Live dashboard stream |
| `GET` | `/health`, `/preflight` | Ops |

---

## Configuration

Everything is env-driven; `.env.example` is the reference. The keys that matter most:

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY_1..N` | — | Your key pool. Auto-detected. |
| `ROUTER_API_PORT` | `58100` | Listen port |
| `ROUTER_API_MAX_OUTPUT_TOKENS` | `8192` | Hard output cap |
| `POOL_SWAP_FAILURES` | `5` | Transient errors before a member is swapped |
| `KEY_429_COOLDOWN_SECONDS` | `15` | Cooldown after a rate limit |
| `KEY_INVALID_COOLDOWN_SECONDS` | `3600` | Freeze after an invalid key |
| `*_RPM` / `*_TPM` / `*_RPD` | per model | Per-model budgets |
| `MODEL_CONTEXT_LENGTH` | `220000` | Context ceiling used for pool math |

> Keys share a project quota in practice — adding more keys to one project does not multiply throughput. The per-model RPM values reflect that.

---

## Custom endpoints

Any OpenAI-compatible provider can join the pool:

```bash
python -m src.console.admin_console endpoint add my-provider https://api.example.com/v1
python -m src.console.admin_console endpoint set-model my-provider my-model
python -m src.console.admin_console endpoint assign my-provider pool gemini-flash:flash
```

The endpoint's model becomes a real pool member with its own slot and concurrency. If the model is not in `MODEL_POOLS`, it falls back to standalone mode (no acquire/release).

---

## Deploying

See **[DEPLOY_DOMAIN.md](DEPLOY_DOMAIN.md)** for putting this behind a domain with Caddy or Nginx — HTTPS, reverse proxy, systemd unit.

Single-worker only. The SQLite locking is process-local, so multiple Uvicorn workers can hit `database is locked`.

---

## Development

```bash
pytest                                    # full suite
pytest tests/test_anthropic_protocol.py   # protocol conformance
```

CI runs two advisory workflows: the [secret scanner](https://github.com/azizu1012/Router-API/actions/workflows/secret_scan.yml) and the [Trustabl agent scanner](https://github.com/azizu1012/Router-API/actions/workflows/trustabl.yml). Neither blocks a build, and the test suite runs locally — see `docs/ci_workflow.md`.

Further reading in `docs/`:

- **[architecture_overview.md](docs/architecture_overview.md)** — components, data flow, design trade-offs
- **[routing_and_resilience.md](docs/routing_and_resilience.md)** — error taxonomy, Double Random, extreme mode
- **[frontend_dashboard.md](docs/frontend_dashboard.md)** — dashboard architecture and state
- **[bug-logs.md](docs/bug-logs.md)** — diagnosed bugs and why they were fixed

---

## Contributing

Issues and PRs welcome. The Trustabl scanner PR that added the CI workflow is a good model for what a useful contribution looks like: findings backed by a concrete failure mode, not a generic pattern match.

If you change request parsing or response formatting, add a test. `tests/test_anthropic_protocol.py` and `tests/test_anthropic_integration.py` exist precisely because protocol regressions are silent — the response still parses, it is just wrong.

---

## License

[MIT](LICENSE) © 2026 azizu1012

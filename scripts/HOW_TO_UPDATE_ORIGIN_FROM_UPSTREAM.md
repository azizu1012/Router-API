# How to update origin from upstream (fork Nine-router-fort)

## Who is who

| Remote   | URL                                               | Owner (`gh repo view`)             | Default branch |
|----------|---------------------------------------------------|------------------------------------|----------------|
| `origin` | `https://github.com/azizu1012/Nine-router-fort.git` | **azizu1012** (your fork, "A fort of 9router") | `main` |
| `upstream` | `https://github.com/decolua/9router.git`         | **decolua** (goc goc, full history) | `master` |

Check again any time:

```bat
git remote -v
gh repo view azizu1012/Nine-router-fort --json owner,name,defaultBranchRef
gh repo view decolua/9router --json owner,name,defaultBranchRef
git log --oneline -5            :: origin/main
git log --oneline upstream/master -5   :: decolua goc
```

> This note lives next to `start_9router.bat` (`router_api/scripts/`) because that
> script is what boots the server from `D:\AI_Projects\nine-router`.
> The `router_api` repo itself is separate (`azizu1012/Router-API`).

## Current state (2026-10-26)

- `origin/main` = 17 commits, ends at `c717ef8f`
  `fix(responses): clamp max_output_tokens to Meta >=16 floor so probes stop 400ing`.
- `upstream/master` = 1487 commits, at `# v0.5.111` (`3a978a63`).
- **No common merge-base** (`git merge-base origin/main upstream/master` exits 1):
  the fork history was rewritten, so a plain `git merge upstream/master` would try
  to replay the whole upstream history -> massive conflict. **Do NOT plain-merge.**
- Previous upgrades were manual ports, e.g. `9df935c8` (len v0.5.104),
  `2e7f9ed9` (len v0.5.109). Upstream is now at **v0.5.111**.

## Where origin differs from upstream (tree diff, 143 files, +2654/-2709)

Regenerate any time: `git diff --name-status upstream/master origin/main`

By area (biggest first): `tests/unit` 37, `src/app` 17, `open-sse/providers` 16,
`src/sse` 10, `src/lib` (db, oauth, usage) 9, `open-sse/handlers` 7,
`open-sse/translator` 5, `open-sse/executors` 5, `tests/__baseline__` 4,
`src/shared` 4, `src/store` 2, `scripts/` 4 new files.

What is uniquely yours (keep on every upgrade):

- **Deleted, keep deleted**: `.github/workflows/gitbook-pages.yml`,
  `open-sse/providers/registry/runcrate.js`, `open-sse/services/opencodeTlsClient.js`,
  `open-sse/handlers/{embeddingProviders,ttsProviders}/ollamaLocal.js`,
  plus dropped tests (`claude-haiku-adaptive-thinking`, `commandcode-kimi-k3-1-pro`,
  `deepseek-v41-flash-upstream-id`, `ollama-local-*`, `tool-result-image-url`, ...).
- **Added, keep**: `scripts/prune-standalone-runtime-state.mjs`,
  `scripts/verify-key-gate.mjs`, `scripts/verify-limits.mjs`,
  `scripts/win-eperm-fix.js`, `src/lib/db/migrations/002-api-key-limits.js`,
  `src/shared/utils/keyLimiter.js`, `src/store/pageDataStore.js`,
  `open-sse/utils/subAgentFallback.js`,
  `src/app/(dashboard)/DashboardCacheInvalidator.js`,
  `tests/unit/responses-max-output-floor.test.js` (Meta `max_output_tokens >= 16` clamp).
- **Modified, keep your side**: `next.config.mjs` (`/v1/v1` rewrites),
  `src/sse/handlers/*` (chat/fetch/embeddings/image/stt/systemone/tts/video),
  `src/lib/db/*` + `src/lib/usageDb.js`, key/api routes (`scope free providers`,
  key limits), `src/lib/oauth/constants/oauth.js`, `src/lib/tunnel/tailscale/*`,
  dashboard pages (basic-chat, cli-tools, combos, endpoint, mitm, profile,
  providers, proxy-pools, pxpipe, token-saver), `open-sse/executors/*`,
  `open-sse/translator/*`, version/update routes, `tests/__baseline__/*`.

## Safe update procedure (keeps the fork unique)

```bat
cd /d D:\AI_Projects\nine-router
git fetch upstream --prune
git tag | findstr "0.5.11"                    :: see newest upstream tag
git diff --name-status <last-ported-tag> upstream/master   :: what upstream changed since your last port
```

1. **Port, don't merge.** For each upstream change: apply the hunk to your file
   by hand (or `git cherry-pick -n <sha>` then resolve), keeping your custom blocks.
2. **Never touch your unique files**: everything under "Added, keep" above,
   and re-delete anything upstream re-adds that you deleted.
3. **Baselines**: after touching provider registry/alias logic, run
   `tests/__baseline__/verify-*.mjs` and commit refreshed snapshots.
4. **Verify**: syntax-check + targeted vitest
   (`cd tests && npx vitest run unit/responses-max-output-floor.test.js`),
   then boot via `D:\AI_Projects\router_api\scripts\start_9router.bat` and hit
   `/dashboard` + `/v1` once.
5. **Commit** as `chore: upgrade to upstream vX.Y.Z` (like `2e7f9ed9`),
   then `git push origin main`.

If upstream ever needs a true merge: `git merge --allow-unrelated-histories`
is the only way (no shared ancestor) — expect to resolve ~143 files, so prefer
the manual port above.

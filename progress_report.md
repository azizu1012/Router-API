# Progress Report: Router API

## Completed Tasks & Code Changes
- **Infrastructure & Server Scripts:**
    - Modified: `start_router_api.ps1`, `run_router_ssl.ps1`
    - Summary: Refined process cleanup logic and added `-NoConsole` support for automated deployments.
- **Testing & Debugging:**
    - Created: `tests/test_opencode_proxy.py`
    - Modified: `accounts.json`
    - Summary: Updated `accounts.json` with valid production keys; implemented test harness for OpenCode Proxy endpoints.
- **Diagnostics & Error Handling:**
    - Summary: Identified `405 Method Not Allowed` errors during proxy testing.
    - Summary: Encountered `429 RESOURCE_EXHAUSTED` (Quota exceeded) error from `generativelanguage.googleapis.com` during API stress testing via LiteLLM.

## Current Status
Infrastructure scripts are stable. Integration testing for the OpenCode Proxy is active, but currently blocked by `405 Method Not Allowed` routing issues and `429 Rate Limit` errors from the underlying Vertex AI/Google AI Studio provider.

## Next Actions / Todo
- [ ] Investigate and fix 405 Method Not Allowed errors in `/opencode/v1/chat/completions` (verify FastAPI route decorators and request methods in `src/api/opencode_proxy.py`).
- [ ] Implement exponential backoff or request throttling in the proxy layer to handle `429 RESOURCE_EXHAUSTED` errors.
- [ ] Execute `python tests/test_opencode_proxy.py` to validate fixes for Non-stream, Stream, Web Search, and Sub-agent overrides.
- [ ] Finalize integration tests for `activeForm`.
- [ ] Review and refactor form submission handlers.
- [ ] Update documentation for API endpoints related to form processing.
- [ ] Resolve `ModuleNotFoundError` in `tests/check-keys.py` by correcting path imports.
# Plan: Fix Pool Counts and Token Representation in Frontend

## Proposed Changes

### 1. Normalize Model Aliases in Backend Stats Queries
- **File to modify**: `src/core/usage_logger.py`
- **Goal**: Group raw/backing model IDs (e.g. `gemini-flash-35`, `gemini-3.1-flash-lite`, etc.) under their parent virtual pool aliases (`gemini-flash` and `gemini-flash-lite`) in `get_stats` and `get_stats_for_prefix`.
- **Implementation**:
  - Add a helper function `normalize_to_pool_alias(model_alias: str) -> str` that maps:
    - `gemini-flash-35`, `gemini-flash-30`, `gemini-flash-25`, `gemini-3.5-flash`, `gemini-3-flash-preview`, `gemini-2.5-flash`, `gemini-flash`, `gemini-flash-latest`, `flash`, `gemini-flash-pool` → `gemini-flash`
    - `gemini-flash-lite`, `gemini-flash-25-lite`, `gemini-3.1-flash-lite`, `gemini-2.5-flash-lite` → `gemini-flash-lite`
  - Group and aggregate values (`p`, `c`, `t`, `cc`, `cr`, `req`) in the `summary` list returned by the query.
  - Group and aggregate values (`t`, `req`) in the `daily` list returned by the query by date `d` and the normalized model alias.

### 2. Include Custom Pool Endpoints in `/api/model-pools-detail`
- **File to modify**: `src/server/openai_server/routes/dashboard_routes.py`
- **Goal**: Append custom endpoints assigned to the pools so they render in the flow diagram.
- **Implementation**:
  - In `get_model_pools_api` endpoint, retrieve assigned custom models using `router.get_pool_custom_models(pool_name)`.
  - Append these custom models to the members list returned for the pool.

### 3. Expose Custom Pool Model Statistics in Stats Pusher (WebSocket)
- **File to modify**: `src/server/stats_pusher.py`
- **Goal**: Avoid custom models displaying "SYNCING..." in the diagram by feeding their active RPM stats from the handler's tracking dictionary.
- **Implementation**:
  - Import `_custom_pool_usage` and `_CUSTOM_POOL_RPM` from `src.server.openai_server.handler`.
  - Calculate `rpm_remaining` and `active_requests` for each custom model and add to the snapshot payload.

### 4. Display Remaining Tokens/Capacity in Pool Structure Frontend
- **File to modify**: `frontend-src/src/tabs/PoolStructureTab.jsx`
- **Goal**: Show remaining TPM, RPM, and RPD for pools and individual backing models.
- **Implementation**:
  - In the pool header, show live remaining RPM and TPM (`poolLive.rpm_remaining / poolLive.rpm_limit` and `poolLive.tpm_remaining / poolLive.tpm_limit`) instead of static total limits.
  - In the member backing model cards, display RPM load, remaining TPM, and remaining RPD.

---

## Verification Plan
1. **Backend Unit Tests & Logs verification**:
   - Run python to inspect stats returned by `/api/stats` and verify model aliases are grouped to just `gemini-flash`, `gemini-flash-lite`, and custom models.
2. **Frontend check**:
   - Run the frontend build or inspect the JSX structure to ensure fields render correctly.

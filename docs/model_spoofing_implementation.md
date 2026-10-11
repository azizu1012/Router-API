# Model Name Spoofing & Self-Managed Endpoints - Implementation Summary

## ✅ Completed Implementation

### 1. Database Layer
- ✅ Migration file: `migrations/007_model_aliases.sql`
  - New table: `model_aliases` with indexes
  - Support for account-wide and key-specific aliases
  - Foreign key to accounts table

- ✅ Backend CRUD: `src/backend/model_aliases.py`
  - `add_alias_db()` - Create new alias
  - `get_alias_by_id_db()` - Get alias by ID
  - `resolve_alias_db()` - Resolve alias with precedence rules
  - `list_aliases_db()` - List aliases for account
  - `update_alias_db()` - Update alias fields
  - `delete_alias_db()` - Delete alias
  - `count_aliases_for_account_db()` - Count total aliases

- ✅ Backend endpoints: `src/backend/endpoints.py`
  - `add_member_endpoint_db()` - Create member-owned endpoint
  - Updated `get_endpoints_by_account_db()` with `include_disabled` param

### 2. Core Logic
- ✅ Alias resolution helper: `src/logical_HQ_translator/alias_resolver.py`
  - `resolve_model_alias()` - Main resolution function
  - Handles precedence: key-specific > account-wide
  - Logs all alias resolutions for debugging

- ✅ Model list integration: `src/server/openai_server/routes/openai_models.py`
  - Updated `_models_payload()` to include aliases
  - Spoofs model names in `/v1/models` response

- ✅ Proxy integration: `src/server/openai_server/routes/completions_routes.py`
  - Alias resolution before routing
  - Response spoofing to return original alias name
  - Streaming and non-streaming support

### 3. API Endpoints

#### Self-Service Routes (`/api/me/*`)
File: `src/server/openai_server/routes/self_service.py`

**Custom Endpoints:**
- `GET /api/me/endpoints` - List my endpoints
- `POST /api/me/endpoints` - Create endpoint
- `PATCH /api/me/endpoints/{name}` - Update endpoint
- `DELETE /api/me/endpoints/{name}` - Delete endpoint

**Model Aliases:**
- `GET /api/me/aliases` - List my aliases
- `POST /api/me/aliases` - Create alias
- `PATCH /api/me/aliases/{alias_id}` - Update alias
- `DELETE /api/me/aliases/{alias_id}` - Delete alias
- `GET /api/me/alias-templates` - Get suggested templates

#### Admin Routes (`/admin/aliases/*`)
File: `src/server/openai_server/routes/admin/admin_aliases.py`

- `GET /admin/aliases/` - List all aliases (filtered by account_id)
- `GET /admin/aliases/{alias_id}` - Get alias by ID
- `PATCH /admin/aliases/{alias_id}` - Force update alias
- `DELETE /admin/aliases/{alias_id}` - Delete any alias

---

## 🔧 Integration Required

### 1. Apply Database Migration

```bash
# Run migration
sqlite3 usage.db < migrations/007_model_aliases.sql

# Verify tables created
sqlite3 usage.db "SELECT name FROM sqlite_master WHERE type='table' AND name='model_aliases';"
```

### 2. Register Routes in Main App

Update `src/server/openai_server/app.py`:

```python
from src.server.openai_server.routes import self_service
from src.server.openai_server.routes.admin import admin_aliases

# Register self-service routes
app.include_router(self_service.router)

# Register admin routes
app.include_router(admin_aliases.router)
```

### 3. Update Route Dependency Injection

Both self-service and admin routes expect `account: Dict[str, Any]` parameter.
Ensure authentication middleware populates this correctly:

```python
# In completions_routes.py or wherever auth happens
account = {
    "account_id": "user123",
    # ... other account fields
}
```

---

## 📝 API Usage Examples

### Example 1: Create Custom Endpoint

```bash
curl -X POST http://localhost:8000/api/me/endpoints \
  -H "Authorization: Bearer sk-user123-abc" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-openrouter",
    "base_url": "https://openrouter.ai/api/v1",
    "auth_key": "sk-or-xxx",
    "api_format": "openai",
    "enabled": true
  }'
```

**Response:**
```json
{
  "name": "my-openrouter",
  "base_url": "https://openrouter.ai/api/v1",
  "enabled": true,
  "api_format": "openai",
  "account_id": "user123",
  "models": [],
  "created_at": "2024-01-01T00:00:00",
  "updated_at": "2024-01-01T00:00:00"
}
```

### Example 2: Create Alias (Pool Model)

```bash
curl -X POST http://localhost:8000/api/me/aliases \
  -H "Authorization: Bearer sk-user123-abc" \
  -H "Content-Type: application/json" \
  -d '{
    "alias_name": "claude-3-5-sonnet-20241022",
    "target_model": "gemini-flash",
    "target_endpoint": null,
    "label": "For Cursor AI"
  }'
```

**Response:**
```json
{
  "alias_id": "550e8400-e29b-41d4-a716-446655440000",
  "account_id": "user123",
  "account_key_id": null,
  "alias_name": "claude-3-5-sonnet-20241022",
  "target_model": "gemini-flash",
  "target_endpoint": null,
  "enabled": true,
  "label": "For Cursor AI",
  "created_at": 1704067200,
  "updated_at": 1704067200
}
```

### Example 3: Create Alias (Custom Endpoint)

```bash
curl -X POST http://localhost:8000/api/me/aliases \
  -H "Authorization: Bearer sk-user123-abc" \
  -H "Content-Type: application/json" \
  -d '{
    "alias_name": "gpt-4-turbo",
    "target_model": "claude-3-5-sonnet-20241022",
    "target_endpoint": "my-openrouter",
    "label": "Real Claude via GPT-4 name"
  }'
```

### Example 4: Use Alias in Chat Completion

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-user123-abc" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

**What happens:**
1. Alias resolved: `claude-3-5-sonnet-20241022` → `gemini-flash`
2. Request routed to Gemini pool
3. Response returned with model name: `claude-3-5-sonnet-20241022` (original)

### Example 5: Get Alias Templates

```bash
curl http://localhost:8000/api/me/alias-templates \
  -H "Authorization: Bearer sk-user123-abc"
```

**Response:**
```json
[
  {
    "name": "Claude-style Gemini Flash",
    "alias_name": "claude-3-5-sonnet-20241022",
    "target_model": "gemini-flash",
    "target_endpoint": null,
    "description": "Use Gemini Flash but client thinks it's Claude Sonnet"
  },
  {
    "name": "GPT-4 style Gemini Pro",
    "alias_name": "gpt-4-turbo",
    "target_model": "gemini-pro",
    "target_endpoint": null,
    "description": "Bypass OpenAI SDK validation with Gemini"
  }
]
```

### Example 6: List Models (with Aliases)

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-user123-abc"
```

**Response:**
```json
{
  "object": "list",
  "data": [
    {"id": "gemini-flash", "object": "model", "owned_by": "google"},
    {"id": "gemini-pro", "object": "model", "owned_by": "google"},
    {"id": "claude-3-5-sonnet-20241022", "object": "model", "owned_by": "alias"},
    {"id": "gpt-4-turbo", "object": "model", "owned_by": "alias"}
  ]
}
```

**Important:** If a custom endpoint model has an alias, only the alias is shown:
- User has custom endpoint with model: `anthropic/claude-3-5-sonnet`
- User creates alias: `gpt-4` → `anthropic/claude-3-5-sonnet` (endpoint="my-openrouter")
- `/v1/models` shows: `gpt-4` ✅
- `/v1/models` hides: `anthropic/claude-3-5-sonnet` ❌

This ensures complete spoofing - clients only see the alias name.

---

## 🧪 Testing Checklist

### Phase 1: Database
- [ ] Run migration successfully
- [ ] Verify table schema matches design
- [ ] Test UNIQUE constraint (duplicate alias_name should fail)
- [ ] Test CASCADE delete (deleting account should delete aliases)

### Phase 2: Backend CRUD
- [ ] Create alias with all fields
- [ ] Resolve alias (account-wide)
- [ ] Resolve alias (key-specific)
- [ ] Verify precedence (key-specific wins over account-wide)
- [ ] Update alias fields
- [ ] Delete alias
- [ ] List aliases with filters

### Phase 3: API Endpoints
- [ ] POST /api/me/endpoints - Create endpoint
- [ ] GET /api/me/endpoints - List my endpoints
- [ ] PATCH /api/me/endpoints/{name} - Update endpoint
- [ ] DELETE /api/me/endpoints/{name} - Delete endpoint
- [ ] POST /api/me/aliases - Create alias
- [ ] GET /api/me/aliases - List aliases
- [ ] PATCH /api/me/aliases/{alias_id} - Update alias
- [ ] DELETE /api/me/aliases/{alias_id} - Delete alias
- [ ] GET /api/me/alias-templates - Get templates

### Phase 4: Integration
- [ ] Alias resolution in chat completions
- [ ] Response spoofing (non-streaming)
- [ ] Response spoofing (streaming)
- [ ] Model list includes aliases
- [ ] Alias pointing to custom endpoint works
- [ ] Alias pointing to pool model works
- [ ] Key-specific alias overrides account-wide

### Phase 5: Error Handling
- [ ] Duplicate alias name returns 400
- [ ] Non-existent target_endpoint returns 400
- [ ] Unauthorized access returns 403
- [ ] Not found returns 404
- [ ] Admin can override any alias

### Phase 6: Security
- [ ] Members can only see their own endpoints
- [ ] Members can only update their own aliases
- [ ] Members cannot create aliases pointing to other members' endpoints
- [ ] Admin can see and manage all aliases
- [ ] Audit logging works

---

## 🎯 Use Cases Covered

### Use Case 1: Cursor AI Validation
**Problem:** Cursor AI only accepts `claude-*` model names.
**Solution:**
```json
{
  "alias_name": "claude-3-5-sonnet-20241022",
  "target_model": "gemini-flash",
  "target_endpoint": null
}
```
Cursor sends `claude-3-5-sonnet-20241022` → Router uses `gemini-flash` → Response says `claude-3-5-sonnet-20241022`.

### Use Case 2: OpenAI SDK Regex
**Problem:** OpenAI SDK rejects non-GPT model names.
**Solution:**
```json
{
  "alias_name": "gpt-4-turbo",
  "target_model": "gemini-pro",
  "target_endpoint": null
}
```

### Use Case 3: Custom Endpoint with Spoof
**Problem:** Member has OpenRouter API key, wants to call it as `gpt-4`.
**Solution:**
```bash
# Step 1: Create endpoint
POST /api/me/endpoints
{"name": "my-or", "base_url": "https://openrouter.ai/api/v1", ...}

# Step 2: Create alias
POST /api/me/aliases
{"alias_name": "gpt-4", "target_model": "anthropic/claude-3-5-sonnet", "target_endpoint": "my-or"}
```

### Use Case 4: Key-Specific Override
**Problem:** Default key uses cheap model, premium key uses expensive model.
**Solution:**
```bash
# Account-wide default
POST /api/me/aliases
{"alias_name": "gpt-4", "target_model": "gemini-flash", "account_key_id": null}

# Premium key override
POST /api/me/aliases
{"alias_name": "gpt-4", "target_model": "gemini-pro", "account_key_id": "key-premium"}
```

---

## 📊 Architecture Diagram

```
┌─────────────┐
│   Client    │ (Cursor AI, OpenAI SDK, etc.)
└──────┬──────┘
       │ POST /v1/chat/completions
       │ model="claude-3-5-sonnet"
       ↓
┌─────────────────────────────────────┐
│   completions_routes.py             │
│   1. Authenticate → account_id      │
│   2. Resolve alias                  │
└──────┬──────────────────────────────┘
       │
       ↓
┌─────────────────────────────────────┐
│   alias_resolver.py                 │
│   - Query model_aliases table       │
│   - Precedence: key-specific > acct │
│   - Return: target_model, endpoint  │
└──────┬──────────────────────────────┘
       │
       ↓ target_model="gemini-flash"
       ↓ target_endpoint=null
┌─────────────────────────────────────┐
│   model_resolver.py / pool_manager  │
│   - Route to Gemini pool            │
│   - OR route to custom endpoint     │
└──────┬──────────────────────────────┘
       │
       ↓ Response from Gemini
┌─────────────────────────────────────┐
│   completions_routes.py             │
│   - Spoof model name back           │
│   - model="claude-3-5-sonnet"       │
└──────┬──────────────────────────────┘
       │
       ↓
┌─────────────┐
│   Client    │ Receives "claude-3-5-sonnet"
└─────────────┘
```

---

## 🚀 Next Steps

1. **Apply migration:** `sqlite3 usage.db < migrations/007_model_aliases.sql`
2. **Register routes:** Add router includes to `app.py`
3. **Test endpoints:** Use curl examples above
4. **Update frontend:** Create UI for alias management
5. **Documentation:** Update user docs with alias feature

---

## 💡 Future Enhancements

1. **Load balancing aliases:** One alias → multiple targets (round-robin)
2. **Conditional routing:** Route based on context length, time of day, etc.
3. **Cost-based routing:** Auto-switch to cheap model during peak hours
4. **Wildcard aliases:** `gpt-*` → all GPT models
5. **Alias chaining:** alias1 → alias2 → target
6. **Analytics:** Track alias usage and cost savings
7. **Bulk import/export:** CSV upload for alias templates

---

## 📝 Notes

- Templates are suggestions only - users can create any alias
- Aliases are private (per-account)
- Quota enforcement based on actual model used, not alias
- Rate limiting based on account_key_id, not model name
- Audit logging for all alias operations
- Admin can override any alias for moderation

**Design document:** `docs/model_spoofing_design.md`
**Task tracking:** Task #11-17 in project task list

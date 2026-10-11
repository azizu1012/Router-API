# Model Spoofing - Implementation Checklist

## ✅ COMPLETED (Backend 100%)

### Database & Schema
- [x] Created `migrations/007_model_aliases.sql`
- [x] Table: `model_aliases` with proper indexes
- [x] Support account-wide and key-specific aliases
- [x] Support pool routing and custom endpoint routing

### Backend Logic
- [x] `src/backend/model_aliases.py` - Full CRUD operations
- [x] `resolve_alias()` function with precedence rules
- [x] `src/backend/endpoints.py` - Member-owned endpoint support
- [x] Integration with existing pool system

### API Routes
- [x] Self-service: `/api/me/endpoints` (CRUD)
- [x] Self-service: `/api/me/aliases` (CRUD)
- [x] Admin: `/admin/aliases` (view/manage all)
- [x] Routes registered in `__init__.py`

### Proxy Integration
- [x] `model_resolver.py` - Resolve alias before routing
- [x] `proxy.py` - Route based on alias target
- [x] `proxy_stream.py` - Route based on alias target
- [x] Response spoofing - Replace model name in response

### Model List Spoofing
- [x] `standard_routes.py` - `/v1/models` endpoint
- [x] Show aliases as virtual models
- [x] Hide custom endpoint models if aliased
- [x] Show custom endpoint models if NOT aliased

### Documentation
- [x] `docs/model_spoofing_design.md` - Architecture
- [x] `docs/model_spoofing_quickstart.md` - Quick guide
- [x] `docs/model_spoofing_implementation.md` - API examples
- [x] `docs/model_spoofing_tests.md` - Test cases
- [x] `IMPLEMENTATION_SUMMARY.md` - Full summary
- [x] `MODEL_SPOOFING_README.md` - Quick overview

---

## ⏳ TODO (Manual Steps)

### Task #19: Apply Database Migration
```bash
# Navigate to project
cd D:\AI_Projects\router_api

# Apply migration
sqlite3 usage.db < migrations/007_model_aliases.sql

# Verify table created
sqlite3 usage.db "SELECT name FROM sqlite_master WHERE type='table' AND name='model_aliases';"
# Expected output: model_aliases

# Verify index created
sqlite3 usage.db "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_model_aliases_lookup';"
# Expected output: idx_model_aliases_lookup
```

**Status:** ⏳ Pending

---

### Task #20: Test API Endpoints

#### 20.1 Test Member Endpoints API

```bash
# 1. Create custom endpoint
curl -X POST http://localhost:8000/api/me/endpoints \
  -H "Authorization: Bearer sk-admin-test" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-openrouter",
    "base_url": "https://openrouter.ai/api/v1",
    "auth_key": "sk-or-xxx",
    "api_format": "openai"
  }'

# Expected: 200 OK, returns endpoint_id

# 2. List my endpoints
curl http://localhost:8000/api/me/endpoints \
  -H "Authorization: Bearer sk-admin-test"

# Expected: Array with "test-openrouter"

# 3. Update endpoint
curl -X PATCH http://localhost:8000/api/me/endpoints/test-openrouter \
  -H "Authorization: Bearer sk-admin-test" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Expected: 200 OK

# 4. Delete endpoint
curl -X DELETE http://localhost:8000/api/me/endpoints/test-openrouter \
  -H "Authorization: Bearer sk-admin-test"

# Expected: 200 OK
```

#### 20.2 Test Alias API

```bash
# 1. Create pool alias (Gemini Flash as Claude)
curl -X POST http://localhost:8000/api/me/aliases \
  -H "Authorization: Bearer sk-admin-test" \
  -H "Content-Type: application/json" \
  -d '{
    "alias_name": "claude-3-5-sonnet-20241022",
    "target_model": "gemini-flash",
    "target_endpoint": null,
    "label": "Gemini Flash for Cursor AI"
  }'

# Expected: 200 OK, returns alias_id

# 2. List my aliases
curl http://localhost:8000/api/me/aliases \
  -H "Authorization: Bearer sk-admin-test"

# Expected: Array with "claude-3-5-sonnet-20241022"

# 3. Test model list (should show alias)
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-admin-test"

# Expected: "claude-3-5-sonnet-20241022" in list

# 4. Test chat with alias
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-admin-test" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "messages": [{"role": "user", "content": "Say hello"}]
  }'

# Expected: Response with model="claude-3-5-sonnet-20241022" (spoofed)

# 5. Delete alias
curl -X DELETE http://localhost:8000/api/me/aliases/{alias_id} \
  -H "Authorization: Bearer sk-admin-test"

# Expected: 200 OK
```

#### 20.3 Test Custom Endpoint + Alias

```bash
# 1. Create custom endpoint
curl -X POST http://localhost:8000/api/me/endpoints \
  -H "Authorization: Bearer sk-admin-test" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-anthropic",
    "base_url": "https://api.anthropic.com/v1",
    "auth_key": "sk-ant-xxx",
    "api_format": "anthropic"
  }'

# 2. Create alias pointing to custom endpoint
curl -X POST http://localhost:8000/api/me/aliases \
  -H "Authorization: Bearer sk-admin-test" \
  -H "Content-Type: application/json" \
  -d '{
    "alias_name": "gpt-4-turbo",
    "target_model": "claude-3-5-sonnet-20241022",
    "target_endpoint": "my-anthropic",
    "label": "Real Claude via GPT-4 name"
  }'

# 3. Test model list
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-admin-test"

# Expected: 
# - "gpt-4-turbo" present
# - "claude-3-5-sonnet-20241022" HIDDEN (because aliased)

# 4. Test chat
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-admin-test" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4-turbo",
    "messages": [{"role": "user", "content": "What model are you?"}]
  }'

# Expected:
# - Routes to my-anthropic endpoint
# - Response model="gpt-4-turbo" (spoofed)
```

#### 20.4 Test Key-Specific Override

```bash
# 1. Create account-wide alias
curl -X POST http://localhost:8000/api/me/aliases \
  -H "Authorization: Bearer sk-admin-test" \
  -H "Content-Type: application/json" \
  -d '{
    "alias_name": "gpt-4",
    "target_model": "gemini-pro",
    "account_key_id": null,
    "label": "Default GPT-4"
  }'

# 2. Create key-specific override (assuming you have key "premium")
curl -X POST http://localhost:8000/api/me/aliases \
  -H "Authorization: Bearer sk-admin-premium" \
  -H "Content-Type: application/json" \
  -d '{
    "alias_name": "gpt-4",
    "target_model": "gemini-flash",
    "label": "Premium key uses Flash"
  }'

# 3. Test with regular key
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-admin-test" \
  -d '{"model":"gpt-4","messages":[{"role":"user","content":"Hi"}]}'

# Expected: Routes to gemini-pro

# 4. Test with premium key
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-admin-premium" \
  -d '{"model":"gpt-4","messages":[{"role":"user","content":"Hi"}]}'

# Expected: Routes to gemini-flash
```

#### 20.5 Test Admin API

```bash
# 1. List all aliases
curl http://localhost:8000/admin/aliases \
  -H "Authorization: Bearer sk-admin-test"

# Expected: All aliases across all accounts

# 2. Get single alias
curl http://localhost:8000/admin/aliases/{alias_id} \
  -H "Authorization: Bearer sk-admin-test"

# Expected: Full alias details

# 3. Update any alias
curl -X PATCH http://localhost:8000/admin/aliases/{alias_id} \
  -H "Authorization: Bearer sk-admin-test" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Expected: 200 OK

# 4. Delete any alias
curl -X DELETE http://localhost:8000/admin/aliases/{alias_id} \
  -H "Authorization: Bearer sk-admin-test"

# Expected: 200 OK
```

**Status:** ⏳ Pending

---

### Task #21: Build Frontend UI

#### 21.1 Model Aliases Tab

**Location:** `frontend/src/components/ModelAliases.vue` (or React equivalent)

**Features:**
- [ ] List user's aliases in table
  - Columns: Alias Name, Target Model, Target Endpoint, Enabled, Actions
- [ ] Create alias form
  - Fields: alias_name, target_model, target_endpoint, label
  - Template picker dropdown with examples
  - User can customize template or enter manually
- [ ] Edit alias (inline or modal)
- [ ] Delete alias with confirmation
- [ ] Enable/disable toggle

**Template Examples (not exhaustive):**
```javascript
const EXAMPLE_TEMPLATES = [
  {
    name: "Claude Sonnet → Gemini Flash",
    alias_name: "claude-3-5-sonnet-20241022",
    target_model: "gemini-flash",
    target_endpoint: null
  },
  {
    name: "Claude Haiku → Gemini Lite",
    alias_name: "claude-3-5-haiku-20241022",
    target_model: "gemini-lite",
    target_endpoint: null
  },
  {
    name: "GPT-4 → Gemini Pro",
    alias_name: "gpt-4-turbo",
    target_model: "gemini-pro",
    target_endpoint: null
  },
  {
    name: "GPT-4 → Custom Endpoint",
    alias_name: "gpt-4-turbo",
    target_model: "anthropic/claude-3-5-sonnet",
    target_endpoint: "my-endpoint"
  }
];
```

**Note:** These are EXAMPLES only. User should be able to:
- Use template as-is
- Customize template before saving
- Ignore templates and enter manually

#### 21.2 Custom Endpoints Tab

**Location:** `frontend/src/components/CustomEndpoints.vue`

**Features:**
- [ ] List user's custom endpoints
  - Columns: Name, Base URL, API Format, Enabled, Actions
- [ ] Create endpoint form
  - Fields: name, base_url, auth_key, api_format
  - Validation: URL format, required fields
- [ ] Edit endpoint
- [ ] Delete endpoint with confirmation
  - Warning if aliases point to this endpoint
- [ ] Show which aliases use this endpoint

#### 21.3 Admin View

**Location:** `frontend/src/components/admin/Aliases.vue`

**Features:**
- [ ] List all aliases across all accounts
- [ ] Filter by account_id
- [ ] Show account_id and account_key_id
- [ ] Force enable/disable any alias
- [ ] Delete any alias
- [ ] Search/filter by alias_name or target_model

**Status:** ⏳ Pending

---

## 📝 Testing Checklist

Run through these scenarios after implementation:

### Scenario 1: Cursor AI + Gemini
- [ ] Create alias: `claude-3-5-sonnet-20241022` → `gemini-flash`
- [ ] Verify `/v1/models` shows alias
- [ ] Send chat request with Cursor AI
- [ ] Verify response model name is `claude-3-5-sonnet-20241022`
- [ ] Verify logs show gemini-flash was actually used

### Scenario 2: Custom Endpoint + Spoof
- [ ] Create custom endpoint (OpenRouter)
- [ ] Create alias: `gpt-4` → `anthropic/claude-3-5-sonnet` @ openrouter
- [ ] Verify `/v1/models` shows `gpt-4`, hides `anthropic/claude-3-5-sonnet`
- [ ] Send request with `gpt-4`
- [ ] Verify routes to custom endpoint
- [ ] Verify response model is `gpt-4`

### Scenario 3: Key-Specific Override
- [ ] Create account-wide: `gpt-4` → `gemini-pro`
- [ ] Create key-specific: `gpt-4` → `gemini-flash` (key=premium)
- [ ] Test with regular key → should use gemini-pro
- [ ] Test with premium key → should use gemini-flash

### Scenario 4: Disable Alias
- [ ] Create alias
- [ ] Disable alias
- [ ] Verify `/v1/models` now shows original model name
- [ ] Verify request uses original model, not alias

### Scenario 5: Delete Endpoint with Alias
- [ ] Create endpoint + alias pointing to it
- [ ] Try to delete endpoint
- [ ] Should warn or cascade delete alias
- [ ] Verify cleanup

---

## 🎯 Success Criteria

- [x] Backend code complete and integrated
- [x] All routes registered
- [x] Documentation complete
- [ ] Migration applied successfully
- [ ] All API tests pass
- [ ] Frontend UI functional
- [ ] End-to-end scenarios work
- [ ] No breaking changes to existing features

---

## 📚 Reference Documents

1. **MODEL_SPOOFING_README.md** - Quick overview (START HERE)
2. **IMPLEMENTATION_SUMMARY.md** - Full technical summary
3. **docs/model_spoofing_design.md** - Architecture design
4. **docs/model_spoofing_quickstart.md** - Quick start guide
5. **docs/model_spoofing_implementation.md** - API examples
6. **docs/model_spoofing_tests.md** - Test cases

---

## 🚀 Next Action

**Start with Task #19:**
```bash
cd D:\AI_Projects\router_api
sqlite3 usage.db < migrations/007_model_aliases.sql
```

Then move to Task #20 (API testing).

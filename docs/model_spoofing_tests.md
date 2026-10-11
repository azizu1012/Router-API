# Model Spoofing - Test Cases

## Test Suite: Model List Spoofing

### Test 1: Basic Alias Spoofing
**Setup:**
```sql
INSERT INTO model_aliases VALUES (
  'alias-001',
  'user123',
  NULL,
  'claude-3-5-sonnet',
  'gemini-flash',
  NULL,
  1,
  'Test alias',
  1234567890,
  1234567890
);
```

**Request:**
```bash
curl http://localhost:8000/v1/models -H "Authorization: Bearer sk-user123-abc"
```

**Expected Response:**
```json
{
  "data": [
    {"id": "gemini-flash", "owned_by": "google"},
    {"id": "gemini-pro", "owned_by": "google"},
    {"id": "claude-3-5-sonnet", "owned_by": "alias", "_alias_target": "gemini-flash"}
  ]
}
```

**Verification:**
- ✅ Pool models shown
- ✅ Alias shown as separate model
- ✅ `_alias_target` metadata included

---

### Test 2: Custom Endpoint WITHOUT Alias
**Setup:**
```sql
INSERT INTO custom_endpoints VALUES (
  'my-openrouter',
  'https://openrouter.ai/api/v1',
  'sk-or-xxx',
  1,
  '["anthropic/claude-3-5-sonnet", "anthropic/claude-3-opus"]',
  '["anthropic/claude-3-5-sonnet"]',
  '{}',
  'user123',
  0,
  'openai',
  '{}',
  '2024-01-01'
);
```

**Request:**
```bash
curl http://localhost:8000/v1/models -H "Authorization: Bearer sk-user123-abc"
```

**Expected Response:**
```json
{
  "data": [
    {"id": "gemini-flash", "owned_by": "google"},
    {"id": "anthropic/claude-3-5-sonnet", "owned_by": "custom_endpoint:my-openrouter"},
    {"id": "anthropic/claude-3-opus", "owned_by": "custom_endpoint:my-openrouter"}
  ]
}
```

**Verification:**
- ✅ Custom endpoint models shown with real names
- ✅ `owned_by` indicates custom endpoint

---

### Test 3: Custom Endpoint WITH Alias (Full Spoofing)
**Setup:**
```sql
-- Custom endpoint
INSERT INTO custom_endpoints VALUES (
  'my-openrouter',
  'https://openrouter.ai/api/v1',
  'sk-or-xxx',
  1,
  '["anthropic/claude-3-5-sonnet"]',
  '["anthropic/claude-3-5-sonnet"]',
  '{}',
  'user123',
  0,
  'openai',
  '{}',
  '2024-01-01'
);

-- Alias pointing to custom endpoint model
INSERT INTO model_aliases VALUES (
  'alias-002',
  'user123',
  NULL,
  'gpt-4-turbo',
  'anthropic/claude-3-5-sonnet',
  'my-openrouter',
  1,
  'Spoof as GPT-4',
  1234567890,
  1234567890
);
```

**Request:**
```bash
curl http://localhost:8000/v1/models -H "Authorization: Bearer sk-user123-abc"
```

**Expected Response:**
```json
{
  "data": [
    {"id": "gemini-flash", "owned_by": "google"},
    {"id": "gpt-4-turbo", "owned_by": "alias", "_alias_target": "anthropic/claude-3-5-sonnet", "_alias_endpoint": "my-openrouter"}
  ]
}
```

**Verification:**
- ✅ Alias `gpt-4-turbo` shown
- ❌ Original model `anthropic/claude-3-5-sonnet` NOT shown (hidden by alias)
- ✅ Client only sees `gpt-4-turbo`, full spoofing achieved

---

### Test 4: Partial Spoofing (Some Models Aliased, Some Not)
**Setup:**
```sql
-- Custom endpoint with 2 models
INSERT INTO custom_endpoints VALUES (
  'my-openrouter',
  'https://openrouter.ai/api/v1',
  'sk-or-xxx',
  1,
  '["anthropic/claude-3-5-sonnet", "anthropic/claude-3-opus"]',
  '["anthropic/claude-3-5-sonnet", "anthropic/claude-3-opus"]',
  '{}',
  'user123',
  0,
  'openai',
  '{}',
  '2024-01-01'
);

-- Only alias ONE of them
INSERT INTO model_aliases VALUES (
  'alias-003',
  'user123',
  NULL,
  'gpt-4-turbo',
  'anthropic/claude-3-5-sonnet',
  'my-openrouter',
  1,
  'Spoof Sonnet only',
  1234567890,
  1234567890
);
```

**Request:**
```bash
curl http://localhost:8000/v1/models -H "Authorization: Bearer sk-user123-abc"
```

**Expected Response:**
```json
{
  "data": [
    {"id": "gemini-flash", "owned_by": "google"},
    {"id": "gpt-4-turbo", "owned_by": "alias", "_alias_target": "anthropic/claude-3-5-sonnet"},
    {"id": "anthropic/claude-3-opus", "owned_by": "custom_endpoint:my-openrouter"}
  ]
}
```

**Verification:**
- ✅ Aliased model: `gpt-4-turbo` shown, `anthropic/claude-3-5-sonnet` hidden
- ✅ Non-aliased model: `anthropic/claude-3-opus` shown with real name

---

### Test 5: Multiple Aliases for Different Endpoints
**Setup:**
```sql
-- Endpoint 1
INSERT INTO custom_endpoints VALUES (
  'openrouter',
  'https://openrouter.ai/api/v1',
  'sk-or-xxx',
  1,
  '["anthropic/claude-3-5-sonnet"]',
  '["anthropic/claude-3-5-sonnet"]',
  '{}',
  'user123',
  0,
  'openai',
  '{}',
  '2024-01-01'
);

-- Endpoint 2
INSERT INTO custom_endpoints VALUES (
  'anthropic-direct',
  'https://api.anthropic.com/v1',
  'sk-ant-xxx',
  1,
  '["claude-3-5-sonnet-20241022"]',
  '["claude-3-5-sonnet-20241022"]',
  '',
  'user123',
  0,
  'anthropic',
  '{}',
  '2024-01-01'
);

-- Alias for endpoint 1
INSERT INTO model_aliases VALUES (
  'alias-004',
  'user123',
  NULL,
  'gpt-4-turbo',
  'anthropic/claude-3-5-sonnet',
  'openrouter',
  1,
  'Via OpenRouter',
  1234567890,
  1234567890
);

-- Alias for endpoint 2
INSERT INTO model_aliases VALUES (
  'alias-005',
  'user123',
  NULL,
  'gpt-4o',
  'claude-3-5-sonnet-20241022',
  'anthropic-direct',
  1,
  'Via Anthropic direct',
  1234567890,
  1234567890
);
```

**Request:**
```bash
curl http://localhost:8000/v1/models -H "Authorization: Bearer sk-user123-abc"
```

**Expected Response:**
```json
{
  "data": [
    {"id": "gemini-flash", "owned_by": "google"},
    {"id": "gpt-4-turbo", "owned_by": "alias", "_alias_endpoint": "openrouter"},
    {"id": "gpt-4o", "owned_by": "alias", "_alias_endpoint": "anthropic-direct"}
  ]
}
```

**Verification:**
- ✅ Both custom endpoint models hidden
- ✅ Only aliases shown
- ✅ Different endpoints properly distinguished by metadata

---

### Test 6: Disabled Alias Should Not Hide Model
**Setup:**
```sql
-- Custom endpoint
INSERT INTO custom_endpoints VALUES (
  'my-openrouter',
  'https://openrouter.ai/api/v1',
  'sk-or-xxx',
  1,
  '["anthropic/claude-3-5-sonnet"]',
  '["anthropic/claude-3-5-sonnet"]',
  '{}',
  'user123',
  0,
  'openai',
  '{}',
  '2024-01-01'
);

-- Disabled alias
INSERT INTO model_aliases VALUES (
  'alias-006',
  'user123',
  NULL,
  'gpt-4-turbo',
  'anthropic/claude-3-5-sonnet',
  'my-openrouter',
  0,  -- enabled=0
  'Disabled',
  1234567890,
  1234567890
);
```

**Request:**
```bash
curl http://localhost:8000/v1/models -H "Authorization: Bearer sk-user123-abc"
```

**Expected Response:**
```json
{
  "data": [
    {"id": "gemini-flash", "owned_by": "google"},
    {"id": "anthropic/claude-3-5-sonnet", "owned_by": "custom_endpoint:my-openrouter"}
  ]
}
```

**Verification:**
- ✅ Disabled alias NOT shown
- ✅ Original model shown (because alias is disabled)
- ✅ No spoofing when alias is disabled

---

## Test Suite: Request Routing with Aliases

### Test 7: Route Aliased Request to Custom Endpoint
**Setup:**
```sql
-- Same as Test 3
```

**Request:**
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-user123-abc" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4-turbo",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

**Expected Behavior:**
1. Alias resolver: `gpt-4-turbo` → `anthropic/claude-3-5-sonnet` (endpoint=`my-openrouter`)
2. Route to custom endpoint `my-openrouter`
3. Call OpenRouter with model `anthropic/claude-3-5-sonnet`
4. Response spoofing: Replace model name back to `gpt-4-turbo`

**Expected Response:**
```json
{
  "id": "chatcmpl-xxx",
  "model": "gpt-4-turbo",
  "choices": [...]
}
```

**Verification:**
- ✅ Request routed to correct custom endpoint
- ✅ Real model name used in upstream call
- ✅ Alias name returned in response

---

## Test Suite: Isolation Between Users

### Test 8: User A Cannot See User B's Aliases
**Setup:**
```sql
-- User A's alias
INSERT INTO model_aliases VALUES (
  'alias-007',
  'userA',
  NULL,
  'gpt-4-turbo',
  'gemini-flash',
  NULL,
  1,
  'User A alias',
  1234567890,
  1234567890
);

-- User B's alias
INSERT INTO model_aliases VALUES (
  'alias-008',
  'userB',
  NULL,
  'gpt-4-turbo',
  'gemini-pro',
  NULL,
  1,
  'User B alias',
  1234567890,
  1234567890
);
```

**Request (User A):**
```bash
curl http://localhost:8000/v1/models -H "Authorization: Bearer sk-userA-abc"
```

**Expected Response:**
```json
{
  "data": [
    {"id": "gemini-flash", "owned_by": "google"},
    {"id": "gemini-pro", "owned_by": "google"},
    {"id": "gpt-4-turbo", "owned_by": "alias", "_alias_target": "gemini-flash"}
  ]
}
```

**Request (User B):**
```bash
curl http://localhost:8000/v1/models -H "Authorization: Bearer sk-userB-abc"
```

**Expected Response:**
```json
{
  "data": [
    {"id": "gemini-flash", "owned_by": "google"},
    {"id": "gemini-pro", "owned_by": "google"},
    {"id": "gpt-4-turbo", "owned_by": "alias", "_alias_target": "gemini-pro"}
  ]
}
```

**Verification:**
- ✅ User A sees alias pointing to `gemini-flash`
- ✅ User B sees alias pointing to `gemini-pro`
- ✅ Same alias name, different targets per user

---

## Summary

**Key behaviors implemented:**
1. ✅ Aliases shown as separate models in `/v1/models`
2. ✅ Custom endpoint models hidden when aliased
3. ✅ Disabled aliases don't hide original models
4. ✅ Partial aliasing supported (some models aliased, some not)
5. ✅ User isolation (aliases are per-account)
6. ✅ Request routing respects aliases
7. ✅ Response spoofing returns alias name, not real model name

**Edge cases covered:**
- Multiple endpoints with aliases
- Same model name in different endpoints
- Disabled endpoints
- Disabled aliases
- Non-existent target models (should fail gracefully)

# Model Name Spoofing - Design Document

## Problem Statement

### Use Case 1: Client-side Regex Validation
Nhiều OpenAI SDK clients có regex validation bắt buộc model name phải match pattern cụ thể:
- Anthropic SDK: `^claude-.*`
- OpenAI SDK: `^(gpt|o1|chatgpt)-.*`
- Some custom clients: hardcoded allowlist

**Problem:** User muốn dùng `gemini-flash` nhưng client reject vì không match regex.

**Solution:** Cho phép user alias `gemini-flash` thành `claude-3-5-sonnet` để bypass validation.

### Use Case 2: Self-Managed Endpoints
Members muốn tự quản lý custom endpoints của riêng họ:
- Add custom endpoint pointing to OpenRouter/Claude/Gemini
- Spoof model name để bypass client validation
- Không cần admin approve

## Architecture Design

### 1. Model Alias System

#### Database Schema Changes

**New table: `model_aliases`**
```sql
CREATE TABLE model_aliases (
    alias_id TEXT PRIMARY KEY,              -- UUID
    account_id TEXT NOT NULL,               -- Owner của alias này
    account_key_id TEXT DEFAULT NULL,       -- NULL = account-wide, non-NULL = key-specific
    
    -- Alias definition
    alias_name TEXT NOT NULL,               -- Name user sẽ gọi (e.g., "claude-3-5-sonnet")
    target_model TEXT NOT NULL,             -- Model thực tế (e.g., "gemini-flash")
    target_endpoint TEXT DEFAULT NULL,      -- NULL = dùng pool, non-NULL = custom endpoint name
    
    -- Metadata
    enabled INTEGER DEFAULT 1,
    label TEXT DEFAULT '',                  -- User description
    created_at INTEGER,
    updated_at INTEGER,
    
    UNIQUE(account_id, account_key_id, alias_name)  -- Một alias name chỉ trỏ đến 1 target
);

CREATE INDEX idx_model_aliases_lookup ON model_aliases(account_id, account_key_id, alias_name, enabled);
```

**Update `custom_endpoints` table:**
```sql
ALTER TABLE custom_endpoints ADD COLUMN account_key_id TEXT DEFAULT '';
-- Note: This column already exists from previous refactor
```

### 2. Alias Resolution Flow

```
Request: POST /v1/chat/completions
Body: {"model": "claude-3-5-sonnet", ...}

↓ Step 1: Authenticate
  → Get account_id + account_key_id from auth header

↓ Step 2: Resolve Alias
  → Check model_aliases table:
      WHERE account_id = ? 
        AND (account_key_id = ? OR account_key_id IS NULL)
        AND alias_name = "claude-3-5-sonnet"
        AND enabled = 1
  
  → If found: alias = {target_model: "gemini-flash", target_endpoint: NULL}
  → If not found: use original model name

↓ Step 3: Resolve Target
  → If alias.target_endpoint != NULL:
      → Route to custom endpoint (passthrough)
  → Else:
      → Route to pool with model = alias.target_model

↓ Step 4: Response
  → Return with ORIGINAL model name in response
      (client expects "claude-3-5-sonnet", not "gemini-flash")
```

### 3. Precedence Rules

Khi có nhiều alias cùng name:

1. **Key-specific alias** > Account-wide alias
   ```
   account_id="admin", account_key_id="key1", alias_name="gpt-4" → gemini-flash
   account_id="admin", account_key_id=NULL,   alias_name="gpt-4" → gemini-pro
   
   Request with key1 → uses gemini-flash
   Request with key2 → uses gemini-pro
   ```

2. **Enabled alias** > Disabled alias
   ```
   Only return aliases where enabled=1
   ```

3. **Most recently created** (if multiple match)
   ```
   ORDER BY created_at DESC LIMIT 1
   ```

### 4. Self-Managed Custom Endpoints

#### Permission Model

**Who can manage what:**

| Role | Can manage custom endpoints | Can create aliases |
|------|---------------------------|-------------------|
| Admin | All endpoints (global) | All aliases (any account) |
| Member | Own endpoints only (account_id = self) | Own aliases only |

**Isolation:**
- Member A's custom endpoint is invisible to Member B
- Member A's aliases only work for Member A's keys
- Admin can see all (for debugging)

#### API Endpoints

**For Members (Self-Service):**

```http
# List my custom endpoints
GET /api/me/endpoints
Response: [{name, base_url, enabled, model_aliases: [...]}]

# Create my custom endpoint
POST /api/me/endpoints
Body: {
  name: "my-openrouter",
  base_url: "https://openrouter.ai/api/v1",
  auth_key: "sk-or-xxx",
  api_format: "openai"
}
Response: {endpoint_id, name, ...}

# Update my custom endpoint
PATCH /api/me/endpoints/{name}
Body: {enabled: false}

# Delete my custom endpoint
DELETE /api/me/endpoints/{name}

# Create model alias
POST /api/me/aliases
Body: {
  alias_name: "claude-3-5-sonnet",
  target_model: "gemini-flash",
  target_endpoint: null,  // null = use pool
  label: "For Cursor AI"
}

# Create alias pointing to my custom endpoint
POST /api/me/aliases
Body: {
  alias_name: "gpt-4",
  target_model: "claude-3-5-sonnet-20241022",
  target_endpoint: "my-openrouter",
  label: "OpenRouter Claude via GPT-4 name"
}

# List my aliases
GET /api/me/aliases
Response: [{alias_name, target_model, target_endpoint, enabled, ...}]

# Delete alias
DELETE /api/me/aliases/{alias_id}
```

**For Admin:**

```http
# List all endpoints (including member-owned)
GET /admin/endpoints?include_member_owned=true

# Override member endpoint
PATCH /admin/endpoints/{name}
Body: {enabled: false}  // Force disable

# List all aliases
GET /admin/aliases?account_id=xxx

# Delete any alias
DELETE /admin/aliases/{alias_id}
```

### 5. Template System

**Template là GỢI Ý, không phải giới hạn.**

User **hoàn toàn tự do** định nghĩa bất kỳ alias nào:
- Alias name: bất kỳ string nào (không giới hạn regex)
- Target model: bất kỳ model nào trong pool hoặc custom endpoint
- Target endpoint: bất kỳ custom endpoint nào của user

**UI workflow:**

```
┌─────────────────────────────────────────────┐
│ Create Model Alias                          │
├─────────────────────────────────────────────┤
│                                             │
│ [Quick Start Templates ▼]  ← Optional      │
│   • Claude-style Gemini Flash              │
│   • Haiku-style Gemini Lite                │
│   • GPT-4 style Custom Endpoint            │
│   • (Click to auto-fill, can edit after)   │
│                                             │
│ ─── OR Manual Setup ─────────────────────  │
│                                             │
│ Alias Name: [_________________________]    │
│ (What your client will call)               │
│ Example: "claude-3-5-sonnet-20241022"      │
│          "gpt-4-turbo-preview"             │
│          "my-super-model-v1"               │
│                                             │
│ Target Model: [_______________________]    │
│ (Real model to use)                        │
│ Dropdown: Pool models + Custom endpoints   │
│                                             │
│ Route Via:                                 │
│  ( ) Pool (Default)                        │
│  ( ) Custom Endpoint: [Select ▼]          │
│                                             │
│ Apply To:                                  │
│  ( ) All my keys (Account-wide)            │
│  (•) Specific key: [Select ▼]             │
│                                             │
│ Label: [___________________________]       │
│ (Optional note for yourself)               │
│                                             │
│ [Cancel]  [Create Alias]                   │
└─────────────────────────────────────────────┘
```

**Example Templates (UI suggestions only):**

```typescript
const EXAMPLE_TEMPLATES = [
  // Common spoofs
  {
    name: "Claude Sonnet → Gemini Flash",
    alias_name: "claude-3-5-sonnet-20241022",
    target_model: "gemini-flash",
    target_endpoint: null,
    description: "Bypass Claude regex, use fast Gemini"
  },
  {
    name: "GPT-4 → Gemini Pro",
    alias_name: "gpt-4-turbo-preview",
    target_model: "gemini-pro",
    target_endpoint: null,
    description: "Bypass GPT regex, use quality Gemini"
  },
  {
    name: "O1 → Gemini Flash",
    alias_name: "o1-preview",
    target_model: "gemini-flash",
    target_endpoint: null,
    description: "O1 style name for Gemini"
  },
  
  // Custom endpoint examples
  {
    name: "GPT-4 → My Claude Endpoint",
    alias_name: "gpt-4",
    target_model: "claude-3-5-sonnet-20241022",
    target_endpoint: "my-anthropic",
    description: "Use real Claude via GPT name"
  },
  {
    name: "Gemini → OpenRouter Mixtral",
    alias_name: "gemini-pro",
    target_model: "mixtral-8x7b-32768",
    target_endpoint: "my-openrouter",
    description: "OpenRouter via Gemini name"
  },
  
  // Creative use cases
  {
    name: "company-ai-v1 → Gemini Flash",
    alias_name: "company-ai-v1",
    target_model: "gemini-flash",
    target_endpoint: null,
    description: "Internal model name"
  }
];
```

**Key point:** Templates chỉ là quick-start. User có thể:
- Ignore template hoàn toàn
- Tạo alias với bất kỳ name nào (kể cả không giống Claude/GPT/Gemini)
- Point đến bất kỳ target nào
- Tạo unlimited aliases

## Implementation Plan

### Phase 1: Database & Core Logic

1. **Create migration:**
   - Add `model_aliases` table
   - Add index

2. **Create CRUD module:**
   - `src/backend/model_aliases.py`
   - Functions: `add_alias()`, `get_alias()`, `list_aliases()`, `delete_alias()`

3. **Update model resolver:**
   - `src/logical_HQ_translator/model_resolver.py`
   - Add `resolve_alias()` function
   - Call it BEFORE existing model resolution

### Phase 2: API Endpoints

1. **Member self-service endpoints:**
   - `src/server/openai_server/routes/me/endpoints.py` (NEW)
   - `src/server/openai_server/routes/me/aliases.py` (NEW)

2. **Admin endpoints:**
   - Update `src/server/openai_server/routes/admin/endpoints.py`
   - Create `src/server/openai_server/routes/admin/aliases.py`

### Phase 3: Integration

1. **Update proxy handlers:**
   - `src/server/openai_server/routes/proxy.py`
   - `src/server/openai_server/routes/proxy_stream.py`
   - Resolve alias → replace model name → route

2. **Update response formatter:**
   - Replace model name in response with ORIGINAL alias name
   - User sent "claude-3-5-sonnet" → response must say "claude-3-5-sonnet"
   - NOT "gemini-flash"

### Phase 4: UI/UX

1. **Member dashboard:**
   - Tab: "My Custom Endpoints"
   - Tab: "Model Aliases"
   - Template picker UI

2. **Admin dashboard:**
   - View all member endpoints
   - Bulk enable/disable

## Security Considerations

### 1. Quota Enforcement

**Question:** Alias ảnh hưởng quota như thế nào?

**Answer:** Quota enforcement based on ACTUAL model used, not alias.

```
User has quota: 100 RPM for gemini-flash

Request 1: model="claude-3-5-sonnet" (alias → gemini-flash)
  → Count against gemini-flash quota

Request 2: model="gemini-flash" (direct)
  → Count against gemini-flash quota

Both requests consume the same quota pool.
```

### 2. Model Discovery

**Question:** User có thể discover all available models qua alias không?

**Answer:** Không. Aliases are private (per-account/key).

```
GET /v1/models
→ Returns: pool models + member's custom endpoint models + member's aliases

Member A cannot see Member B's aliases.
```

### 3. Rate Limiting

**Question:** Alias có bypass rate limit không?

**Answer:** Không. Rate limit dựa trên account_key_id, không phải model name.

### 4. Audit Logging

**All alias usage should be logged:**

```
[INFO] Alias resolved: alias="claude-3-5-sonnet" → target="gemini-flash" (account=admin, key=key1)
[INFO] Request routed: model=gemini-flash, endpoint=pool, account=admin
```

## Example Scenarios

### Scenario 1: Cursor AI with Gemini Flash

**Setup:**
```sql
INSERT INTO model_aliases VALUES (
  'alias-001',
  'user123',
  NULL,
  'claude-3-5-sonnet-20241022',
  'gemini-flash',
  NULL,
  1,
  'For Cursor AI',
  1234567890,
  1234567890
);
```

**Request:**
```http
POST /v1/chat/completions
Authorization: Bearer sk-user123-abc123
Body: {"model": "claude-3-5-sonnet-20241022", "messages": [...]}
```

**Router behavior:**
1. Authenticate → account_id="user123"
2. Resolve alias → target_model="gemini-flash"
3. Route to pool → call gemini-flash key
4. Response → model="claude-3-5-sonnet-20241022" (original name)

**Cursor AI sees:** Claude Sonnet response (bypass regex)

### Scenario 2: Custom Endpoint + Alias

**Setup:**
```sql
-- Custom endpoint
INSERT INTO custom_endpoints VALUES (
  'my-anthropic',
  'https://api.anthropic.com/v1',
  'sk-ant-xxx',
  1,
  '[]', '[]', '[]',
  'user123',
  0,
  'anthropic',
  '{}',
  '2024-01-01'
);

-- Alias pointing to custom endpoint
INSERT INTO model_aliases VALUES (
  'alias-002',
  'user123',
  'key1',
  'gpt-4-turbo',
  'claude-3-5-sonnet-20241022',
  'my-anthropic',
  1,
  'Real Claude via GPT-4 name',
  1234567890,
  1234567890
);
```

**Request:**
```http
POST /v1/chat/completions
Authorization: Bearer sk-user123-key1
Body: {"model": "gpt-4-turbo", "messages": [...]}
```

**Router behavior:**
1. Authenticate → account_id="user123", key_id="key1"
2. Resolve alias → target_model="claude-3-5-sonnet-20241022", target_endpoint="my-anthropic"
3. Route to custom endpoint → passthrough to Anthropic API
4. Response → model="gpt-4-turbo" (original alias name)

**Client sees:** GPT-4 response (but actually Claude)

### Scenario 3: Key-Specific Override

**Setup:**
```sql
-- Account-wide default
INSERT INTO model_aliases VALUES (
  'alias-003',
  'admin',
  NULL,
  'gpt-4',
  'gemini-pro',
  NULL,
  1,
  'Default GPT-4',
  1234567890,
  1234567890
);

-- Key-specific override
INSERT INTO model_aliases VALUES (
  'alias-004',
  'admin',
  'key-premium',
  'gpt-4',
  'gemini-flash',
  NULL,
  1,
  'Premium key uses Flash for speed',
  1234567890,
  1234567890
);
```

**Request 1:**
```http
Authorization: Bearer sk-admin-regular
Body: {"model": "gpt-4", ...}
→ Routes to gemini-pro
```

**Request 2:**
```http
Authorization: Bearer sk-admin-key-premium
Body: {"model": "gpt-4", ...}
→ Routes to gemini-flash (key-specific override)
```

## Migration Path

### For Existing Users

**No breaking changes:**
- Existing requests continue to work (no alias = use original model name)
- Aliases are opt-in feature

**Gradual adoption:**
1. User creates alias
2. Test with new client
3. If works → expand usage
4. If fails → delete alias, no harm done

## Limitations & Future Work

### Known Limitations

1. **One alias → one target**
   - Cannot round-robin between multiple targets
   - Workaround: Create multiple aliases (gpt-4-a, gpt-4-b)

2. **No wildcard aliases**
   - Cannot alias "gpt-*" → all GPT models
   - Must create individual aliases

3. **No alias chaining**
   - Cannot alias1 → alias2 → target
   - Only single-level resolution

### Future Enhancements

1. **Load balancing aliases**
   ```sql
   alias_name="gpt-4" → [target1, target2, target3] (round-robin)
   ```

2. **Conditional routing**
   ```sql
   IF context_length > 100k THEN gemini-pro
   ELSE gemini-flash
   ```

3. **Cost-based routing**
   ```sql
   IF time_of_day IN (9-17) THEN cheap_model
   ELSE expensive_model
   ```

## Summary

Model alias system cho phép:
- ✅ Bypass client-side regex validation
- ✅ Self-managed custom endpoints
- ✅ Flexible routing (pool or custom endpoint)
- ✅ Account-wide or key-specific aliases
- ✅ Template system để dễ setup
- ✅ No breaking changes (opt-in)

Next step: Implement Phase 1 (Database + Core Logic)

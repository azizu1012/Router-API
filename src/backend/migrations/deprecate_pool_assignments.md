# Migration: Deprecate pool_assignments field

## Context

Trong thiết kế cũ, custom endpoint có thể tham gia vào pool như một member thông qua field `pool_assignments`:

```json
{
  "name": "my-endpoint",
  "pool_assignments": {
    "gemini-flash": "claude-3-5-sonnet",
    "gemini-pro": "claude-opus-4"
  }
}
```

Nghĩa là: khi request gọi model `gemini-flash`, pool sẽ có thể chọn custom endpoint này và dùng model `claude-3-5-sonnet` từ endpoint đó.

## Vấn đề với thiết kế cũ

1. **Confusing semantics**: Custom endpoint vừa là "provider" vừa là "pool member"
2. **Retry complexity**: Khi custom endpoint fail, phải handle circuit breaker, swap, retry như Gemini key
3. **Pool contamination**: Custom endpoint failure có thể làm toàn bộ pool bị exhausted
4. **Wrong abstraction**: Custom endpoint không phải là "key" để rotate, mà là "destination" để route

## Thiết kế mới

Custom endpoint giờ là **pure passthrough translation bridge**:

```
Client Request 
  ↓
Proxy Handler (check account/key → custom endpoint?)
  ├─ YES → Custom Endpoint Passthrough (translate + forward)
  └─ NO → Pool Manager (Gemini keys rotation)
```

### Assignment model mới:

**Option 1: Account-level assignment**
```json
{
  "name": "my-endpoint",
  "account_id": "admin",
  "account_key_id": null
}
```
→ Tất cả keys thuộc account "admin" sẽ dùng endpoint này

**Option 2: Key-level assignment** (RECOMMENDED)
```json
{
  "name": "my-endpoint",
  "account_id": "admin",
  "account_key_id": "sk-proj-abc123"
}
```
→ Chỉ key "sk-proj-abc123" dùng endpoint này

**Option 3: No assignment** (Public/fallback)
```json
{
  "name": "my-endpoint",
  "account_id": "",
  "account_key_id": null
}
```
→ Không được route tự động, chỉ dùng khi admin config fallback

## Migration steps

### Step 1: Add account_key_id column (DONE)
```sql
ALTER TABLE custom_endpoints ADD COLUMN account_key_id TEXT DEFAULT NULL;
```

### Step 2: Migrate existing pool_assignments data

**Identify affected endpoints:**
```sql
SELECT name, pool_assignments, account_id 
FROM custom_endpoints 
WHERE pool_assignments != '{}' AND pool_assignments IS NOT NULL;
```

**Decision matrix:**

| Current state | Migration action |
|---------------|------------------|
| `pool_assignments != {}` + `account_id != ''` | ✅ Already has account assignment, clear pool_assignments |
| `pool_assignments != {}` + `account_id = ''` | ⚠️ Admin decision required: assign to which account? |
| `pool_assignments = {}` | ✅ No action needed |

**Automated migration:**
```python
# Clear pool_assignments for endpoints with account_id set
UPDATE custom_endpoints 
SET pool_assignments = '{}' 
WHERE account_id != '';
```

**Manual migration for unassigned endpoints:**
```python
# Admin needs to decide: either assign to account or leave as fallback
# Log warning for endpoints that need manual review:
SELECT name, pool_assignments 
FROM custom_endpoints 
WHERE pool_assignments != '{}' AND account_id = '';
```

### Step 3: Update code to ignore pool_assignments

**Files to update:**
- `src/core/providers/custom_endpoint_manager.py` - Remove pool assignment logic
- `src/logical_HQ_translator/model_resolver.py` - Don't check pool_assignments
- `src/backend/endpoints.py` - Mark field as deprecated in docstring

**Keep the field in DB schema** (for now):
- Don't drop column yet (allows rollback)
- Set to `'{}'` for all new endpoints
- In 2-3 releases, can drop column entirely

### Step 4: Update admin UI

Replace pool assignment UI with key-level assignment:

**Old UI:**
```
[Endpoint: my-endpoint]
Pool Assignments:
  gemini-flash → claude-3-5-sonnet
  [Add pool assignment]
```

**New UI:**
```
[Endpoint: my-endpoint]
Assignment:
  Account: [admin ▼]
  Key: [All keys] or [sk-proj-abc123 ▼]
  [Save]
```

### Step 5: Testing

**Test cases:**
1. ✅ Request with key that has custom endpoint → routes to passthrough
2. ✅ Request with key without custom endpoint → routes to pool
3. ✅ Custom endpoint 400 error → returns immediately (no retry)
4. ✅ Custom endpoint 503 error → returns immediately (no retry)
5. ✅ Account-level assignment → all keys use same endpoint
6. ✅ Key-level assignment → only that key uses endpoint

### Step 6: Deprecation timeline

**v2.1 (current):**
- Add `account_key_id` column
- Implement passthrough routing
- Code ignores `pool_assignments`
- Field kept in DB (backward compat)

**v2.2 (next release):**
- Add deprecation warning in admin UI
- Automatically migrate remaining `pool_assignments` data
- Document field as deprecated

**v2.3 (future):**
- Drop `pool_assignments` column from schema
- Remove field from all code

## Rollback plan

If passthrough architecture has issues:

1. **Revert proxy handlers** - remove passthrough check
2. **Re-enable pool logic** - custom endpoints fall back to pool routing
3. **No DB rollback needed** - `account_key_id` is additive, doesn't break old logic

Field `pool_assignments` is kept in DB for safety during migration period.

## Impact assessment

### Breaking changes:
- ❌ **None** - old pool_assignments logic is bypassed by proxy handlers, not removed

### Behavior changes:
- ⚠️ Custom endpoint no longer retries on error (fails fast)
- ⚠️ Custom endpoint no longer participates in pool rotation
- ✅ More predictable routing (no hidden pool swapping)
- ✅ Better error messages (no "hệ thống quá tải" confusion)

### Performance:
- ✅ Faster routing (skip pool acquire/release)
- ✅ No lock contention with Gemini keys
- ✅ Cleaner separation of concerns

## Related documents
- `docs/custom_endpoint_cleanup_plan.md` - Overall cleanup strategy
- `docs/architecture_overview.md` - Updated architecture diagrams
- `CLAUDE.md` - Updated instructions for developers

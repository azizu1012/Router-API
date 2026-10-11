# Model Spoofing - Tổng Quan Nhanh

## 🎯 Bạn Muốn Gì?

1. ✅ **Spoof model name** - Client gọi `gpt-4`, thực tế chạy `gemini-flash`
2. ✅ **Bypass validation** - Cursor AI, OpenAI SDK chỉ chấp nhận regex cụ thể
3. ✅ **Self-service** - Members tự tạo custom endpoint + alias, không cần admin
4. ✅ **Template gợi ý** - UI có examples, user tự do customize (không giới hạn)
5. ✅ **Spoof cả model list** - `/v1/models` chỉ show alias, ẩn model gốc

## ✅ Đã Làm Xong

### Backend (100%)
- ✅ Database schema: `model_aliases` table
- ✅ CRUD: `src/backend/model_aliases.py`
- ✅ Alias resolution: `resolve_alias()` with precedence
- ✅ Proxy integration: Route based on alias
- ✅ Response spoofing: Replace model name in response
- ✅ Model list spoofing: Hide aliased custom endpoint models
- ✅ Self-service API: `/api/me/endpoints`, `/api/me/aliases`
- ✅ Admin API: `/admin/aliases`
- ✅ Route registration: All routes wired up

### Files Changed/Created

**Database:**
- ✅ `migrations/007_model_aliases.sql`

**Backend:**
- ✅ `src/backend/model_aliases.py` (NEW)
- ✅ `src/backend/endpoints.py` (updated)
- ✅ `src/logical_HQ_translator/model_resolver.py` (updated)

**API Routes:**
- ✅ `src/server/openai_server/routes/self_service.py` (NEW)
- ✅ `src/server/openai_server/routes/admin/admin_aliases.py` (NEW)
- ✅ `src/server/openai_server/routes/standard_routes.py` (updated)
- ✅ `src/server/openai_server/routes/proxy.py` (updated)
- ✅ `src/server/openai_server/routes/proxy_stream.py` (updated)
- ✅ `src/server/openai_server/routes/__init__.py` (updated)
- ✅ `src/server/openai_server/routes/admin/__init__.py` (updated)

**Documentation:**
- ✅ `docs/model_spoofing_design.md`
- ✅ `docs/model_spoofing_quickstart.md`
- ✅ `docs/model_spoofing_implementation.md`
- ✅ `docs/model_spoofing_tests.md`
- ✅ `IMPLEMENTATION_SUMMARY.md`

## ⏳ Còn Lại

### Task #19: Apply Migration
```bash
cd D:\AI_Projects\router_api
sqlite3 usage.db < migrations/007_model_aliases.sql
```

### Task #20: Test APIs
```bash
# 1. Create endpoint
curl -X POST http://localhost:8000/api/me/endpoints \
  -H "Authorization: Bearer sk-admin-test" \
  -d '{"name":"my-openrouter","base_url":"https://openrouter.ai/api/v1","auth_key":"sk-or-xxx"}'

# 2. Create alias
curl -X POST http://localhost:8000/api/me/aliases \
  -H "Authorization: Bearer sk-admin-test" \
  -d '{"alias_name":"gpt-4","target_model":"anthropic/claude-3-5-sonnet","target_endpoint":"my-openrouter"}'

# 3. Test model list
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-admin-test"
# Should show "gpt-4" only, not "anthropic/claude-3-5-sonnet"

# 4. Test chat
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-admin-test" \
  -d '{"model":"gpt-4","messages":[{"role":"user","content":"Hi"}]}'
# Response should have model="gpt-4"
```

### Task #21: Build Frontend UI
- Tab: "Model Aliases" (create/list/edit)
- Tab: "Custom Endpoints" (create/list/edit)
- Template picker với examples (user tự do customize)

## 🔑 Tính Năng Chính

### 1. Full Model List Spoofing (NEW!)
```
Custom endpoint có model: "anthropic/claude-3-5-sonnet"
User tạo alias: "gpt-4" → "anthropic/claude-3-5-sonnet"

GET /v1/models response:
✅ "gpt-4" (alias)
❌ "anthropic/claude-3-5-sonnet" HIDDEN

Client chỉ thấy "gpt-4", không biết đằng sau là Claude!
```

### 2. Request/Response Spoofing
```
Client sends: {"model": "gpt-4", ...}
Router routes to: anthropic/claude-3-5-sonnet @ openrouter
Response returns: {"model": "gpt-4", ...}  <- Spoofed back!
```

### 3. Flexible Precedence
```
Account-wide:  "gpt-4" → gemini-pro
Key-specific:  "gpt-4" → gemini-flash (key=premium)

Request with "premium" key → uses gemini-flash
Request with other keys → uses gemini-pro
```

## 📊 Ví Dụ Thực Tế

### Cursor AI + Gemini Flash
```json
POST /api/me/aliases
{
  "alias_name": "claude-3-5-sonnet-20241022",
  "target_model": "gemini-flash",
  "target_endpoint": null
}
```

Cursor nghĩ đang dùng Claude, thực tế dùng Gemini Flash (rẻ + nhanh).

### OpenAI SDK + Real Claude
```json
POST /api/me/endpoints
{
  "name": "anthropic-direct",
  "base_url": "https://api.anthropic.com/v1",
  "auth_key": "sk-ant-xxx"
}

POST /api/me/aliases
{
  "alias_name": "gpt-4-turbo",
  "target_model": "claude-3-5-sonnet-20241022",
  "target_endpoint": "anthropic-direct"
}
```

OpenAI SDK gọi `gpt-4-turbo`, thực tế là Claude từ Anthropic API.

## 🚀 Quick Start

1. **Apply migration:**
   ```bash
   sqlite3 usage.db < migrations/007_model_aliases.sql
   ```

2. **Create custom endpoint (optional):**
   ```bash
   curl -X POST http://localhost:8000/api/me/endpoints \
     -H "Authorization: Bearer YOUR_KEY" \
     -d '{
       "name": "my-endpoint",
       "base_url": "https://api.example.com/v1",
       "auth_key": "sk-xxx"
     }'
   ```

3. **Create alias:**
   ```bash
   curl -X POST http://localhost:8000/api/me/aliases \
     -H "Authorization: Bearer YOUR_KEY" \
     -d '{
       "alias_name": "gpt-4",
       "target_model": "gemini-flash",
       "target_endpoint": null
     }'
   ```

4. **Use it:**
   ```python
   from openai import OpenAI
   
   client = OpenAI(
       api_key="YOUR_KEY",
       base_url="http://localhost:8000/v1"
   )
   
   response = client.chat.completions.create(
       model="gpt-4",  # Actually uses gemini-flash!
       messages=[{"role": "user", "content": "Hello"}]
   )
   ```

## 📚 Chi Tiết

- **Design:** `docs/model_spoofing_design.md`
- **API Examples:** `docs/model_spoofing_implementation.md`
- **Test Cases:** `docs/model_spoofing_tests.md`
- **Full Summary:** `IMPLEMENTATION_SUMMARY.md`

## ✨ Highlights

1. **Zero breaking changes** - Tất cả existing code vẫn chạy bình thường
2. **Opt-in** - User không dùng thì không ảnh hưởng gì
3. **Self-managed** - Members tự lo, không cần admin
4. **Full spoofing** - Cả request, response, VÀ model list đều được spoof
5. **Template flexible** - UI gợi ý examples, user tự do customize

---

**Ready to test!** 🎉

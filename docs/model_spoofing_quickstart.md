# Model Name Spoofing - Quick Start Guide

## Tính năng đã implement

### 1. Model Alias System
User có thể tạo alias để spoof model name:
- `claude-3-5-sonnet` → route đến `gemini-flash`
- `gpt-4-turbo` → route đến custom endpoint của user
- Bypass client regex validation (Cursor AI, OpenAI SDK, etc.)

### 2. Self-Managed Custom Endpoints
Members tự quản lý endpoint của chính họ:
- Add/edit/delete custom endpoint
- Không cần admin approve
- Kết hợp với alias để spoof model name

### 3. Template System
UI có template suggestions (user tự do tạo bất kỳ alias nào):
- Claude-style Gemini Flash
- GPT-4 style Gemini Pro
- Haiku-style Gemini Lite
- Custom endpoint aliases

---

## Files đã tạo

### Backend
- `migrations/007_model_aliases.sql` - Database schema
- `src/backend/model_aliases.py` - CRUD operations
- `src/backend/endpoints.py` - Updated for member ownership

### Core Logic
- `src/logical_HQ_translator/alias_resolver.py` - Alias resolution
- Updated `src/server/openai_server/routes/openai_models.py` - Model list spoofing
- Updated `src/server/openai_server/routes/completions_routes.py` - Response spoofing

### API Routes
- `src/server/openai_server/routes/self_service.py` - Member self-service API
- `src/server/openai_server/routes/admin/admin_aliases.py` - Admin management

### Documentation
- `docs/model_spoofing_design.md` - Detailed design document
- `docs/model_spoofing_implementation.md` - Implementation guide & API reference

---

## Cần làm để hoàn thiện

### 1. Apply migration
```bash
sqlite3 usage.db < migrations/007_model_aliases.sql
```

### 2. Register routes trong app.py
```python
from src.server.openai_server.routes import self_service
from src.server.openai_server.routes.admin import admin_aliases

app.include_router(self_service.router)
app.include_router(admin_aliases.router)
```

### 3. Test endpoints
```bash
# Test tạo endpoint
curl -X POST http://localhost:8000/api/me/endpoints \
  -H "Authorization: Bearer sk-xxx" \
  -d '{"name":"my-ep","base_url":"https://api.example.com","auth_key":"sk-xxx"}'

# Test tạo alias
curl -X POST http://localhost:8000/api/me/aliases \
  -H "Authorization: Bearer sk-xxx" \
  -d '{"alias_name":"claude-3-5-sonnet","target_model":"gemini-flash"}'

# Test dùng alias
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-xxx" \
  -d '{"model":"claude-3-5-sonnet","messages":[{"role":"user","content":"Hi"}]}'
```

### 4. Build frontend UI
- Alias management page
- Endpoint management page  
- Template picker
- Model selector dropdown

---

## Architecture Flow

```
Client gọi: model="claude-3-5-sonnet"
    ↓
Alias resolver: claude-3-5-sonnet → gemini-flash
    ↓
Route đến Gemini pool (hoặc custom endpoint)
    ↓
Response spoofing: model="claude-3-5-sonnet" (trả về tên gốc)
    ↓
Client nhận: "claude-3-5-sonnet"
```

---

## Key Features

✅ **Client bypass** - Cursor AI, OpenAI SDK validation  
✅ **Self-service** - Members tự quản lý endpoint/alias  
✅ **Precedence** - Key-specific > Account-wide  
✅ **Template** - Gợi ý setup nhanh (user tự do tùy chỉnh)  
✅ **Spoofing** - Cả request & response model name  
✅ **Model list** - `/v1/models` include aliases  
✅ **Custom endpoint spoofing** - Ẩn model name gốc, chỉ hiện alias  
✅ **Security** - Members chỉ quản lý của chính họ  

---

## Example Use Cases

### Use Case 1: Cursor AI
Cursor chỉ chấp nhận `claude-*` names:
```json
{
  "alias_name": "claude-3-5-sonnet-20241022",
  "target_model": "gemini-flash"
}
```

### Use Case 2: OpenAI SDK
SDK reject non-GPT names:
```json
{
  "alias_name": "gpt-4-turbo",
  "target_model": "gemini-pro"
}
```

### Use Case 3: Custom Endpoint
Member có OpenRouter key, gọi qua `gpt-4` name:
```json
{
  "alias_name": "gpt-4",
  "target_model": "anthropic/claude-3-5-sonnet",
  "target_endpoint": "my-openrouter"
}
```

**Quan trọng:** Khi có alias này, `/v1/models` sẽ:
- ✅ Show: `gpt-4` (alias)
- ❌ Ẩn: `anthropic/claude-3-5-sonnet` (model gốc)

Client chỉ nhìn thấy `gpt-4`, không biết đằng sau là Claude từ OpenRouter.

---

## Chi tiết kỹ thuật

Xem `docs/model_spoofing_implementation.md` để biết:
- Full API reference
- Database schema
- Testing checklist
- Security considerations
- Future enhancements

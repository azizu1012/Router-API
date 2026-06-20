# Router API — Claude Code Instructions

## Nguyên tắc làm việc (2026-06)

### 1. TODO bắt buộc cho task phức tạp

- Khi thấy task từ 3 bước trở lên hoặc chạm nhiều file → **tự động tạo TODO list ngay** trước khi làm.
- Ghi cụ thể từng bước, theo đúng thứ tự cần làm.
- Làm xong bước nào → **tick hoàn thành bước đó ngay** (dùng todowrite).
- Không tự ý làm bước sau khi bước trước chưa xong.
- Chỉ skip TODO nếu task chỉ 1-2 bước đơn giản.

### 2. Đọc file theo lô, song song

- Luôn lập danh sách các file cần đọc trước khi gọi tool.
- Ưu tiên đọc nhiều file một lượt bằng gọi song song.
- Tránh đọc tuần tự từng file nếu không bắt buộc.
- Dùng tìm kiếm nhanh trước khi đọc sâu (grep/search) khi chưa chắc vị trí.

### 3. Sub-agent chỉ khi thật cần

- Chỉ dùng sub-agent khi cần scan rộng hoặc tìm kiếm khó.
- Không dồn hết mọi việc vào một sub-agent.
- Mỗi sub-agent chỉ làm 1 mục tiêu, chỉ đọc file, trả về dữ liệu có cấu trúc.

Mẫu prompt sub-agent:
- `description`: 3-5 từ, đúng mục tiêu.
- `prompt`: nói rõ chỉ RESEARCH, không ghi file, liệt kê chính xác file cần đọc, yêu cầu trả về bảng/ASCII.

### 4. Không tự động cập nhật snapshot
- Chỉ cập nhật snapshot khi người dùng yêu cầu rõ ràng.


## Cấu trúc dự án

```
src/
  api/claude_proxy.py     # Claude→Gemini proxy (stream + non-stream)
  logical_HQ_translator/   # Resources/converters shared between Claude proxy and OpenCode proxy
  server/pass_through_server/ # Pass-through server endpoints (OpenAI, Anthropic, Gemini)
  backend/                 # DB layer (accounts, endpoints, keys, schema)
  core/                    # Router, rate limiter, config, usage logger
console/admin_console.py  # CLI admin tool
main.py                   # Entry point (uvicorn)
```

## Quy tắc code

- Không commit file `.env`, `usage.db`, `logs/`
- DB dùng SQLite qua `src/backend/_db.py` (connection pool + RLock)
- Không tạo logger mới. Dùng logger sẵn có:
   - `from src.core.config import logger` cho log chung
   - hoặc `from src.core.logger import logger_system/logger_proxy/logger_keys/logger_api/logger_keepalive` theo ngữ cảnh

## Pool & Key Management — Rules (CRITICAL)

### 1. Luôn pass pool name (không phải member alias) cho `_resolve_model` trong pool mode

```python
# ✅ ĐÚNG — pool name cho reserve_key tìm MODEL_POOLS entry
_resolve_model(body, model_alias, pool_mode=True, ...)

# ❌ SAI — member alias (e.g. "gemini-flash-35") không khớp MODEL_POOLS
_resolve_model(body, actual_alias, pool_mode=True, ...)
```

Khi `reserve_key` nhận pool name (`"gemini-flash"`), nó enter pool path, iterate qua members, dùng pool-level RPM (key_count × cfg.rpm) cho mỗi member. Nếu nhận member name, nó fallback non-pool path với `rpm_limit=cfg.rpm` (chỉ 1 key), gây quota mismatch → freeze → pool swap loop → timeout.

### 2. Luôn dùng pool name cho `acquire_quota`

```python
# ✅ ĐÚNG — pool-level rate limiter (RPM = sum_members × total_keys)
acquire_quota(tokens, model_alias)

# ❌ SAI — per-member limiter, không phản ánh dung lượng thực
acquire_quota(tokens, actual_alias)
```

### 3. Luôn track `member_used` từ reservation cho error recording

`reserve_key` (pool path) trả về reservation với `model_alias` = member thực tế được chọn. Member này có thể khác `pool.current_model` nếu member đầu hết key. Dùng `member_used` để:

```python
member_used = reservation.get("model_alias", pool.current_model)
pool.record_failure(member_used, reason)  # ✅ ghi đúng member
```

### 4. Rate limiter RPM cho pool = tổng RPM các member × số key

Pool level RPM được rate limiter tự tính = `cfg.rpm × key_count` cho mỗi alias. Đây là lý do dashboard dùng `limiter.rpm_limit`, không dùng `cfg.rpm`.

### 5. Xử lý lỗi và đóng băng Key (Key Freezing)

Các lỗi từ API được phân loại và xử lý khác nhau:

- **Lỗi tạm thời không đóng băng Key (ví dụ: `unavailable` / 503, các lỗi `server_error`, `timeout`, `grounding_fallback`, `unknown`):** Với các lỗi này, hệ thống chỉ thực hiện backoff (chờ 5 giây) và thử lại. Key **không bị đóng băng**, và không có `pool.record_failure`. Thời gian cooldown cho các lỗi này được cấu hình bởi `KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS` với các hệ số nhân cụ thể trong `get_penalty_config`.

- **Lỗi tạm thời có đóng băng Key (ví dụ: `rate_limit` / 429, `rate_limit_rpm_tpm`, `rate_limit_rpd`, `project_quota_429`):** Khi gặp các lỗi này, key **bị đóng băng** với thời gian cooldown động.
    - Đối với `rate_limit_rpd` và `project_quota_429`, thời gian đóng băng là đến nửa đêm giờ Thái Bình Dương (`get_seconds_until_pacific_midnight()`).
    - Đối với `rate_limit` và `rate_limit_rpm_tpm`, thời gian đóng băng là `config.KEY_429_COOLDOWN_SECONDS * 10`, và có thể tăng theo số lần thất bại liên tiếp.
    - Các lỗi này vẫn được coi là tạm thời nhưng yêu cầu đóng băng key để tránh lặp lại lỗi và bảo vệ quota.

- **Lỗi vĩnh viễn có đóng băng Key và ghi nhận lỗi (ví dụ: `bad_request`, `billing_error`, `invalid_key`, `permission_denied`, `project_denied`):** Key **bị đóng băng** và `pool.record_failure` được gọi. Thời gian đóng băng cho các lỗi này thường dài hơn và cũng được xác định trong `get_penalty_config`.

### 6. `asyncio.shield` bắt buộc khi dùng `wait_for` cho keepalive

Khi wrap `__anext__()` của async generator với `asyncio.wait_for(timeout=4.0)`:
```python
# ✅ ĐÚNG — shield bảo vệ generator khỏi bị cancel khi timeout
evt = await asyncio.wait_for(asyncio.shield(it.__anext__()), timeout=4.0)

# ❌ SAI — timeout sẽ cancel generator, stream chết vĩnh viễn → response rỗng
evt = await asyncio.wait_for(it.__anext__(), timeout=4.0)
```

`wait_for` cancel task chứa `__anext__()` khi timeout → `CancelledError` propagates vào async generator → generator chết → lần gọi `__anext__()` sau chỉ trả về `StopAsyncIteration` → response rỗng.

### 7. Thinking Config — sub-agent check TRƯỚC body thinking

Claude Code gửi `thinking` trong **mọi** request (kể cả sub-agent). `is_sub_agent_body` phải được gọi **trước** khi xử lý body thinking:

```python
def _build_litellm_thinking(body, model_id):
    # ✅ ĐÚNG — check sub-agent đầu tiên, bất kể body có thinking hay không
    if is_sub_agent_body(body):
        return {}
    ...
    thinking = body.get("thinking")  # chỉ xử lý sau khi đã loại sub-agent
```

Nếu check sau (chỉ khi `thinking is None`), sub-agent có thinking trong body sẽ bypass check → thinking enabled trên flash-lite → response rỗng.

### 8. Thinking `"adaptive"` dùng budget vừa phải

```python
# ✅ ĐÚNG — budget 4096 cho flash, 8192 cho pro — đủ thinking nhưng không treo lâu
if ttype == "adaptive":
    budget = 4096 if "flash" in m else 8192
    return {"thinking": {"type": "enabled", "budget_tokens": budget}}

# ❌ SAI — không budget thì Gemini think vô hạn, TTFB 3ph+
if ttype == "adaptive":
    return {"thinking": {"type": "enabled"}}

# ❌ SAI — ép budget 24576+ trên flash làm model think hết budget, không output
budget = 32768 if "pro" in m else 24576
return {"thinking": {"type": "enabled", "budget_tokens": budget}}
```

### 9. Luôn strip field `display` khỏi thinking config

Claude Code gửi `thinking: {type: "adaptive", display: "summarized"}`. Trường `display` không hợp lệ với Gemini/litellm — gây lỗi tiềm ẩn. Luôn tạo dict mới chỉ với các field hợp lệ:

```python
# ✅ ĐÚNG — chỉ giữ type và budget_tokens
return {"thinking": {"type": "enabled", "budget_tokens": budget}}

# ❌ SAI — copy toàn bộ dict giữ lại display
t_copy = thinking.copy()
t_copy["type"] = "enabled"
return {"thinking": t_copy}
```

### 10. File & function mapping

| File | Function | Vai trò |
|------|----------|---------|
| `opencode_proxy/handler/stream_executor.py:578` | `_stream_with_pool` | Pool stream entry — pool name → `_resolve_model` |
| `opencode_proxy/handler/stream_executor.py:596` | `_stream_with_pool` | Pool-level quota check |
| `opencode_proxy/handler/stream_executor.py:583` | `_stream_with_pool` | `member_used` extraction |
| `opencode_proxy/handler/proxy.py:226` | `_nonstream_pool` | Pool non-stream — pool name → `_resolve_and_call` |
| `opencode_proxy/handler/proxy.py:245` | `_nonstream_pool` | `member_used` in exception handler |
| `core/router/core/key_resolver.py:85` | `reserve_key` | Pool path: iterate members, use `MODEL_POOLS.get(model_alias)` |
| `core/router/core/key_resolver.py:220` | `reserve_key` | Return `model_alias` = actual member selected |
| `server/stats_pusher.py` | `StatsPusher._snapshot` | Push real-time RPM/TPM per model alias |
| `server/openai_server/routes/dashboard_routes.py:345` | `get_model_pools_api` | Pool detail API with members + rate limiter values |

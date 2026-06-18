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

### 5. File & function mapping

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

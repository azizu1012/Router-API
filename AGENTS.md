# Project Instructions

## TODO bắt buộc cho task phức tạp

- Khi thấy task từ 3 bước trở lên hoặc chạm nhiều file → **tự động tạo TODO list ngay** trước khi làm.
- Ghi cụ thể từng bước, theo đúng thứ tự cần làm.
- Làm xong bước nào → **tick hoàn thành bước đó ngay** (dùng todowrite).
- Không tự ý làm bước sau khi bước trước chưa xong.
- Chỉ skip TODO nếu task chỉ 1-2 bước đơn giản.

## Nguyên tắc chung

- Đọc file theo lô, song song khi có thể.
- Sub-agent chỉ dùng khi cần scan rộng hoặc tìm kiếm khó.
- Mỗi sub-agent chỉ làm 1 mục tiêu.

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

### 5. 429 và 503 xử lý như nhau — không freeze key, không count failure

Cả rate_limit (429) và unavailable (503) đều là lỗi tạm thời từ API. Không freeze key, không `pool.record_failure` — chỉ backoff 5s rồi retry. Key freeze và member failure chỉ áp dụng cho lỗi vĩnh viễn (bad_request, billing_error, invalid_key).

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

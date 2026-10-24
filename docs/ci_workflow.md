# CI Workflows

Hai workflow chạy trên mỗi push lên `main` và mỗi pull request. `secret_scan.yml`
có thêm `workflow_dispatch` để bấm tay.

| Workflow | Job | Tính chất | Nội dung |
|---|---|---|---|
| `ci.yml` | `test` | **gate** — fail là run đỏ | `pytest -q` toàn bộ suite, sau đó `scripts/scan_security.py` |
| `secret_scan.yml` | `scan` | gate cho diff, advisory cho lịch sử | credential mới trong diff làm đỏ; finding cũ chỉ báo cáo |

`ci.yml` chạy trên `ubuntu-latest`, Python 3.11, timeout 15 phút, và **không** có
`|| true`: exit code của pytest chính là kết quả job. Vì vậy test suite là một
phần của CI, không phải việc chỉ chạy local.

> Tài liệu này từng ghi "test suite không chạy trong CI". Điều đó sai từ commit
> `7059aa0` (2026-10-09), khi `ci.yml` được thêm. Nếu bạn đọc một bản docs cũ
> nói ngược lại, bản đó lỗi thời.

## 1. Test suite

CI chạy đúng lệnh này:

```bash
python -m pytest -q
python scripts/scan_security.py
```

Chạy local cũng cùng lệnh (bỏ `-q` nếu muốn xem chi tiết):

```bash
pytest
```

`pytest.ini` dùng `python_files` để chỉ định đúng danh sách test, thay vì để pytest quét mọi `test_*.py`. Danh sách hiện tại:

| File | Phủ |
|---|---|
| `test_pool.py` | slot concurrency, ưu tiên custom endpoint, member kẹt không chặn cả pool |
| `test_transient_penalties.py` | penalty & cooldown |
| `test_custom_pool_resolver.py` | custom endpoint trong pool |
| `test_rtk_filters.py` | lọc output tool |
| `test_message_converter_truncation.py` | Anthropic → OpenAI message conversion |
| `test_search_endpoint.py` | `/v1/search` |
| `test_anthropic_protocol.py` | tuân thủ Anthropic Messages API |
| `test_anthropic_integration.py` | proxy Claude end-to-end (PoolManager mock) |
| `test_account_auth.py` | token format, giới hạn 4 lớp, invite code |
| `test_secret_scanner.py` | scanner bắt secret thật, không báo động giả |
| `test_schema_bootstrap.py` | DDL lúc import khớp `schema.py` |
| `test_effort_mapping.py` | effort Anthropic/OpenAI → thinking level của Gemini |
| `test_invite_codes.py` | va chạm mã 4 số không làm hỏng nút admin |
| `test_dashboard_tokens.py` | API quản lý token qua app thật, có DB thật |
| `test_master_key.py` | Master Key 46 ký tự (`sk-live-admin-`), collision guard, trần quyền hạn admin |
| `test_retry_counter.py` | Bộ đếm số lần retry trong PoolManager khi gặp lỗi tạm thời |
| `test_error_classification.py` | Phân loại lỗi HTTP/SDK (transient vs hard error, status 429, 503) |
| `test_layering.py` | Tầng dưới không import tầng trên (AST, cần chạy sạch) |
| `test_custom_pool_rate_limit.py` | Rate limit độc lập cho custom endpoint pools |
| `test_error_visibility.py` | Minh bạch thông điệp lỗi cho client thay vì giấu lỗi |
| `test_stream_impl_seams.py` | Ranh giới SSE stream và chunk parsing giữa Anthropic & OpenAI |
| `test_responses_mapping.py` | Mapping response schema giữa Gemini, Anthropic và OpenAI |
| `test_api_help.py` | Endpoint `/stats/help` tài liệu hướng dẫn tích hợp API |
| `test_tier_limits.py` | Trần RPM/TPM/RPD theo tier, clamp xuống không nâng |
| `test_password_recovery.py` | Bản mã hoá mật khẩu cho admin, hash vẫn là thứ xác thực |
| `test_admin_password_recovery.py` | Route admin xem mật khẩu, chỉ admin, nói rõ khi không đọc được |
| `test_must_change_password.py` | Cờ `must_change` từ DB tới banner; admin bật/tắt được |
| `test_search_engine_precedence.py` | Engine theo dialect; override ở tầng account đã bị bỏ |
| `test_log_permissions.py` | Phân quyền RBAC xem log stream (system/keys/web vs proxy/api) |
| `test_pool_member_toggle.py` | Bật/tắt model con trong pool: lọc member, cache pool, aggregate, route toggle |
| `test_responses_and_search_dialects.py` | `/v1/responses` trả typed event; Anthropic SDK server tool web search không bị drop |
| `test_stream_keepalive.py` | Keepalive không bắn vào chính generator đang chạy |
| `test_openai_stream_usage_chunk.py` | Chunk `choices: []` chỉ gửi khi client yêu cầu `include_usage` |
| `test_anthropic_stream_order.py` | Thứ tự SSE Anthropic (`message_start` trước `ping`), ping đúng spec, và tool_use không bị rơi |
| `test_responses_conformance.py` | Mọi event `/v1/responses` validate bằng model chính thức của OpenAI SDK, kể cả `strict=True`; tool call phải quay lại đủ |
| `test_finish_reason.py` | `finish_reason` luôn nằm trong enum OpenAI; có tool call thì là `tool_calls` |
| `test_uncovered_routes.py` | `/v1/completions` không 503 khi thiếu `max_tokens`; `/v1/models` là superset hợp lệ; `web_search` chạy server-side chứ không rò tool ra client |
| `test_hosted_search_shape.py` | Hosted web search đúng hình dạng chuẩn của từng provider: `web_search_call` + `sources` + `url_citation` bên OpenAI, `server_tool_use` + citations + `usage.server_tool_use` + `max_uses` bên Anthropic, không tự bật search. Gate dùng `strict=True` **và** `extra="forbid"` — `strict` một mình không bắt field ngoài spec |

Bảng trên là danh sách chọn lọc các suite chính, **không phải toàn bộ**. Nguồn sự
thật là `pytest.ini`: file không nằm trong `python_files` sẽ **không được
collect**, và vì thế CI vẫn xanh trong khi test đó chưa từng chạy. Muốn kiểm tra
một file có thực sự được chạy hay không:

```bash
python -m pytest --collect-only -q | Select-String test_my_new_thing
```

Chạy nhanh một file:

```bash
pytest tests/test_anthropic_protocol.py -v
```

Ba test dưới đây không dùng mock DB nên đáng chạy trước khi động vào tầng DB:

- `test_schema_bootstrap.py` — so DDL của `_db.py` với khai báo trong `schema.py`
- `test_dashboard_tokens.py` — chạy app thật trên một `usage.db` tạm
- `test_pool.py` — có mutation test, mọi acquire đều bọc `asyncio.wait_for` nên hỏng sẽ fail chứ không treo

## 2. Thêm test mới

`python_files` là **allowlist**, không phải glob. Pytest chỉ collect đúng những
tên nằm trong dòng đó; mọi file `test_*.py` khác bị bỏ qua trong im lặng. Đây
không phải chuyện lý thuyết: 10 file test đã track từng không được đăng ký, nên
CI xanh trong khi chúng chưa từng chạy.

Thêm file mới cần **cả ba** bước:

```bash
# 1. viết test
# 2. đăng ký vào pytest.ini (thêm tên vào cuối dòng python_files)
# 3. cho phép git track
!tests/test_my_new_thing.py

# 4. xác nhận nó thực sự được collect
python -m pytest --collect-only -q | Select-String test_my_new_thing
pytest tests/test_my_new_thing.py -v
```

Bỏ bước 2 thì test chạy được khi gọi thẳng đường dẫn, nhưng `pytest -q` trong
CI không thấy nó — và không có cảnh báo nào. Bỏ bước 3 thì file không lên
GitHub, nên CI cũng không có gì để chạy.

## 3. Secret Scan

`scripts/scan_secrets.py` quét file đã được git track (dùng `git ls-files`), nên file local chưa commit không xuất hiện.

```bash
python scripts/scan_secrets.py --git --warn-only    # báo cáo, không fail
python scripts/scan_secrets.py                      # quét cả file chưa track
```

Job `scan` có **hai** lần quét, và chúng không cùng tính chất:

| Bước | Phạm vi | Khi có finding |
|---|---|---|
| Scan current diff | chỉ thay đổi của push/PR | **fail job** — credential mới chặn thay đổi |
| Scan tracked files | toàn bộ lịch sử đã track | chỉ in báo cáo, không fail |

Lý do tách: một key đã thu hồi từ lâu sẽ làm đỏ mọi commit mãi mãi nếu full
history là gate, và một gate đỏ vì lý do không liên quan sẽ bị người ta bỏ qua.

Quy tắc đáng chú ý trong cả hai workflow:

- Action được pin theo commit SHA, không phải tag
- `persist-credentials: false` → checkout không giữ credential cho step sau
- `concurrency` + `cancel-in-progress` → push mới huỷ run cũ
- `timeout-minutes` → không để job treo (CI 15 phút, secret scan 5 phút)

Scanner **không** tự sửa file. Một rewrite tự động là commit bạn chưa review — nên nó chỉ báo cáo, việc sửa là thủ công.


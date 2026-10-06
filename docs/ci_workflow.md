# CI Workflows

Hai workflow, cả hai chạy trên mỗi push lên `main`, mỗi pull request, và theo yêu cầu tay:

| Workflow | Job | Tính chất |
|---|---|---|
| `secret_scan.yml` | `scan` | advisory — phát hiện credential trong file đã track |
| `trustabl.yml` | `scan` | advisory — độ tin cậy agent runtime |

Cả hai `continue-on-error: true`: có finding thì vẫn report nhưng **không làm CI đỏ**.

> Test suite **không** chạy trong CI — xem mục dưới.

## 1. Test suite

Chạy local:

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
| `test_adk_runner.py` | ADK safety parity + optional dependency |
| `test_account_auth.py` | token format, giới hạn 4 lớp, invite code |
| `test_secret_scanner.py` | scanner bắt secret thật, không báo động giả |
| `test_schema_bootstrap.py` | DDL lúc import khớp `schema.py` |
| `test_effort_mapping.py` | effort Anthropic/OpenAI → thinking level của Gemini |
| `test_invite_codes.py` | va chạm mã 4 số không làm hỏng nút admin |
| `test_dashboard_tokens.py` | API quản lý token qua app thật, có DB thật |
| `test_tier_limits.py` | trần RPM/TPM/RPD theo tier, clamp xuống không nâng |
| `test_password_recovery.py` | bản mã hoá mật khẩu cho admin, hash vẫn là thứ xác thực |
| `test_admin_password_recovery.py` | route admin xem mật khẩu, chỉ admin, nói rõ khi không đọc được |
| `test_layering.py` | tầng dưới không import tầng trên (AST, cần chạy sạch) |

Chạy nhanh một file:

```bash
pytest tests/test_anthropic_protocol.py -v
```

Ba test dưới đây không dùng mock DB nên đáng chạy trước khi động vào tầng DB:

- `test_schema_bootstrap.py` — so DDL của `_db.py` với khai báo trong `schema.py`
- `test_dashboard_tokens.py` — chạy app thật trên một `usage.db` tạm
- `test_pool.py` — có mutation test, mọi acquire đều bọc `asyncio.wait_for` nên hỏng sẽ fail chứ không treo

## 2. Thêm test mới

`tests/` chỉ chứa suite. Thêm file mới thì cần **cả hai** bước:

```bash
# 1. viết test
# 2. đăng ký vào pytest.ini
python_files = … test_my_new_thing.py

# 3. cho phép git track
!tests/test_my_new_thing.py

# 4. chạy
pytest tests/test_my_new_thing.py -v
```

Bỏ bước 3 thì test chạy được local nhưng không lên GitHub, và CI báo "no tests ran" cho file đó.

## 3. Secret Scan

`scripts/scan_secrets.py` quét file đã được git track (dùng `git ls-files`), nên file local chưa commit không xuất hiện.

```bash
python scripts/scan_secrets.py --git --warn-only    # như CI
python scripts/scan_secrets.py                      # quét cả file chưa track
```

Hai quy tắc đáng chú ý trong workflow:

- Action được pin theo commit SHA, không phải tag
- `persist-credentials: false` → checkout không giữ credential cho step sau
- Quyền write chỉ cấp cho job scan, không ở top level
- `concurrency` + `cancel-in-progress` → push mới huỷ run cũ
- `timeout-minutes: 15` → không để scanner treo

Scanner **không** tự sửa file. Một rewrite tự động là commit bạn chưa review — nên nó chỉ báo cáo, việc sửa là thủ công.

## 4. Trustabl Agent Scanner

```yaml
permissions:
  contents: read          # mặc định
jobs:
  scan:
    permissions:
      security-events: write
      pull-requests: write
    continue-on-error: true
```

Cùng nguyên tắc với Secret Scan: advisory, action pin theo SHA, không block build.
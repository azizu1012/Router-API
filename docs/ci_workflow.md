# CI Workflows

Hai workflow chạy trên mỗi push lên `main` và mỗi pull request.

## 1. Test job

Chạy `pytest` trên toàn bộ suite được đăng ký trong `pytest.ini`.

```bash
pytest
```

`pytest.ini` dùng `python_files` để chỉ định đúng danh sách test — repo có một `tests/` lớn chứa cả SDK vendor 707 MB và các script thử nghiệm, nên collection mặc định sẽ quét nhầm. Danh sách hiện tại:

| File | Phủ |
|---|---|
| `test_pool.py` | pool concurrency, swap, exhausted |
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

Chạy nhanh một file:

```bash
pytest tests/test_anthropic_protocol.py -v
```

Bỏ qua cây SDK vendor (nếu bạn tự un-ignore nó):

```bash
pytest -q --ignore=tests/gcloud_sdk
```

### `tests/` chỉ track một phần

`.gitignore` re-include đúng 10 file đăng ký trong `pytest.ini`, mọi thứ khác trong `tests/` vẫn local:

```
!tests/
tests/*
!tests/test_pool.py
...
```

Lý do: `tests/` chứa 48 000 file của SDK Google vendor (707 MB) cùng các script quản lý key. Thêm file mới vào suite thì thêm một dòng vào `pytest.ini` **và** một dòng `!tests/...` vào `.gitignore`.

## 2. Trustabl Agent Scanner

Công cụ quét độ tin cậy agent runtime, chạy advisory.

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

Đặc điểm đáng chú ý:
- `continue-on-error: true` → có finding thì vẫn report nhưng **không làm CI đỏ**, không chặn việc khác
- Action được pin theo commit SHA (`trustabl/trustabl-action@973f666…` tương ứng `v0.4.1`), không phải tag
- `persist-credentials: false` → checkout không giữ credential để step sau dùng lại
- Quyền write chỉ cấp cho job scan, không ở top level
- `concurrency` + `cancel-in-progress` → push mới huỷ run cũ
- `timeout-minutes: 15` → không để scanner treo

## Thêm test mới

```bash
# 1. viết test
# 2. đăng ký vào pytest.ini
python_files = … test_my_new_thing.py

# 3. cho phép git track
!tests/test_my_new_thing.py

# 4. chạy
pytest tests/test_my_new_thing.py -v
```

Bỏ qua bước 3 thì test chạy được local nhưng không lên GitHub, và CI sẽ báo "no tests ran" cho file đó.
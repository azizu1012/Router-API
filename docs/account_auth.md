# Account Auth & Multi-Key Tokens

Tài liệu này mô tả lớp auth của Router API: cách tạo token, cách lưu trữ, và bốn lớp giới hạn độc lập áp dụng cho mỗi token.

---

## 1. Hai loại credential

Router API phân biệt rõ hai thứ **không liên quan đến nhau**:

| | Dùng cho | Hình thức | Lưu trữ |
|---|---|---|---|
| **API auth token** | Client gọi REST API | `sk-<name>-<6 ký tự>` | plaintext trong `account_keys` |
| **Web credential** | Đăng nhập dashboard | `name` + `password` | PBKDF2 hash trong `account_credentials` |

Một account có **nhiều API token** (mỗi token một bộ 6 ký tự và hạn mức riêng), nhưng chỉ **một** cặp `name`/`password` cho web.

---

## 2. Định dạng API auth token

### Wire format

```
sk-coder-X7kQ2m
   │     └── 6 ký tự ngẫu nhiên, alphanumeric thuần
   └── tên account
```

**Quy tắc 6 ký tự:**
- Chỉ dùng `A-Za-z0-9` — loại trừ ký tự đặc biệt để token sống sót khi paste qua shell, YAML, URL mà không cần escape
- Phân tách bằng dấu `-` cuối cùng, vì tên account có thể chứa `-` (ví dụ `azure-yena`)
- Chiều dài phải **đúng 6**, không pad — token cắt cụt phải bị từ chối chứ không được khớp

```python
parse_token("sk-azure-yena-abc123")  # → ("azure-yena", "abc123")
parse_token("sk-coder-abc12")        # → None   (thiếu 1 ký tự)
parse_token("sk-coder-ab@123")       # → None   (ký tự đặc biệt)
```

### Vì sao lưu phân giải, không lưu nguyên chuỗi

Bảng `account_keys` lưu **hai cột tách rời**, không lưu chuỗi `"sk-coder-X7kQ2m"`:

| cột | vai trò |
|---|---|
| `name` | **khoá chính** — 1 account sở nhiều token |
| `token_code` | 6 ký tự, unique theo `(name, token_code)` |
| `key_id` | khoá chính thứ cấp, dùng cho update/revoke |

Lý do: truy vấn phổ biến nhất là *"account này có bao nhiêu token?"* và *"token này thuộc ai, quyền bao nhiêu?"*. Lưu nguyên chuỗi sẽ phải `LIKE 'sk-coder-%'` — quét toàn bảng mỗi lần đăng nhập và index không dùng được. Tách cột thì `WHERE name = ?` dùng index, và đếm token là `COUNT(*)`.

Khi auth, chuỗi client gửi được **ghép lại rồi tách ra**:

```
"sk-coder-X7kQ2m" → name="coder", code="X7kQ2m" → tra 2 cột
```

---

## 3. Schema

```sql
CREATE TABLE account_keys (
    key_id              TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL,
    name                TEXT NOT NULL,
    token_code          TEXT NOT NULL,
    enabled             INTEGER DEFAULT 1,
    tier                TEXT DEFAULT 'free',
    rpm                 INTEGER DEFAULT 30,
    tpm                 INTEGER DEFAULT 200000,
    rpd                 INTEGER DEFAULT 1000,
    max_concurrency     INTEGER DEFAULT 6,
    min_interval_seconds REAL    DEFAULT 3.0,
    label               TEXT DEFAULT '',
    created_at          INTEGER,
    updated_at          INTEGER
);
CREATE UNIQUE INDEX idx_account_keys_name_code ON account_keys(name, token_code);

CREATE TABLE account_credentials (
    account_id    TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,      -- PBKDF2-SHA256, 200k vòng
    password_salt TEXT NOT NULL,
    must_change   INTEGER DEFAULT 0,
    updated_at    INTEGER
);

CREATE TABLE invite_codes (
    code       TEXT PRIMARY KEY,      -- 4 chữ số
    created_by TEXT,
    created_at INTEGER,
    expires_at INTEGER,
    used_at    INTEGER,               -- NULL = chưa dùng
    used_by    TEXT
);
```

`token_code` sinh bằng `secrets.choice()`, retry tới 8 lần nếu trùng `(name, token_code)`.

---

## 4. Bốn lớp giới hạn độc lập

Mỗi token mang 4 con số riêng. Chúng **không cộng dồn và không cạnh tranh** — mỗi lớp chặn độc lập.

| Lớp | Ý nghĩa | Trả về khi vượt |
|---|---|---|
| `max_concurrency` | Số request **đang chạy song song** | `token_concurrency_limit` |
| `rpm` | Số request **trong 60 giây** | `token_rpm_exceeded` |
| `tpm` | Số **token** trong 60 giây | `token_tpm_exceeded` |
| `rpd` | Số request **cả ngày** | `token_rpd_exceeded` |

### Concurrency ≠ request rate

Concurrency giới hạn **song song**, không giới hạn tần suất. Throughput thực tế phụ thuộc thời gian mỗi request:

```
max_concurrency = 6

mỗi request mất 1s   → ~6 req/s
mỗi request mất 5s   → ~1.2 req/s
mỗi request mất 30s  → ~0.2 req/s
```

Công thức: `max_concurrency / thời_gian_mỗi_request`.

### Vì sao cần cả RPM lẫn concurrency

Với `max_concurrency=6, rpm=20`: tối đa 6 request chạy cùng lúc, và không quá 20 request mỗi phút. Chỉ riêng concurrency thì một burst 6 request là đủ để đụng trần của provider; chỉ riêng RPM thì 20 request có thể đồng loạt đi và bị chặn dù chưa vượt trần.

### `min_interval_seconds` — giới hạn khoảng cách giữa các lần bắt đầu

Field này **chỉ có tác dụng khi `max_concurrency = 1`**.

```
max_concurrency=1, min_interval=3s:
  t=0.0  req1 bắt đầu
  t=0.0  req2 → chặn (chưa đủ 3s)
  t=3.0  req2 bắt đầu

max_concurrency=6, min_interval=3s:
  t=0.0  req1 bắt đầu
  t=0.1  req2 bắt đầu  ← VÀO ĐƯỢC (bỏ qua interval)
  ...
```

Khi `max_concurrency > 1`, interval **cạnh tranh** với concurrency chứ không cộng thêm — chạy song song thì khoảng cách giữa các lần bắt đầu luôn nhỏ hơn interval. Code bỏ qua nó, và API trả `interval_effective: false` để không ai tưởng rằng field đang hoạt động.

**Muốn giãn 3s mà vẫn chạy 6 song song** → dùng `rpm=20` (20/phút ≈ 1 mỗi 3s), không phải `min_interval_seconds`.

### Trạng thái lưu ở đâu

Toàn bộ bộ đếm nằm **trong RAM** (`TokenRateLimiter`), không ghi SQLite mỗi request. Lý do: chúng mô tả áp lực đang chạy và cửa sổ trượt 60 giây — cả hai đều reset khi restart, nên không có lý do ghi xuống đĩa.

---

## 5. Ai được set gì

| | User tự set | Admin |
|---|---|---|
| `max_concurrency` | ≤ 6 | ≤ 64 |
| `rpm` | ≤ `DEFAULT_ACCOUNT_RPM` | ≤ 100 000 |
| `tpm` | ≤ `DEFAULT_ACCOUNT_TPM` | ≤ 100 000 000 |
| `rpd` | ≤ `DEFAULT_ACCOUNT_RPD` | ≤ 100 000 |
| `min_interval_seconds` | ≥ 3.0 | tự do |

User chỉ có thể **siết chặt hơn**, không nới lỏng hơn mặc định. Admin nâng được tuỳ ý.

---

## 6. Master key — đường ngoại lệ

`accounts.auth_key` giữ lại cho tương thích ngược. Account dùng đường này **không có `token_key_id`**, nên:

- Không qua `account_keys`
- **Miễn trừ toàn bộ gate concurrency/RPM/TPM/RPD**
- Vẫn authenticate API và vẫn vào được dashboard nếu có web credential

Đây là chủ ý: master key là đường vào không bị giới hạn cho chủ hệ thống.

Hiện tại `azuree-admin` giữ master key `azure-dotcom-absolutecinema`, không có structured token và không có web credential — nó thuần túy là key-holder. Account chính là `azure-yena`.

---

## 7. Luồng xác thực

```
Client gửi "Bearer sk-coder-X7kQ2m"
        │
        ▼
  _resolve_auth()          lấy từ Authorization hoặc x-api-key
        │
        ▼
  _check_auth()            so khớp DB (secrets.compare_digest)
        │                  ├─ tìm thấy  → trả account + quota của token
        │                  └─ không    → 401
        │
        ▼
  token_limit_middleware   TokenRateLimiter.acquire()
        │                  ├─ vượt giới hạn → 429 với message nêu rõ lớp nào
        │                  └─ vào được    → gán release callback
        │
        ▼
  route handler            proxy → PoolManager → provider
        │
        ▼
  release()                giảm in_flight, backfill token thật
```

### Fail-closed

`auth.py` **không** có fallback. Trước đây có đoạn:

```python
if not config.AUTH_TOKEN and (token.startswith("sk-ant-") or token.startswith("sk-")):
    active_accounts = account_manager.list_accounts(include_disabled=False)
    if active_accounts:
        return active_accounts[0]     # ← trả account đầu tiên, không cần khớp key
```

Đoạn này cấp hạn mức admin cho **mọi token hình dạng `sk-`**, kể cả rác. Đã xoá. Giờ key không khớp DB thì 401.

---

## 8. Đăng nhập web và enrollment

### Login

`POST /dashboard/login` nhận `username` + `password`. Đường `auth_key` cũ vẫn chạy để client cũ không gãy.

### Mã 4 chữ số

Admin bấm "cấp token" → `POST /dashboard/admin/invites/issue`:

- Sinh mã 4 chữ số
- Hạn **3 phút** (`ttl_seconds`, giới hạn 30–900)
- **Dùng 1 lần**: `consume_invite_db()` chạy `UPDATE ... WHERE used_at IS NULL AND expires_at > ?` và kiểm tra `rowcount == 1`, nên hai request đồng thời không thể dùng cùng một mã
- **Cấp lại sẽ vô hiệu mã cũ** — mỗi admin giữ tối đa một mã đang sống

### Tự đăng ký

`POST /dashboard/register` với `invite_code` + `name`:

1. Kiểm tra mã (hợp lệ, chưa hết hạn, chưa dùng)
2. Tạo account tier `free`
3. Đặt password — mặc định `1234`, đánh dấu `must_change=1`
4. Cấu hình 1 structured token với concurrency 6, interval 3s
5. Trả về token để dùng ngay

Admin cũng có thể tạo thẳng account qua `POST /dashboard/admin/accounts/create`, không cần mã.

---

## 9. Endpoints

### User tự quản lý

| Method | Path | Mô tả |
|---|---|---|
| `GET` | `/dashboard/my/keys` | Liệt kê token (hiện dạng đầy đủ) |
| `POST` | `/dashboard/my/keys/create` | Cấp token mới, tự set giới hạn trong trần |
| `POST` | `/dashboard/my/keys/update` | Sửa giới hạn / khoá token của chính mình |
| `POST` | `/dashboard/my/keys/revoke` | Thu hồi token |
| `POST` | `/dashboard/password` | Đổi password web |

`my/keys/update` tính lại trần ở server thay vì tin giá trị client gửi lên — user gửi `max_concurrency: 60` vẫn bị chặn về 6.

### Admin

| Method | Path | Mô tả |
|---|---|---|
| `POST` | `/dashboard/admin/invites/issue` | Cấp mã 4 chữ số |
| `POST` | `/dashboard/admin/accounts/keys` | Xem token của một account |
| `POST` | `/dashboard/admin/accounts/keys/issue` | Cấp token cho account bất kỳ |
| `POST` | `/dashboard/admin/accounts/keys/update` | Sửa giới hạn / trạng thái |
| `POST` | `/dashboard/admin/accounts/keys/revoke` | Thu hồi token |
| `POST` | `/dashboard/register` | Đăng ký tự do (cần mã) |

---

## 10. Thêm model vào pool

Alias theo quy ước `gemini-flash-<version>` → `gemini-<version>-flash`.

### Flash pool (6 member)

| Alias | Model ID | RPM | TPM | RPD |
|---|---|---|---|---|
| `gemini-flash-38` | `gemini-3.8-flash` | 2 | 250k | 50 |
| `gemini-flash-37` | `gemini-3.7-flash` | **1** | 250k | 15 |
| `gemini-flash-36` | `gemini-3.6-flash` | 2 | 250k | 50 |
| `gemini-flash-35` | `gemini-3.5-flash` | 2 | 250k | 50 |
| `gemini-flash-30` | `gemini-3-flash-preview` | 2 | 250k | 50 |
| `gemini-flash-25` | `gemini-2.5-flash` | 5 | 250k | 20 |

**Vì sao bóp RPM của 3.7:** đo trực tiếp qua `ListModels` + `generateContent` ngày 2026-10-05 cho thấy 3.7 trả 503 *"high demand"* khoảng 2/3 lần gọi, còn 3.6 ổn định 3/3. Đặt RPM=1 để nó hấp thụ lưu lượng nhỏ, tránh làm pool swap liên tục.

**Về 3.8:** `version` trả về `3.0` thay vì chuỗi ngày như 3.5/3.6/3.7 (`3.5-flash-05-2026`). Đây là metadata bất thường, **không** phải bằng chứng model không tồn tại — generate call vẫn thành công ~60%. Đã giữ trong pool.

### Flash Lite pool (3 member)

| Alias | Model ID | RPM | TPM | RPD |
|---|---|---|---|---|
| `gemini-flash-35-lite` | `gemini-3.5-flash-lite` | 2 | 250k | 20 |
| `gemini-flash-lite` | `gemini-3.1-flash-lite` | 3 | 250k | 500 |
| `gemini-flash-25-lite` | `gemini-2.5-flash-lite` | 3 | 250k | 20 |

### Model đã bị Google retire

`gemini-2.0-flash` và `gemini-2.0-flash-lite` không còn trong API. Config không tham chiếu chúng.

Có sẵn alias không ghim version: `gemini-flash-latest`, `gemini-flash-lite-latest`. Nên dùng thay vì hardcode version để không phải sửa config mỗi khi Google ra bản mới.
# Bug Logs

Nơi ghi lại các bug đã fix hoặc log ra bug, cách sửa và lý do tại sao sửa như vậy, để sau này tra cứu khi gặp lại.

---

## Bug #1: Agent Block Không Pass Được Qua Translator & Proxy (2026-07-05)

### Mô tả
Khi Claude Code (agent mode) gửi request chứa `agent_use`/`agent_result` blocks, hệ thống trả về lỗi `"Thiếu agent block support"` hoặc Gemini không nhận diện được function response → retry loop → context bloat.

### Tỉ lệ
Không phải 100% — Gemini API đôi khi lenient về functionResponse name mismatch nên thoáng qua được, chỉ fail "tỉ lệ nho nhỏ" như user report.

### Root cause

Có 6 bugs, chia làm 2 phía:

**INPUT SIDE (message_converter.py):**
1. `agent_use` block dùng `agent_type` (VD: `"general-purpose"`) làm function name thay vì `"Agent"` — Gemini không match được với tool declaration tên `"Agent"`.

**OUTPUT SIDE (5 files):**
2-6. Cả 5 file output đều check `name == "Task"` để sinh `agent_use` block, nhưng Gemini trả về tool name `"Agent"` (không phải `"Task"`). Kết quả: `tool_use {name:"Agent"}` được emit ra SSE như tool_use thường, Claude Code không transform nó thành `agent_use` → lỗi.

### Chain đầy đủ

```
Claude Code gửi:           agent_use {agent_type:"general-purpose", prompt:"..."}
  ↓ message_converter.py (BUG 1)
Gemini nhận:               functionResponse {name:"general-purpose", ...}  ← sai!
  ↓ (nếu Gemini vẫn trả lời)
Proxy emit:                tool_use {name:"Agent"} ← đúng
  ↓ proxy_stream.py (BUG 2-6): check "Task" → sai → không nhận ra là agent
Claude Code nhận:          tool_use {name:"Agent"} ← không được transform
  ↓
Lần request sau gửi:       tool_use → agent_use {agent_type:"general-purpose"} ← name vẫn sai
  ... loop
```

### Fix

#### 1. INPUT: `src/logical_HQ_translator/message_converter.py:435`

**Before:** `t_name = block.get("name") or block.get("agent_type") or "Task"`

**After:** `t_name = block.get("name") or ("Agent" if b_type == "agent_use" else "Task")`

**Vì sao:** `agent_type` là metadata nội bộ của Claude Code (VD: `"general-purpose"`), không phải tên tool. Tool declaration Claude Code gửi xuống có tên `"Agent"`. Dùng `"Agent"` làm function name để Gemini match với tool declaration.

#### 2-6. OUTPUT: 5 files check `== "Task"` → `in ("Agent", "Task")`

| File | Dòng |
|------|------|
| `src/api/claude_proxy/handler/proxy_stream.py` | 565 |
| `src/api/claude_proxy/handler/proxy_nonstream.py` | 281 |
| `src/api/claude_proxy/handler/stream_executor.py` | 248 |
| `src/api/claude_proxy/handler/nonstream_executor.py` | 408 |
| `src/api/claude_proxy/stream.py` | 294, 352, 363 |

**Vì sao:** Gemini trả về tool `name: "Agent"` (theo tool declaration trong system prompt). Code cũ chỉ check `"Task"` — di sản từ Claude Code cũ. Giữ `"Task"` làm fallback cho backward compatibility với setup cũ, thêm `"Agent"` cho setup mới.

### Tại sao không fix OpenCode proxy

OpenCode proxy (`opencode_proxy/handler/`) dùng OpenAI-compatible format, không có `agent_use`/`agent_result` blocks. Bug này chỉ ảnh hưởng Claude Code agent mode.

### Tại sao không over-engineer

- Hardcode `"Agent"` thay vì scan tools array — ít code, đủ dùng, tool declaration không đổi giữa các request.
- Không refactor message_converter — chỉ sửa 1 dòng.
- Giữ nguyên fallback `"Task"` — zero risk cho non-agent requests.

### Files changed

```
src/logical_HQ_translator/message_converter.py          | 2 +-
src/api/claude_proxy/handler/proxy_stream.py             | 2 +-
src/api/claude_proxy/handler/proxy_nonstream.py          | 2 +-
src/api/claude_proxy/handler/stream_executor.py          | 2 +-
src/api/claude_proxy/handler/nonstream_executor.py       | 2 +-
src/api/claude_proxy/stream.py                           | 6 +++---
```

---

## Bug #2: Máy Mới Không Khởi Động Được Vì DB Rỗng (2026-10-06)

### Mô tả
Deploy trên một máy chưa từng chạy Router API: app chết ngay lúc khởi động với `sqlite3.OperationalError: no such table: key_status`.

### Root cause
`src/core/router/__init__.py` tạo `APIRouter()` ở **module level**, và constructor đó gọi `get_key_status_db()`. `custom_endpoint_manager` cũng dựng cache ở module level. Cả hai đọc DB **trong lúc import** — trước mọi startup hook của FastAPI. Trên máy có `usage.db` sẵn thì không sao vì bảng đã tồn tại; máy mới thì không có gì để đọc.

Không phải mọi entrypoint đều qua `main.py`, nên sửa ở `main.py` là chưa đủ: bất kỳ `import app` nào cũng phải crash.

### Fix
`src/backend/_db.py` — `conn()` tạo hai bảng đọc lúc import ngay lần mở connection đầu tiên. `main.py` gọi `init_config_tables()` trước khi bind port để lỗi schema nổi ra lúc khởi động chứ không phải trên request đầu tiên.

### Ranh giới dễ vỡ
`CREATE TABLE IF NOT EXISTS` **không** sửa được bảng đã có sai shape. Nên nếu DDL ở `_db.py` lệch với `schema.py`, bản bootstrap chạy trước và thắng; `CREATE` của `schema.py` thành no-op; bất kỳ cột nào không có `ALTER` migration sẽ không bao giờ được tạo. Lần đầu viết sai cột cho `custom_endpoints` và `key_usage` đã đúng vào loại lỗi này. `tests/test_schema_bootstrap.py` so DDL của hai bên để chặn.

---

## Bug #3: Mã Đăng Ký Va Chạm Thành Lỗi 500 (2026-10-06)

### Mô tả
Nút "Cấp mã" của admin thỉnh thoảng trả 500: `sqlite3.IntegrityError: UNIQUE constraint failed: invite_codes.code`.

### Root cause
Mã chỉ 4 chữ số → 10 000 giá trị. `create_invite_db()` xoá mã chưa dùng rồi sinh mã ngẫu nhiên — nhưng mã **đã dùng** được giữ lại làm audit trail. Khi bảng có đủ mã cũ, xác suất trùng là 1/10 000 mỗi lần cấp, và sẽ tăng dần theo thời gian.

Bug này không xuất hiện trên database mới nên không thấy cho tới khi săn flake — nó hiện ra thành một lần build đỏ ngẫu nhiên trong `test_account_auth.py`.

### Fix
Thử tối đa 8 lần trước khi báo lỗi, và lỗi cuối cùng nói rõ nguyên nhân thay vì để `IntegrityError` lọt lên làm 500.

### Test
`tests/test_invite_codes.py` — ép va chạm bằng `patch("secrets.choice")`, kiểm tra retry hoạt động và giới hạn 8 lần.

---

## Bug #4: Dashboard Nuốt Mất Lý Do Lỗi (2026-10-06)

### Mô tả
Mọi lỗi từ endpoint admin hiện thành `HTTP error! status: 404` thay vì thông báo thật.

### Root cause
`frontend-src/src/utils/api.js` đọc `errorData.error`. Nhưng các route admin raise `HTTPException(detail="Account not found")` — FastAPI đặt chuỗi vào `detail`, không phải `error`. Nên nguyên nhân thật bị vứt đi.

### Fix
Đọc cả `error` lẫn `detail`, và parse `detail` dạng JSON string mà FastAPI dùng khi detail là object.

---

## Bug #5: Effort Bị Đảo Ngược Hoặc Bỏ Qua (2026-10-06)

Chi tiết ở [`routing_and_resilience.md`](routing_and_resilience.md) mục 9 và 10. Tóm tắt: `output_config.effort` — field Claude Code thật sự gửi — không được đọc, nên mọi effort đều rơi về `low`; `reasoning_effort: medium` lại tắt thinking; `thinking: disabled` bị `return {}` nuốt mất.

Điểm chung của bug #2–#5: **không cái nào sinh ra lỗi để báo cáo**. Response vẫn hợp lệ, request vẫn 200. Chỉ có hành vi sai. Vì vậy chúng không tự lộ ra ngoài đời thực — cần test so sánh đầu/cuối mới thấy được.

---

## Bug #6: Tầng Đáy Import Ngược Lên Tầng Trên (2026-10-06)

### Muc do
Không sinh ra lỗi runtime nao — day la ma **van` hoa`. Cac ham deu chay dung, chi la sai cho.

### Root cause
Ba ham thuoc tinh cua mot tang sai, nam o cho co the import nguoc len tren:

| Ham | Nam o | Ai can no | Sai o gi |
|-------|-------|-----------|---------|
| `_sanitize_schema_for_gemini` + 6 helper | `logical_HQ_translator/message_converter.py` | `providers/gemini_format.py` | Tầng dưới import tầng trên |
| `_GLOBAL_TOOL_NAME_CACHE` | cung tren | ca hai deu dung | Khong thuoc ve tang nao |
| `is_sub_agent_body` | `logical_HQ_translator/sse_cache_agent.py` | `server/openai_server/auth.py` | Auth middleware import tầng proxy |

`_sanitize_schema_for_gemini` la vi du rõ nhat: ham` chi ton tai de thoa man
google-genai (khong ho tro `const`, khong ho tro union `type`, khong ho tro
`allOf`). Nó nam trong `message_converter` roi `providers` phai import nguoc len de
lay.

`auth.py` ngoi ra con **import trong ham** — dung hinh dang ma mot vong long se co.
Khong co vong long that o day, nhung import trong ham khong con ly do de ton tai.

### Fix
Ba ham chuyen ve nhung noi trung tinh ma ca hai dau deu dung:

| File moi | Noi cu | Chu o ai dung |
|----------|--------|---------------|
| `src/core/providers/gemini/schema_sanitizer.py` | canh google-genai | `message_converter`, `gemini_format` |
| `src/core/tool_name_cache.py` | state dung chung (cross-request) | `message_converter`, `gemini_format` |
| `src/core/sub_agent_detect.py` | ham thuan tren raw body | `auth`, 2 proxy, `sse_cache_agent` |
| `src/core/sse_format.py` | 1 dong f-string | `auth`, `proxy_stream`, `sse_cache_agent` |

`message_converter` gio import `schema_sanitizer` — **dung chieu** (translator
nam tren providers). `sse_cache_agent` re-export de khong phai sua 4 proxy
con lai, nhung import goc da troi thang ve `core/`.

### Ket qua
- Khong con `src/core/providers/` nao import `logical_HQ_translator`.
- `auth.py` khong con import `src/api/`.
- Pyflakes sach tren tat ca file da sua.

### Vay co tai sao lai sao?
Ba ham deu **thuan** — khong doc config, khong giua state rieng, khong I/O. Tinh
`khong` nen chuyen file khong doi gi. Ham co side effect thi phan bo lai se la
thay doi hanh vi, va do la luc can test truoc.

### Con lai gi
`auth.py` van lazy-import 2 ham tu `src/api/`: `get_system_status_summary` va
`get_client_model_name`. Khong phai vong long (proxy khong import auth), va
`get_client_model_name` la **ham identity co chu dich** — OpenCode doi chieu ten
model voi `opencode.json`, nen tra ve nguyen ban la mot quyet dinh, khong phai
code thua. Giai quyet ton tai bang cach giai quyet o 15 call site de lan ban
quyet dinh do ra nhieu cho, te hon giu no.
---

## Bug #7: Custom Endpoint Không Bị Giới Hạn RPM Khi Streaming (2026-10-06)

### Muc do
Nghiêm trọng theo thiết hậu quả, im lặng theo biểu hiện. Không có log, không có
trace, không co response nao sai.

### Root cause
`check_custom_pool_rate()` — sliding window 10 req/phút cho **từng model** của
custom endpoint — nam trong `_resolve_and_call()`:

```python
if is_custom:
    if not await check_custom_pool_rate(model_id_val):
        raise RuntimeError("custom_endpoint_rate_limited")
else:
    has_quota = await router.acquire_quota(...)
```

`call_nonstream()` goi `_resolve_and_call()`, nen duong non-stream duoc bao ve.

`call_stream()` **khong** goi `_resolve_and_call()`. No phai do qua bet — ma vi
key phai duoc giu suot thoi gian client keo chunk:

```python
# _resolve_and_call, dong 633-636
finally:
    if api_key_val:
        router.release_key(api_key_val)   # tha key ngay khi ham return
```

Dung helper thi key bi tha truoc khi chunk dau tien toi client. Nen stream phai
tu viet lai phan setup, va ban viet lai do **khong kem** nhanh `is_custom`.

### Hậu quả
Mot model custom bi chan 10 RPM tren duong `/v1/messages`, nhung **khong chan
gi** tren:

- `call_stream()` — pool mode
- `call_stream()` — standalone mode (thieu ca `is_custom` luon)

Gemini keys khong bi anh huong — `check_custom_pool_rate` chi danh cho custom
endpoint. Nen loi chi mo khi nao co nguoi cau hinh custom endpoint lau dau tien.

### Fix
Bo sung nhanh `is_custom` vao ca hai duong stream, giong het `_resolve_and_call`.

### Test
`tests/test_custom_pool_rate_limit.py` — 9 test, parametrize ca 3 duong
(non-stream / pool stream / standalone stream), pin ca "bi chan khi qua han" va
"khong chan khi duoi han" de khong over-correct. Mutation: revert tung fix rieng,
test do do tu chay.

---

## Bug #8: `per_model` dạng JSON sai hình dạng làm sập lúc đọc key (2026-10-06)

### Muc do
Không sinh ra lỗi cho tới khi cột `per_model` chứa JSON không phải object —
ví dụ một list. Khi đó toàn bộ doc key status ném `AttributeError`, và vì nó
xảy ra trong lúc khởi tạo nên app không lên được.

### Root cause
`except Exception` chỉ bọc `json.loads()`, không bọc dòng dùng kết quả:

```python
try:
    pm_dict = json.loads(d["per_model"])
except Exception:
    pm_dict = {}

for mid, pm_entry in pm_dict.items():   # <-- AttributeError, khong ai bat
```

`json.loads("[1,2,3]")` tra ve list, het. `except` o tren khong dong vao vi no
khong nem loi — loi nam o dong sau.

### Fix
Thu hep `except` thanh `(ValueError, TypeError)` va them guard hinh dang:

```python
except (ValueError, TypeError):
    pm_dict = {}
if not isinstance(pm_dict, dict):
    pm_dict = {}
```

Co **hai** handler loai nay trong cung mot ham (parse o dau vong lap, merge o
duoi), ca hai deu bi thu hep.

### Vì sao `except Exception` o day la sai
Nó tao cam giang an toan ma that ra la che loi. Mot `AttributeError` trong vong
lap — tuc la bug lap trinh — se **biot y nhieu** voi mot cot JSON hong. Test
`test_no_catch_all_handler_remains_in_the_row_loop` quet AST de chan handler
catch-all moi them vao.

### Test
`tests/test_error_visibility.py` — 12 test, gom ca 2 fix nay va log o
`router.list_models` (xem Bug #9).
---

## Bug #9: Dashboard Nuốt Mất Lý Do Lỗi (2026-10-06)

### Muc do
Khong sinh ra response sai — sinh ra **khong co** response nao de chan doan.

### Root cause
`router.list_models()` nap danh sach model cua custom endpoint trong mot
`except Exception: pass`:

```python
for mid in (ep.get("enabled_models") or ep.get("models") or []):
    if not any(m["id"] == mid for m in models):
        models.append({...})
except Exception:
    pass
```

Neu `list_endpoints()` loi — bang bi khoa, DB loi bat thuong — **toan bo model
custom bien mat khoi `/v1/models`**, khong mot dong log nao. Client goi toi
model do nhan `unknown-model error` tro ve, tro sai hoan toan.

### Fix
Ghi log thay vi `pass`. Khong thu hep exception: `list_models()` la code duong
bien, mat mot model hon la dung hon la lam mat tat ca.

### Test
`tests/test_error_visibility.py` — pin co log khi endpoint list that, khong log
khi khoe, va phan Gemini van tra ve duoc khi endpoint that. Mutation: revert
ve `pass` thi test do.

---

## Bug #10: Master Key Va Chạm ~1.1% Bị Parse Nhầm Thành Structured Token (2026-10-06)

### Mô tả
Khi chạy test suite lớn (`test_master_key.py`), có ~1.1% xác suất test fail ngẫu nhiên: master key bị nhận nhầm thành structured token.

### Root cause
Master key có format `sk-<43 ký tự urlsafe>`. Do base64url bao gồm cả ký tự `-`, nếu một master key ngẫu nhiên có dấu `-` rơi trúng vị trí cách đuôi 6 ký tự (ví dụ `sk-Vm3BpOw...-ab12cd`), hàm `parse_token()` cũ thực hiện `rsplit("-", 1)` và khớp được `token_code` 6 ký tự. Khi đó master key bị nhận diện nhầm thành structured token của một account rác, dẫn đến tra cứu DB thất bại hoặc bị kẹp rate limit sai.

### Fix
Trong `src/backend/account_keys.py`:
1. Master key chuẩn có độ dài đúng 46 ký tự (`sk-` + 43 ký tự urlsafe). Thêm guard kiểm tra độ dài và tiền tố để loại trừ ngay lập tức trước khi parse.
2. Kiểm tra tính hợp lệ của token code và name: phải là alphanumeric thuần túy, không chứa ký tự đặc biệt hoặc cấu trúc master key.

### Test
`tests/test_master_key.py` — sinh 5000 master key ngẫu nhiên liên tiếp để chứng minh 0% va chạm.

---

## Bug #11: Hệ Thống Log Stream & Web Dashboard Log Không Hoạt Động (2026-10-06)

### Mô tả
Giao diện Log Stream trên WebUI không xem được log, terminal trắng trơn, không nhận websocket message và không tải được log history.

### Root cause
1. `LogWatcher.get_history()` tra cứu key `proxy`, trong khi bộ nhớ đệm lưu key dạng `log:proxy`, dẫn đến kết quả tra cứu rỗng 100%.
2. `LogWatcher.watch_file()` khi khởi động seek thẳng đến EOF (`f.seek(0, 2)`) mà không đọc tail trước đó, dẫn đến buffer trong RAM ban đầu luôn rỗng.
3. `LogTerminal.jsx` gọi `/dashboard/logs/history?file=proxy.log`, nhưng backend route chỉ chấp nhận param `channel`.
4. Frontend gọi `data.lines.forEach(...)`, nhưng backend lại trả về `lines: len(history)` (kiểu số nguyên đếm dòng), khiến client văng TypeError crash app.
5. Frontend fetch log thiếu header `X-Dashboard-Token`, bị backend chặn 401 Unauthorized.
6. Thiếu cơ chế phân quyền RBAC: user thường có thể xem lén log nhạy cảm chứa key API và cấu hình hệ thống.

### Fix
1. Thêm hàm `normalize_channel()` xử lý mọi biến thể (`proxy`, `log:proxy`, `proxy.log`, `keys:endpoint`).
2. Bổ sung `_read_tail()` nạp trước 1000 dòng từ đĩa vào RAM buffer ngay lúc server khởi động; đồng thời cung cấp fallback đọc disk tail khi buffer RAM chưa đủ.
3. Sửa `/dashboard/logs/history` nhận cả `channel` lẫn `file`, trả về đồng thời `history: [...]` và `lines: [...]`.
4. Gửi đầy đủ `X-Dashboard-Token` và `Authorization: Bearer` từ frontend.
5. Phân quyền RBAC nghiêm ngặt: Kênh `proxy` và `api` cho mọi tier; kênh `system`, `keys`, `web` chỉ dành cho Admin (trả về 403 Forbidden trên HTTP và từ chối subscription trên WebSocket). Dropdown trên frontend tự động ẩn các kênh này đối với user thường.

### Test
`tests/test_log_permissions.py` (9 tests pass 100%), AST layering test `tests/test_layering.py`.

---

## Bug #12: Tab My Account Bị Crash Khi Mount & Bố Cục Accounts Quá Tải (2026-10-06)

### Mô tả
1. Người dùng truy cập trang My Account (`/stats/my-account`) bị trắng trang, không mở lên được hoặc tưởng bị trùng với trang quản trị accounts.
2. Giao diện quản lý tài khoản admin (`/stats/accounts`) cũ là bảng 10 cột co cụm `table-fixed`, cột thao tác chỉ rộng 8% nhưng nhồi nhét tới 7 nút icon không nhãn; danh sách token bật ở tận đáy trang xa tầm mắt; các thông báo token/mật khẩu dùng `alert()` dễ bị mất.

### Root cause
1. **My Account Crash (`ReferenceError`)**: Trong commit `1b0a62a` (gỡ bỏ dropdown search-engine thừa), việc xoá khối state `wsLoading` đã vô tình xoá mất dòng khai báo `const [resetCountdown, setResetCountdown] = useState('');`. Trong khi đó, `useEffect` vẫn gọi `setResetCountdown(...)` và thẻ hiển thị hạn mức RPD vẫn đọc `resetCountdown`. Khi mount component, trình duyệt văng lỗi `ReferenceError: resetCountdown is not defined`, làm React crash toàn bộ cây component của tab My Account.
2. **Lỗi Đồng Bộ URL & Quyền Truy Cập Tab**:
   - `AppContext.jsx` thiếu mapping cho tab `/stats/help` (`ApiHelpTab`), khiến việc chuyển tab hoặc reload trang bị rơi về `/stats`.
   - Tab Models (`md`) bị sót khỏi danh sách `isAdminTab`, dẫn đến user thường có thể vào được bằng URL trực tiếp.
   - Quá trình silent poll 1.5s của My Account gán lại `setUser({ name, tier })` thiếu trường `must_change_password`, làm mất banner cảnh báo đổi mật khẩu sau 1.5s.
3. **Bố cục AccountsTab cũ không tối ưu**: Bảng dữ liệu quá nhiều cột ngang, không phân cấp thông tin; token table hiển thị tách rời dưới đáy bảng; thiếu cơ chế copy master key an toàn.

### Fix
1. **Khôi phục State trong [MyAccountTab.jsx](file:///d:/AI_Projects/router_api/frontend-src/src/tabs/MyAccountTab.jsx)**: Khai báo lại `const [resetCountdown, setResetCountdown] = useState('');` độc lập, ngăn ngừa lỗi ReferenceError khi mount.
2. **Chuẩn hóa Routing trong [AppContext.jsx](file:///d:/AI_Projects/router_api/frontend-src/src/context/AppContext.jsx)**:
   - Bổ sung URL mapping 2 chiều cho `/stats/help`.
   - Bổ sung tab `md` vào danh sách `isAdminTab` cả lúc validate URL lẫn lúc login.
   - Đồng bộ đầy đủ `must_change_password` trong chu kỳ poll profile.
3. **Refactor Bố cục [AccountsTab.jsx](file:///d:/AI_Projects/router_api/frontend-src/src/tabs/AccountsTab.jsx) theo mô hình Master–Detail**:
   - Tách component chi tiết độc lập [AccountDetailPanel.jsx](file:///d:/AI_Projects/router_api/frontend-src/src/components/AccountDetailPanel.jsx).
   - Cột trái (Master): Danh sách tài khoản có avatar, tier badge, trạng thái kết nối, RPM/TPM/RPD vắn tắt, thanh tìm kiếm thông minh, bộ lọc Tier/Status và nút sắp xếp theo trường.
   - Cột phải (Detail): Chi tiết tài khoản được chọn, chia thành 4 phân vùng rõ rệt:
     + Thẻ định danh & Nút bật/tắt (Enable/Disable), Sửa hạn mức.
     + Hạn mức tài khoản (RPM / TPM / RPD).
     + Quản lý Auth Tokens riêng của tài khoản đó (Tạo, Sửa, Thu hồi).
     + Bảo mật & Đăng nhập (Mật khẩu web với 2-step reveal, cờ yêu cầu đổi MK, copy & cấp mới Master Key).
     + Vùng nguy hiểm (Xóa tài khoản vĩnh viễn với xác nhận).
   - Banner hiển thị Secret độc lập, không tự biến mất để tránh mất token/key.

### Test
Build Vite bundle thành công (`npm run build`), kiểm tra `no-undef` bằng ESLint, toàn bộ 579 tests pytest pass 100%.

---

## Bug #13: Giá Ảo Lệch 10–15x Trong DB, Thiếu Model Flash 3.6–3.8 & Rào Cản Hardcode Model Pool (2026-10-07)

### Mô tả
1. Giá token trong database (`model_prices`) bị cấu hình giá ảo cao gấp 10-15 lần thực tế (ví dụ `gemini-2.5-flash` và các flash models bị tính $0.0015/1k input và $0.009/1k output thay vì $0.00015 và $0.0006).
2. Hệ thống chỉ hỗ trợ 3 models Flash và 2 models Lite, thiếu hụt các thế hệ Flash mới (`gemini-flash-38`, `37`, `36`) và Flash Lite (`gemini-flash-35-lite`).
3. Logic phân bổ dung lượng token (`src/core/limits/capacity.py`) hardcode danh sách tên model (`gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-flash-35`), dẫn đến các model mới không nhận diện được pool và bị gán nhầm quota mặc định 15 RPM / 32k TPM / 1k RPD.
4. Cấu hình `.env` và database SQLite `model_config` hoạt động tách biệt: thay đổi `.env` không đồng bộ lên DB, và admin sửa model trên Dashboard không ghi ngược lại `.env`.

### Root cause
1. **Giá ảo trong seed data**: Bảng `model_prices` lúc ban đầu được khởi tạo với bảng giá nháp/ước lượng quá cao, không khớp với bảng giá chính thức từ Google Gemini API.
2. **Thiếu hỗ trợ alias và model mới trong Router**: `src/core/router/core/router.py` và `src/core/api_config.py` chưa khai báo alias cho các model `gemini-flash-36`, `37`, `38` và `gemini-flash-35-lite`.
3. **Hardcode Capacity Limiter**: Trong `capacity.py`, hàm phân loại pool dựa vào chuỗi tĩnh thay vì đọc cấu hình pool từ config hoặc router, khiến các model mới không được hưởng hạn mức cao của pool ảo.
4. **Thiếu cơ chế Two-Way Sync**: `src/backend/model_config.py` chỉ đọc/ghi SQLite, không có hook đồng bộ với `.env`, trong khi `src/core/api_config.py` đọc `.env` lúc khởi động mà không reload động khi file `.env` bị sửa.

### Fix
1. **Đồng bộ bảng giá thực tế Google Gemini API**:
   - Cập nhật `DEFAULT_MODEL_PRICES` trong [src/backend/model_prices.py](file:///d:/AI_Projects/router_api/src/backend/model_prices.py) chuẩn xác theo biểu giá Google:
     - 6 Flash models: $0.00015 / 1k input ($0.0003 >128k), $0.0006 / 1k output ($0.0012 >128k).
     - 3 Lite models: $0.000075 / 1k input ($0.00015 >128k), $0.0003 / 1k output ($0.0006 >128k).
     - Pro models: $0.00125 / 1k input ($0.0025 >128k), $0.005 / 1k output ($0.010 >128k).
   - Thêm auto-migration trong `init_db()` ([src/backend/schema.py](file:///d:/AI_Projects/router_api/src/backend/schema.py)) tự động sửa giá cũ nếu phát hiện giá ảo > 0.001 cho flash.
2. **Mở rộng 6 Flash, 3 Lite và 2 Virtual Pools**:
   - Thêm đầy đủ 6 Flash models (`gemini-flash-38` đến `25`), 3 Lite models (`gemini-flash-35-lite`, `lite`, `25-lite`) vào `.env`, `src/core/api_config.py`, và router mappings.
   - 2 Virtual Pools: `gemini-flash` (gộp 6 models flash) và `gemini-flash-lite` (gộp 3 models lite) với cơ chế fallback tự động.
3. **Dynamic Pool Resolution Trong Capacity Limiter**:
   - Xóa bỏ toàn bộ hardcode tên model trong [src/core/limits/capacity.py](file:///d:/AI_Projects/router_api/src/core/limits/capacity.py).
   - Bổ sung hàm `_resolve_pool_model_ids(pool_name)` tự động phân giải danh sách model trực tiếp từ `api_config.py` hoặc alias registry.
4. **Cơ chế Two-Way Synchronization (.env ↔ DB)**:
   - Triển khai `sync_env_to_db()` và `sync_db_to_env()` trong [src/backend/model_config.py](file:///d:/AI_Projects/router_api/src/backend/model_config.py).
   - Khi server khởi động: đồng bộ các model từ `.env` vào DB nếu chưa có.
   - Khi Admin cập nhật model qua Dashboard (`PUT /dashboard/admin/models/{id}`): cập nhật vào DB đồng thời ghi lại biến môi trường tương ứng trong `.env` (`update_env_var`).
   - Bổ sung `_watch_env_file()` tự động theo dõi file `.env` theo chu kỳ để hot-reload vào RAM cache mà không cần restart server.

### Test
Toàn bộ unit test & integration test pass 100%:
- `test_layering.py` (AST layering không vi phạm phân tầng).
- `test_master_key.py`, `test_dashboard_tokens.py`, `test_secret_scanner.py`.
- Đồng bộ dữ liệu giá thực tế và model pools kiểm tra thành công trên SQLite và `.env`.

---

## Bug #14: Cột `enabled` Tồn Tại Nhưng Không Tắt Được Model Nào (2026-10-07)

### Muc do
Nghiêm trọng theo hậu quả, im lặng theo biểu hiện. Không có log, không có trace, response van` dung.

### Mô tả
Google bắt buộc paid tier cho Flash 3.7 / 3.8, còn key free chi tới 3.6. Pool `gemini-flash` van xoay vong qua 3.7/3.8, nen moi request dinh `permission_denied` roi doi model lien tuc — dung triệu chứng nguoi dùng bao loi.

Admin muon tat 3.7/3.8 tren dashboard cho toi khi Google mo cho tat ca. Cot `model_config.enabled` **đã có sẵn** từ lâu. Tắt nó không làm gì.

### Root cause
Ba tầng độc lập nhau, cả ba đều sai theo cach riêng:

**1. `merge_db_models` bo qua han row disabled**

```python
for alias, db_cfg in db_models.items():
    if not db_cfg.get("enabled", True):
        continue          # <-- co la doc, nhung bo ca row
```

`continue` nghia la "khong ap dung limit cua row nay" chu khong phai "model nay dang tat". Model van nam trong `MODEL_POOLS[...]["members"]` voi han muc lay tu env. Khong co co `enabled` gi de tang routing doc.

**2. `ModelPool.get_or_create` cache theo ten pool**

```python
if pool_name not in cls._instances:
    cls._instances[pool_name] = cls(pool_config, ...)
return cls._instances[pool_name]
```

Pool object dau tien duoc tao — voi ca 6 member — song lai ton tai het vong doi process, giu `_locks` cua member da bi tat. Bat `enabled=0` vao DB thay doi mot thu ma routing khong bao gio doc.

**3. Khong cho client thay model da tat**

`list_models()` chi bo qua `hidden`. Client van thay 3.7 trong `/v1/models`, chon no, roi router tu choi.

### Fix

| File | Thay doi |
|---|---|
| `src/core/api_config.py` | `is_model_enabled()` + `active_pool_members()`; doc `enabled` vao `AVAILABLE_MODELS` thay vi bo qua row; `_recompute_pool_aggregates` bo qua member da tat; `reload_model_config()` goi `ModelPool.reset_instances()` |
| `src/core/router/pool.py` | `ModelPool.reset_instances()` |
| `src/core/router/core/router.py` | `resolve_pool` + `list_models` doc qua `active_pool_members()` / `enabled` |
| `src/core/router/core/key_resolver.py` | `reserve_key` doc qua `active_pool_members()`; chan request truc tiep vao model da tat |
| `src/core/providers/gemini/utils.py` | `all_models_excluded` doc cung danh sach da loc |
| `src/core/limits/account_limiter/capacity.py` | `_resolve_pool_model_ids` bo qua member da tat |
| `src/server/.../admin/models.py` | Route `POST /dashboard/admin/pools/toggle-member`, expose `enabled` + `active_members` |
| `frontend-src/src/tabs/ModelsTab.jsx` | Panel "Thanh vien Pool" voi toggle |

### Vì sao `all_models_excluded` phải đọc cùng một danh sách đã lọc
Member da tat **khong bao gio nhan loi goi**, nen khong bao gio tich luý failure, nen khong bao gio bi exclude. Dem no vao la `all([])` semantics sai: ham tra `False` vinh vien, va vong retry cu dao mai mot pool da can thay vi doi ra.

### Vì sao chặn member cuối
Tat het member cua mot pool la nghen co: `ModelPool.acquire()` het member, moi request vao pool alias do se `TimeoutError` sau 120s. Route tra 400 ro rang thay vi de admin tu phat hien luc production chet.

### Bất biến đã pin trong test
- Member da tat khong bao gio duoc cap phat tu `ModelPool.acquire()`.
- Aggregate cua pool khong tinh budget cua member da tat — con lai thi pool quang bao RPM no khong the dat toi, va moi model them vao lam lo chenh lech lon hon chu khong nho hon.
- `AVAILABLE_MODELS[alias]` mac dinh `enabled=True` khi alias khong ton tai. Doc thieu config la "off" se kien mot typo trong ten member lam pool tu rong.
- `MODEL_POOLS[...]["members"]` khong bi mutate: day la thanh vien khai bao, bo loc chi la view luc doc, nen reload sau bi gia lai duoc.

### Test
`tests/test_pool_member_toggle.py` — 28 test. Chay tren ban goc: **24 fail** (feature chua ton tai). Bam lai tung fix rieng de xac nhan test do tu chay.

---

## Bug #15: `/v1/responses` Streaming Không In Ra Gì, Anthropic SDK Mất Web Search (2026-10-07)

### Muc do
Nghiêm trọng theo biểu hiện. Cả hai đều trả HTTP 200 với body **hợp lệ rỗng** — client nhận được phản hồi thành công rồi không có gì để hiển thị. Không log, không trace, không status code sai.

### Bug A — `/v1/responses` stream trả sai dialect

**Mô tả**: Client dùng Responses API với `stream: true` không in ra chữ nào.

**Root cause**: `responses()` stream thẳng chat chunk của proxy ra, không đổi frame:

```python
async def _gen():
    async for chunk in opencode_proxy.stream_chat_completion(...):
        yield chunk          # <-- data: {"choices":[{"delta":...}]}
```

Responses client parse **typed event** — `response.created`, `response.output_text.delta`, `response.completed` — và bỏ qua payload nó không nhận ra. Nên stream mở ra, có dữ liệu bay, rồi đóng lại, không có event nào để client in. Model chưa từng trả sai; chỉ là không có event nào đúng hình dạng.

Non-stream thì **đúng** (`_extract_response_text` + dựng `output[]`), nên bug chỉ xuất hiện khi bật stream — và test chỉ thử non-stream nên không thấy.

**Fix**: thêm `_responses_sse_stream()` — adapter dịch chat delta → Responses event, kèm đủ vòng đời `created → in_progress → output_item.added → output_text.delta → output_item.done → completed` + `[DONE]`.

### Bug B — Anthropic SDK web search bị drop

**Mô tả**: Client chat dùng Anthropic SDK (không phải Claude Code) gửi `{"type": "web_search_20250305"}` → không tìm kiếm, model trả lời từ trí nhớ.

**Root cause**: `_convert_messages` chỉ đọc `tool["name"]`:

```python
tool_name = str(tool.get("name", "")).strip()
if not tool_name or tool_name in UNSUPPORTED_OR_HEAVY_TOOLS:
    continue          # <-- tool typed khong co name -> bo qua im lang
```

Anthropic server tool được định danh bằng `type`, **không phải** `name` — schema nằm trong model chứ không nằm trong request. SDK version nào không gửi `name` thì tool bị bỏ trong im lặng. Request vẫn 200, vẫn có câu trả lời, chỉ là không có bước search nào chạy.

**Fix**: `SERVER_SEARCH_TOOL_TYPES` gom các biến thể đã biết; khi `type` khớp thì suy ra `name = "web_search"`. Giữ nguyên guard "không có name lẫn type thì vẫn bỏ" — fix không được biến thành "giữ tất cả".

### Vì sao không phát hiện được bằng log
Cả hai đều là **silent success**. Server làm đúng việc nó nghĩ nó được yêu cầu: stream đúng 200, tool đúng không phải function tool. Chỉ có client mới biết mình không nhận được gì — nên phải test ở tầng frame, không phải assert HTTP 200.

### Test
`tests/test_responses_and_search_dialects.py` — 24 test. Chạy trên bản gốc: **collection error** (`_responses_sse_stream` chưa tồn tại). Có guard chống fix quá tay: tool không có cả `name` lẫn `type` vẫn phải bị bỏ.

---

## Bug #16: `/v1/messages` Streaming Chết Vì Keepalive Tự Bắn Vào Chính Mình (2026-10-07)

### Muc do
Nghiêm trọng nhất trong ba bug này. **Mọi request streaming của Anthropic đều chết** nếu model cần hơn 1 giây để trả chunk đầu tiên.

### Mô tả
Test thực tế:
```
event: error
data: {"type": "error", "error": {"type": "api_error",
        "message": "anext(): asynchronous generator is already running"}}
event: message_stop
data: {"type": "message_stop"}
```

HTTP **200**, SSE hợp lệ, có `message_stop` đóng stream đàng hoàng — nhưng không có `content_block_delta` nào. Client thấy một response thành công rồi không có chữ nào.

### Root cause
Keepalive wrapper trong `proxy_stream.py`:

```python
item = await asyncio.wait_for(asyncio.shield(aiter_.__anext__()), timeout=1.0)
except asyncio.TimeoutError:
    if ...:
        yield _KEEPALIVE
    continue          # <-- tao __anext__ MOI tren generator dang chay
```

`asyncio.shield` tồn tại **chính để** pull đang chạy sống sót qua timeout. Nên `continue` là cái sai tuyệt đối nhất: nó tạo một `__anext__` thứ hai trên generator chưa xong, và asyncio ném `RuntimeError`.

Vòng lặp ping được viết để giữ kết nối sống — nhưng nó giết chính stream cần giữ. Một lượt thinking của Gemini mất hơn 1 giây, tức là **đúng trường hợp keepalive sinh ra để xử lý** là trường hợp làm hỏng.

### Fix
Tách thành hàm module-level `_iter_with_keepalive(stream, keepalive_interval)`, giữ pull đang chạy trong `pending` và chờ lại nó ở vòng sau thay vì tạo pull mới:

```python
pending: asyncio.Future = asyncio.ensure_future(aiter_.__anext__())
while True:
    try:
        item = await asyncio.wait_for(asyncio.shield(pending), timeout=_POLL_TIMEOUT)
    except asyncio.TimeoutError:
        ...  # yield _KEEPALIVE; KHONG tao pull moi
        continue
    except StopAsyncIteration:
        return
    yield item
    pending = asyncio.ensure_future(aiter_.__anext__())   # pull moi sau khi da xong
```

Pull kế tiếp chỉ được tạo **sau khi chunk trước đã được yield** — tức là pull cũ chắc chắn đã hoàn thành.

Cùng lớp bug còn một chỗ nữa, ở `opencode_proxy/handler/stream_executor.py` — cùng pattern `shield` + timeout, cùng `continue`/lặp lại pull. Sửa cùng lúc, vì đó là bản sao của cùng một quyết định. Ngoài ra `_fetch_first` raise `RuntimeError("Empty stream from pool manager")` khi generator kết thúc trước chunk đầu tiên (client ngắt giữa lúc) — giờ đóng SSE sạch thay vì ném exception ra giữa stream.

### Vì sao đưa ra module-level
Wrapper là nested closure nên không test được. Test chỉ có thể chép lại logic — mà test một bản sao chỉ chứng minh bản sao đó đúng, không chứng minh code thật. Tách ra để `tests/test_stream_keepalive.py` gọi đúng hàm đang chạy production.

### Test
`tests/test_stream_keepalive.py` — 12 test, bao gồm:
- Chunk chậm hơn timeout vẫn giao **đủ và đúng thứ tự** (bug gốc).
- **Không bao giờ có 2 pull đồng thời** — đo bằng counter, đây là bất biến gốc.
- Keepalive **vẫn ping** — nếu fix bằng cách bỏ ping thì mất hết mục đích, và test phải bắt được điều đó.
- Model nhanh không bị spam ping.
- Assert cấu trúc: không có `__anext__` nào trong khối `except asyncio.TimeoutError`.

Trên bản gốc: **collection error** (hàm chưa tồn tại).

### Bài học
`asyncio.shield` + timeout + `continue` là một cặp độc hại, và trông rất hợp lý khi đọc. Keepalive vốn là code "phòng thủ chung", nên nó chạy đúng khi không có gì sai và chỉ hỏng khi thứ nó bảo vệ xảy ra.

---

## Bug #17: 7 Test Đỏ Khi Chạy Full Suite, Xanh Khi Chạy Riêng (2026-10-07)

### Muc do
Test nói dối. Không phải bug production — nhưng cũng là bug, vì nó che giấu bug thật.

### Mô tả
`test_dashboard_tokens.py` pass 29/29 khi chạy riêng. Chạy full suite: 7 fail với `KeyError: 'code'`, `KeyError: 'key_id'`, `AssertionError: 429 == 200`.

### Root cause
`security_middleware` giới hạn `/dashboard/*` 60 req/phút mỗi IP, đếm trong `_dash_hits` — dict ở module level, sống suốt process. `TestClient` **luôn** báo host `"testclient"`, nên mọi test trong suite dồn về một ô đếm.

Test thứ 61 bị tính vào trần của test thứ 1. Chạy riêng file thì dưới 60 nên xanh — đó là lý do nó sống sót: một global dùng chung chỉ vượt trần production khi đúng thứ tự chạy đầy đủ.

### Fix
Autouse fixture trong `conftest.py` xoá counter trước và sau mỗi test.

**Đây là fixture, không phải sửa limiter.** 60 req/phút mỗi IP là hành vi production đúng và không có gì ở đây làm nó nới lỏng.

Ngoài ra sửa một bug sẵn có trong chính conftest: guard chỉ kiểm `before is None`, không kiểm `after` — nếu `usage.db` bị xoá giữa lúc thì `.get()` gọi trên `None`.

### Test
Xác nhận fix là thật: revert `tests/conftest.py` → 7 fail trở lại đúng như cũ. Suite xanh 647 test.

---

## Bug #18: Chunk `choices: []` Gửi Không Đúng Spec, Client Báo "invalid response" (2026-10-08)

### Muc do
Client chat dung OpenAI SDK, bao loi khi stream. Không phai loi model — cau truc phai ve sai.

### Mô tả
`/v1/chat/completions` với `stream: true` luôn gửi thêm mot chunk dung ở cuối:

```json
{"id":"chatcmpl-...","choices":[],"usage":{"prompt_tokens":N,...}}
```

Client nao index `chunk.choices[0]` ma khong kiem tra do dai se gap `IndexError` — va loi do len la "invalid response" / "switch model", khong chi nao mot frame client chang khong nen nhan.

### Spec (OpenAI, `include_usage`)

> If set, an additional chunk will be streamed before the `data: [DONE]` message.
> The usage field on this chunk shows the token usage statistics for the entire
> request, and **the choices field will always be an empty array**.

Truc "an toan" danh sach rong **chi** hop le o chunk do, va **chi khi client yeu cau**. Do do:

| Client gui | Spec | Router truoc fix | Router sau fix |
|---|---|---|---|
| khong co `stream_options` | khong gui usage chunk | **gui** (sai) | khong gui |
| `include_usage: true` | gui chunk usage | gui | gui |
| `include_usage: false` | khong gui | **gui** (sai) | khong gui |

Code cu lay ra 6 frame cho moi ca ba truong hop — bang chung rang no bo qua client.

### Root cause
`execute_stream()` doc `body` nhung khong bao gio doc `stream_options`. Chunk usage duoc `yield` vo dieu kien:

```python
usage_chunk = {..., "choices": [], "usage": {...}}
yield f"data: {json.dumps(usage_chunk)}\n\n".encode("utf-8")
```

### Fix
Doc `stream_options.include_usage` va chi yield chunk khi client thuc su yeu cau. Khong sua gi o client-side — client lam dung, router phai tuan thu spec.

### Vì sao phải verify bằng SDK thật
Test chay `TestClient` + assert tho co the bo loi. Phai chay **OpenAI SDK that** (`openai` 2.41.0) va **Anthropic SDK that** (`anthropic` 1.12.0) qua HTTP that tren port thật, roi doc exception chain day du (SDK boc loi trong `APIConnectionError`). Chi khi do moi biet client that su that co that bai khong.

Ket qua do test: 4 duong deu xanh — non-stream/stream × 2 protocol, kem tool call va output dai. Loi chi xuat hien o client *tuy chon* dung frame, nen phai dung client that moi bat duoc.

### Test
`tests/test_openai_stream_usage_chunk.py` — 8 test, chay app that qua route that. Co guard chong fix qua tay: `include_usage: true` van phai nhan duoc usage chunk, va moi frame van phai co `choices` khong rong.
- Bản gốc: **5 fail**
- Sau fix: **8 pass**

### Them: `temp_db` fixture hong data that
Con do lo test nay, `conftest` bat duoc suite ghi vao `usage.db` (`key_status` +1, `key_penalties` +1):

```
AssertionError: the test suite wrote to usage.db: {'key_status': (66, 67), ...}
```

`temp_db` redirect `db_mod._DB` nhung **khong refresh `router` singleton**. Router giu key status tai thoi diem import, tu DB luc do. Test boot app -> `register_keys_in_db()` + `record_success()` ghi qua state da cache do, va nhung write do ve `usage.db` that.

Fix: `temp_db` goi `router.refresh_keys()` sau khi redirect (tro con cache ve temp file), va them mot lan nua o teardown tro nguoc lai. Day la lo hong cua fixture dung chung, khong phai cua test nay.

### Test
655 pass, chay 2 lan lien tiep deu xanh, khong con canh bao ghi DB.
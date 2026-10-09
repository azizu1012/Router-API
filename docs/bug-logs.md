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
---

## Bug #19: Tool Call Bien Mat Hoan Toan Tren Anthropic Streaming (2026-10-09)

### Muc do
Nghiem trong nhat cua lo chay nay. Moi request streaming co tool_use deu tra ve
HTTP 200, SSE hop le, `message_stop` dung — **va khong co block nao**. Client
chay xong mot luot hop le roi khong lam duoc gi.

### Root cause

`_buffer_tool_calls` doc delta bang:

```python
tool_calls_val = getattr(delta, "tool_calls", None) or delta.get("tool_calls") if hasattr(delta, "get") else None
```

Python parse bieu thuc tren theo `(a or b) if hasattr(...) else None`. Vay khi
delta **khong co** `.get()` — va delta luon la object, boi vi proxy doc bang
`getattr` — toan bie bieu thuc ra `None`, ke ca khi `tool_calls` co that.

```python
class D: tool_calls=[{'id':'c1',...}]
d = D()
getattr(d,'tool_calls',None) or d.get('tool_calls') if hasattr(d,'get') else None
# -> None        <-- tool_calls ton tai va bi bo qua
```

Dong 405 trong cung file (`thought_signature`) co dau ngoac, nen **dung**. Chi
dong 197 thieu — va no la dong quyet dinh tool call co the ban hay khong.

### Hau qua
Khong phai chi thieu mot block:

| | Truoc | Sau |
|---|---|---|
| `content_block_start` | khong co | `tool_use` |
| `stop_reason` | `end_turn` | `tool_use` |

`end_turn` chinh la ly do bug song sot: client thay mot cau tra loi hoan chinh
voi `end_turn` thay vi `tool_use`, va Claude Code tu ket thuc luot, khong bao
gi gọi tool. Khong log, khong trace, khong status sai.

### Fix
Tach `getattr` va `.get` thanh hai buoc, khong dua `hasattr` vao conditional
expression:

```python
tool_calls_val = getattr(delta, "tool_calls", None)
if tool_calls_val is None and hasattr(delta, "get"):
    tool_calls_val = delta.get("tool_calls")
```

### Vì sao test harness cũ không bắt được
Test phai truyen delta dang **object**. Test cu truyen dict, va dict co `.get`,
nen bieu thuc cu giong vo cach chay production — chi co chunk dau tien co
tool_calls la loi, phan con lai chay dung. Test moi truyen object nhu that.

### Test
`tests/test_anthropic_stream_order.py::TestToolCallsSurvive` — 4 test: co
`tool_use` block, dung id/name/arguments, `stop_reason == "tool_use"`, va ca
duong dict. Bản gốc: **fail**; revert fix: **fail ngay**.

Xac nhan them bang **Anthropic SDK that** 1.12.0 qua HTTP that:
`stop_reason=tool_use tool=get_weather({'city': 'Paris'})`, va
`ToolUseBlock.model_validate()` pass.

---

## Bug #20: `/v1/responses` Event Sai Spec, Cac Frame Closing Mat Câu Tra Loi (2026-10-09)

### Muc do
Nghiem trong theo bat xac thuc. Cac frame van **validate duoc** — nen khong co
gi bao loi — nhung mang noi dung rong.

### Root cause
Adapter dung mau cu (chi co 3 event, thieu 2) va bo qua tung field bat buoc.
Check bang `openai.types.responses` (model that client that dung de build):

| Sai lech | Hau qua |
|---|---|
| `Response` thieu `parallel_tool_calls`, `tool_choice`, `tools` | 3 event fail validation |
| `output_text.delta` / `.done` thieu `logprobs` | 1 event fail validation |
| `ResponseUsage` thieu `input_tokens_details`, `output_tokens_details` | `response.completed` fail |
| `response.output_text.done` `text=""` | mat toan bo cau tra loi |
| `output_item.done` `content: []` | mat cau tra loi |
| `response.completed` `output: []` | **day la field client doc ket qua** |
| thieu `content_part.added` / `content_part.done` | vong doi content part khong dong |
| thieu dong `event: <type>` | client dispatch theo event name khong chay |

Dong `text: ""` + `content: []` + `output: []` nguy hiem nhat: ca ba deu hop le
validate, nen may tinh khong bao gi. Nhưng `response.completed.output` chinh
la noi client Responses doc ket qua — **mot request thanh cong tra ve rong**.

### Fix
- `_responses_event` them dong `event: <type>` truoc `data:`.
- `_responses_envelope()` — mot cho, ca hai path — khai bao `parallel_tool_calls`,
  `tool_choice`, `tools`, `usage`.
- `_responses_usage()` doi `prompt/completion_tokens` sang `input/output_tokens`
  va luon kem ca hai object `*_tokens_details`.
- Gom text trong adapter, roi day vao `output_text.done`, `content_part.done`,
  `output_item.done` va `response.completed.output`.
- `sequence_number` chay tu `0`, dung mau docs.

### Vi sao lai do chung mot helper
Stream va non-stream la hai route, nhung cung mot dialect. Hai ban sao la cach
tuy chon de chung mot field bat buoc. `_responses_envelope` va
`_responses_usage` bay gio la noi dung chung; test khoá ca hai.

### Test
`tests/test_responses_conformance.py` — 24 test. Validate **moi** event bang
model chinh thuc, ke ca `strict=True` (ban doc chat hon, reject bat ky field
SDK khong khai bao). Cac frame mau trong docs OpenAI chinh la chuan so sanh.

`tests/test_responses_and_search_dialects.py` phai doc dong `data:` thay vi
`text.startswith("data: ")` — frame hien hop le co hai dong.

---

## Bug #21: Anthropic Stream Mo Bang `ping`, Khong Phai `message_start` (2026-10-09)

### Muc do
Nghiem trong theo hieu bieu. Client doc `type` truc tiep tu payload se **that
bai khi vua nhan** chu khong phai hien thi sai.

### Root cause
Proxy phat ping truoc, kem hai field tu phat `reason` va `retry` — ca hai deu
khong co trong spec. Docs Anthropic (`messages-streaming`) mo ta:

> 1. `message_start` (a Message with empty content)
> ...
> There may be `ping` events dispersed throughout the response as well

va payload cua ping la `{"type": "ping"}`, khong gi them.

Anthropic SDK accumulator tu cho thay:

```python
if current_snapshot is None:
    if event.type == "message_start":
        return ...
    raise RuntimeError(f'Unexpected event order, got {event.type} before "message_start"')
```

SDK that **khong** gap loi vi `Stream.__stream__` loc event theo **ten SSE**
va bo `ping` truoc khi den accumulator. Client nao doc payload `type` thi khong
co loc do — va no that bai bang loi *"event order"*, tro sai hoan toan.

### Fix
`message_start` phat truoc khi keo tu pool, truoc moi ping va context event.
Payload ping rut gon ve `{"type": "ping"}`. `emit_message_start` co lai, nen
vong tool recursion (`emit_message_start=False`) khong phat lan hai.

### Test
`tests/test_anthropic_stream_order.py` — 19 test, gồm:
- `message_start` la event dau.
- Mot bo terminal (`message_delta`, `message_stop`) duy nhat, ke ca khi recursion.
- Payload ping **dung bang** `{type}` — cau hinh cu `reason`/`retry` se do lao.
- `accumulate_event` cua SDK chay duoc ca stream (test dat payload ping sai thu
  tinh co lenh RuntimeError trong `test_ping_before_message_start_is_what_the_sdk_rejects`).

Hai test cu (`test_stream_impl_seams.py`, `test_anthropic_integration.py`) **dinh
thu tu sai** — chung chinh la thu tu bug — nen da sua theo docs. Intent goc cua
chung ("ping den truoc token dau") van duoc giu.

---

## Bug #22: `thinking_level` Explicit Bo qua Enum Cua Gemini (2026-10-09)

### Muc do
Im lang theo bieu bieu, va chi lo ra khi key nguoi dung go tay `thinking_level`.

### Root cause
`ThinkingLevel` cua GenAI la enum dong: `minimal/low/medium/high`. Nhung SDK
**khong validate** — no chi `UserWarning` roi van tao ra member
`ThinkingLevel.banana`:

```
UserWarning: banana is not a valid ThinkingLevel
```

`build_thinking_config` cung chi `.lower().strip()`, nen khong chan gi ca.

Va `_effort_to_level` — ham chuan hoa **moi** effort source khac — khong chay
 tren nhanh `thinking_level` explicit:

```python
if tl is not None:
    params["thinking_level"] = tl     # <-- thang duy nhat bo qua chuan hoa
```

Con `reasoning_effort` va `output_config.effort` deu `or "low"` khi gap tu la.

Ket qua: client gui `thinking_level: "banana"` se day `thinkingLevel: "banana"`
xuong Google, va Google **tu choi** tai request do.

### Fix
Cho `thinking_level` explicit di qua kiem tra enum, giu nguyen nghia cac tu tat
thinking:

```python
_VALID_LEVELS = ("minimal", "low", "medium", "high")
_OFF_WORDS = ("none", "off", "false")
```

`_effort_to_level` se gop `off/none/false` thanh `minimal`, va tren 2.5
`minimal` lai **bat** thinking — ham nay loai do, giu nguyen duong tat.

### Test
`tests/test_effort_mapping.py::TestExplicitThinkingLevelIsChecked` — 9 test:
tu la ve `low`, ca 4 level that giu nguyen, uppercase chuan hoa, ca 3 tu tat
van tat (ca tren 2.5), chuoi rong, va test cuoi cung kiem `types.ThinkingConfig(...)`
co that su ra mot enum member hop le hay khong.
---

## Bug #23: `/v1/responses` Bo Qua Tool Call, Client Duoc Tra Ve "Da Tra Loi" Rong (2026-10-09)

### Muc do
Nghiêm trọng theo nghia nghĩa client: mot client goi `/v1/responses` co truyen
`tools`, model goi ham, va client nhan ve mot response **thanh cong** trong do
khong co gi ca.

### Root cause
Adapter chi doc `delta.content`:

```python
delta = (choice.get("delta") or {}).get("content")
if not isinstance(delta, str) or not delta:
    continue
```

Mot luot goi tool khong co text nao, nen khong frame nao duoc sinh ra ngoai
bo khung. `response.completed` den voi `output: []` — va do chinh la field
client doc ket qua.

Day la **cung lop bug** voi Bug #19 (tool call bi bo tren Anthropic), chi o
mot tang duoi. Bug #19 da duoc sua o `src/api/`, con duong nay la mot adapter
rieng trong `src/server/` nen khong di theo.

Non-stream cung vay: `_extract_response_text` tra `""`, envelope duoc dung
tuong doc, `output_text: ''`, `output` rong.

### Fix
- Stream: theo doi `delta.tool_calls` theo `index`, cap `output_index` **khi
  item mo** chu khong phai khi item dong (`len(output)` luon bang 0 trong vong
  lap). Emit day du bo:
  `response.output_item.added` (item `function_call`) →
  `response.function_call_arguments.delta` →
  `response.function_call_arguments.done` → `response.output_item.done`.
- Item message **khong** mo up front nua: chi mo khi co text delta dau tien,
  vi luot chi goi tool se sinh ra mot message rong.
- Non-stream: them `_extract_response_tool_calls()`, doc ca hai khu duyet
  (`function.name` long nhau va dang flatten) va serialize dict arguments
  thanh JSON thay vi bo.

### Vì sao test cua chinh tui bat them mot bug
`test_two_tool_calls_get_separate_output_indices` fail ngay lan chay dau voi
`assert [0, 0] == [0, 0]` — `len(output)` la 0 suot thoi gian loop, nen ca hai
tool call cung nhan `output_index` 0. Neu chi kiem "co function_call hay khong"
thi hai client deu co the nhan ve mot stream **hop le** voi hai item chong
nhau.

### Test
`tests/test_responses_conformance.py::TestToolCalls` va
`::TestNonStreamToolCalls` — 11 test. Trong do co guard chong fix qua tay:
mot luot chi goi tool **khong** duoc sinh `output_text.delta`,
va text + tool call phai ra `["message", "function_call"]`.

Xac nhan bang **OpenAI SDK that** 2.41.0 qua HTTP that:
`get_weather({"city": "Paris"})` o ca non-stream va stream, va moi frame
validate duoi `strict=True`.

### Khong phai bug: streaming khong co thinking block
Quan sat HTTP cho thay `/v1/messages` co `thinking` block o non-stream nhung
khong co o stream, nghi la mat extended thinking. Kiem chung lai bang stub
deterministic: khi chunk mang `reasoning_content`, stream path **co** san
`content_block_start` (thinking), `thinking_delta`, `signature_delta` va
`content_block_stop`. Khac biet chi la model khong tra thought part cho prompt
do. Khong sua gi.
---

## Bug #24: `finish_reason` Vuot Enum OpenAI, Client Bao "Invalid Response" (2026-10-09)

### Muc do
Nghiem trong theo bat xac thuc. `finish_reason` la `Literal` trong SDK
(`stop`, `length`, `tool_calls`, `content_filter`, `function_call`), nen mot
gia tri ngoai enum lam **ca response** khong parse duoc.

### Root cause
Router pass thang gia tri tu upstream:

```python
finish = getattr(choice, "finish_reason", "stop")
```

Gemini dung bo khac hoan toan: `SAFETY`, `RECITATION`, `BLOCKLIST`,
`PROHIBITED_CONTENT`, `SPII`, `IMAGE_SAFETY`, `MALFORMED_FUNCTION_CALL`,
`UNEXPECTED_TOOL_CALL`...

Da quan sat thuc te mot client nhan `finish_reason: "malformed_function_call"`
voi `tool_calls: null` — dung chuoi duy nhat trong danh sach khong phai enum
ma lai "moi` loi invalid response".

Duong streaming con sai theo huong nguoc: no rut moi ve `length` hoac `stop`,
nen **`content_filter` bi mat hoan toan**.

### Fix
`src/core/providers/finish_reason.py` — mot noi quyet dinh, ca hai duong dung:

| Truong hop | Ket qua |
|---|---|
| co `tool_calls` trong payload | `tool_calls` (thang ca ca khi upstream noi gi) |
| `SAFETY` / `RECITATION` / `BLOCKLIST` / `SPII` / ... | `content_filter` |
| `MAX_TOKENS` va moi tu dua tren | `length` |
| `MALFORMED_FUNCTION_CALL` / `UNEXPECTED_TOOL_CALL` | `stop` |
| bat ky gia tri khac | `stop` |

Hang cuoi cung la ly do fix ton tai: mot chuoi la khong enum thi **khong the**
pass thang, va do la chinh la nguyen nhan response khong hop le.

### Test
`tests/test_finish_reason.py` — 34 test, gom vong lap tren **moi** member cua
`google.genai.types.FinishReason` de khong con enum nao chua duoc xet, va hai
test goi truc tiep `build_response` / `_extract_finish_reason` de chung to ra
wire. Guard chong fix qua tay: tool call **khong** co trong payload thi
`MALFORMED_FUNCTION_CALL` phai ra `stop`, khong phai `tool_calls`.

---

## Bug #25: Gemini Native Tra 500 `text/plain`, Root Cause La Mot Package Thieu (2026-10-09)

### Muc do
Nghiem trong theo he qua: **ca endpoint** `:generateContent` chet, va client
chi thay `Internal Server Error` khong phai JSON.

### Chain day du
```
client -> POST /v1beta/models/gemini-flash:generateContent {"contents": [...]}
  -> web_search = True          (mac dinh cap account, du client khong hoi search)
  -> can_native_ground = False  (model khong phai lite)
  -> _load_adk_runner()        (google-adk CHUA CAI)
  -> nam trong retry loop -> bat `except Exception` -> dem la model failure
  -> thu lai moi key, moi pool member
  -> all_models_excluded -> RuntimeError("quota_exhausted")
  -> khong bat -> 500, content-type: text/plain, body: "Internal Server Error"
```

Ba tang, ca ba sai:
1. `ADKUnavailableError` docstring ghi *"Raised before the retry loop"* — nhung
   `adk = _load_adk_runner()` nam **trong** loop, dung nguoc docstring.
2. `quota_exhausted` chon sai hoan toan: nguyen nhan la mot package chua cai.
3. Handler nem exception ra Starlette, nhan `500 text/plain`. Google API tra
   `{"error": {"code", "message", "status"}}`, va `google.genai.errors.APIError`
   doc dung ba field do. Client native chi thay loi decode.

`streamGenerateContent` còn te hon: no nem frame `{"error": {"message","type"}}`
— **shape cua chat-completions** — nen client native thay mot loi ma doc
duoc gi ca.

### Fix
- `_load_adk_runner()` chuyen ra **truoc** retry loop, o ca `call_gemini` va
  `call_gemini_stream`. Dieu kien `web_search and not can_native_ground` khong
  doi theo key, attempt hay model nen tinh mot lan la du.
- Handler phan biet **search client yeu cau** voi **mac dinh cap account**. Khong
  co yeu cau thi tra loi luon khong co search — client hoi mot cau hoi, khong
  phai hoi mot cu tim kiem. Co yeu cau thi tra loi loi that, noi ro nguyen nhan.
- `gemini_error.py`: mot noi sinh envelope cua Google cho ca hai duong.
  Streaming het HTTP 200 roi nen body la cho duy nhat con lai de bao client.
- Khong sua gi o `gemini_streaming.py` cho phep fallback — `except RuntimeError`
  bat mat `ADKUnavailableError` truoc khi fallback co co hoi.

### Bonus: SDK field lo bi ship ra client
`GenerateContentResponse.sdk_http_response` la httpx response con s sung cua
SDK. `model_dump(by_alias=True)` ship header block no vao **moi chunk**, va o
mot chunk no chua `bytes` kien `json.dumps` nem
`"Object of type bytes is not JSON serializable"` — loi khong noi ten model, khong
noi ten field, khong noi ten chunk nao.

`src/core/providers/gemini/response_dump.py` cat field nay (va `parsed`,
`automatic_function_calling_history`).

**Bay bien**: `Part.thought_signature` la `bytes`, va config pydantic cua SDK tu
base64 ho no khi xuat JSON. `model_dump()` tra raw bytes, nen `json.dumps` hoac
tu choi, hoac — neu them `default=str` — ghi dung python repr `"b'\\x12i...'"`
vao mot field client coi la chu ky. Do la ly do phai qua `model_dump_json`.
Test khoa ca hai chieu.

### Vì sao helper nam o `src/core/`, khong o `src/server/`
Lần dau dat `dump_generation_response` canh route cua native Gemini. Nhung no
dung trong `manager.py` — ma `manager.py` o `src/core/`, va`src/core/` khong duoc
import `src/server/`. `tests/test_layering.py` bat ngay. Helper chuyen xuong
`src/core/providers/gemini/`; phan sinh HTTP envelope moi thuoc ve server.

### Test
`tests/test_native_gemini_errors.py` — 20 test: envelope co dung field ma SDK
doc, mapping status, `APIError` parse duoc body cua chung ta, phan loai loi
(thieu ADK = 500 chu roi quota = 429), va hai test doc source bang `inspect` de
khoa `adk` nam truoc `for attempt`.

Xac nhan bang **google-genai that** qua HTTP that: `generateContent` tra loi binh
thuong, `APIError` doc duoc loi cua chung ta, `streamGenerateContent` ra chunk
validate duoc.
---

## Bug #26: `/v1/completions` 503, `/v1/models` Khong Hop Le, WebSearch Ro Ra Client (2026-10-09)

Ba bug tim bang cach quet lai **toan bo** endpoint, dung SDK that qua HTTP that.
Khong cai nao nam trong khu vuc test hien co.

### 1. `/v1/completions` — 503 khi khong co `max_tokens`

Route do lenh `chat_body["max_tokens"] = body.get("max_tokens")`, tuc la
**luon co key**, gia tri la `None`. Ben ngoai:

```python
max_tokens = min(int(body.get("max_tokens", config.MAX_OUTPUT_TOKENS)), ...)
```

`dict.get(k, default)` **chi** tra default khi key *vang mat*. Key co mat,
gia tri `None`, nen `int(None)` nem `TypeError`. Route bat loi roi tra 503
generic — client chi thay "Service temporarily unavailable".

Cung lo trap voi `temperature`. Sua ca hai: dung `or`, va route bo hanh
ghi `None` vao body.

### 2. `/v1/models` — thieu `lifecycle`

Comment trong file khai *"emit a superset that satisfies each validator"*.
`anthropic.types.ModelInfo` **bat buoc** `id`, `type`, `display_name`,
`created_at`, `lifecycle` — thieu `lifecycle` nen entry khong validate duoc.
README cung quang bao "OpenAI **and** Anthropic schema superset".

### 3. `web_search: true` — router ro `WebSearch` tool ra client

Route chat inject `_WEBSEARCH_TOOL_DEF`, gan voi hien `pool_manager` **khong co**
tham so `web_search` nen khong the dung Google grounding. Tool do la co che tim
kiem duy nhat tren path nay — va **khong ai chay no**.

Ket qua do moi cau hoi can search:

```
finish_reason: 'tool_calls'
content:       ''
tool_calls:    [{"name": "WebSearch", "arguments": "{\"query\": \"...\"}"}]
```

Mot tool noi bo ma client khong co phien ban, tren mot response rong. README
mo ta day la "server-side tool loop" — client khong phai implement search.

May interception co san **chi o proxy Anthropic**. Proxy OpenCode co mot ban
khac trong `nonstream_executor.py`, nhung `/v1/chat/completions` goi thang
`pool_manager.call_nonstream` va bo qua ca hai.

Fix: `search_intercept.py` chay search, feed ket qua nguoc vao, lap lai, gioi
han 3 vong. Doc ca hai shape tool call (facade lam phang `{name, arguments}`,
OpenAI long trong `function`) va ca hai hinh response (`choices[0].message`
va `message`) — sai mot cai la loop im lang khong bat tool nao.

### Vì sao test harness cu khong bat duoc
Ca ba deu can request that. Test hien co chi goi `pool_manager` truc tiep
hoac dinh nghia route rieng, nen khong route nao chay day du vong
`route -> proxy -> pool_manager -> provider`.

### Test
`tests/test_uncovered_routes.py` — 13 test. Bản gốc: **5 fail**.
Kiem chung them bang SDK that qua HTTP that: `/v1/completions` validate la
`Completion`, `/v1/models` validate la `ModelInfo`, va `web_search: true` tra
`finish_reason: stop` kem cau tra loi that tren ca 3 engine.
---

## Bug #27: Hosted Web Search Ra Sai Hinh Dang Cua Ca Hai Provider (2026-10-09)

### Muc do
Im lang va lam hong luot chat. Client nhan mot `function_call` cho mot tool ma
no khong co — do chinh router nem ra.

### Root cause
Router xu ly search **nhu mot function tool** ten `WebSearch`. Do la khong dung
hinh dang cua **ca hai** provider, va khong dung ca hinh dang function tool:

| | Client khai bao | Provider thuc thi | Client chay loop | Tra ve |
|---|---|---|---|---|
| OpenAI | `tools:[{"type":"web_search"}]` | co | **khong** | item `web_search_call` |
| Anthropic | `tools:[{"type":"web_search_20250305"}]` | co | **khong** | block `server_tool_use` + `web_search_tool_result` |
| Function tool | `tools:[{"type":"function",...}]` | **khong** | **co** | `function_call` + client gui `tool_result` |

OpenAI noi ro trong docs Agents API:

> "If you leave web_search out of agent.tools, built-in web search is off.
> **Asking for a search in the prompt does not turn it on.**"

Ba tang sai, ke ca mot tang tu "auto" ma ban ghi:
1. `gemini_handlers.py` bat search cho **moi** account ma `search_engine` khong
   literal la `"disabled"` — tuc la moi account, vi default la `"auto"`. Mot
   cau hoi binh thuong bi day vao ADK path; thieu `google-adk` thi ca endpoint
   `:generateContent` chet (Bug #25).
2. Chat dialect inject tool search ma khong gi chay no (Bug #26).
3. Ket qua la client nhan `WebSearch` tren mot response rong.

### Fix
- **`_hosted_search`**: Responses danh dau chat body; chi dialect nay duoc inject.
- **Responses**: phat `response.web_search_call.in_progress` / `.searching` /
  `.completed` + item `web_search_call` co `action` va `status`. Khong phat
  `function_call`.
- **Anthropic**: phat `server_tool_use` (id tien to `srvtoolu_`) + 
  `web_search_tool_result` noi bang `tool_use_id`. Khong phat client `tool_use`.
- **Gemini native**: bo nhanh default-on. Client muon search thi gui
  `google_search` tool; Google tu quyet dinh grounding — van dung.
- **Chat Completions**: khong co hosted search, khop spec OpenAI. Muon search thi
  dung `POST /v1/search`.
- `drive_search_loop` tra them `trace` de moi dialect tu phat hinh dang cua no.

### Vì sao trace lai can
`search_intercept` lo duyet client co the chay tool nao. Nhung client phai
thay **hinh dang cua provider dang noi**, khong phai mot function call chung.
Mot danh sach "cuc bo nao da search" duoc cho ca hai va khong ton tai o gi.

### Ghi chu: streaming
Responses stream phai bao cao `web_search_call` *truoc* khi text bat dau — dung
thu tu OpenAI. De lam duoc, search duoc chay cho xong truoc khi stream bat dau
(`prime_search`); luot co search do khong stream dan thong cho den khi search
xong. Do la dinh dich, va no ghi ro trong docstring.

### Test
`tests/test_hosted_search_shape.py` — 21 test: `web_search_call` validate duoi
`strict=True`, `server_tool_use` co `srvtoolu_`, result block noi bang
`tool_use_id`, search fail -> `status: failed` / `error_code`, function tool
that cua client **van** la `function_call`, va mot test doc source khoa rang native
path chi bat search mot lan — o cho doc `google_search` tool cua client.

Xac nhan bang SDK that qua HTTP that:
- OpenAI: `output=['web_search_call','message']`, status=completed, action=search
- Anthropic: blocks `['server_tool_use','web_search_tool_result',...,'text']`,
  `stop_reason=end_turn`
---

## Bug #28: `strict=True` Khong Phai Cong Gate — Hosted Search Van Sai Spec Sau Khi Da "Xanh" (2026-10-10)

### Muc do
Client van nhan duoc cau tra loi **va** nhan day du metadata sai. Khong frame nao
bi loai, khong field bat buoc nao thieu. Cac mau cau hinh protocol sai hon doc
nay deu **validate duoc** — nen mot test xanh khong chung minh gi ca.

### Root cause

`a440dcb` sua hosted search cho khop OpenAI/Anthropic va them test validate bang
model chinh thuc cua SDK. Doi chieu SDK that cho thay pydantic co **hai** co che
khuac nhau, va test chi dung co che thu hai:

| Co che | Chan gi |
|---|---|
| `strict=True` | Sai **kieu** du lieu — string thay vi int |
| `extra="forbid"` | Field **khong khai bao** trong spec |

`ActionSearch` khai bao `type`, `queries`, `query`, `sources`. Router ship them
`search_engine` — mot ten field internal, khong nam trong spec nao:

```python
action["search_engine"] = record["engine"]   # -> 'duckduckgo'
```

`ResponseFunctionWebSearch.model_validate(item, strict=True)` **PASS**. Chi khi
them `extra="forbid"` moi REJECT. Test cu goi `strict=True` va xanh — no kiem
dung thu ma khong chan gi.

### Vì sao đây là loại bug khó nhất trong loạt này

`docs/bug-logs.md` đã lặp lại một luật suốt 27 bug: *silent success* — code
chạy đúng, response hợp lệ, chỉ là hành vi sai. Lần này nặng hơn, vì **test
cũng xanh**. Một regression test chỉ chứng minh điều mà nó kiểm, và test này
kiểm sai thứ.

### Fix

| Sai lech | Truoc | Sau |
|---|---|---|
| Field tu chế trong action | `search_engine: "duckduckgo"` | bo; chi `type`/`query`/`queries`/`sources` |
| Nguon cua bai viet | khong co | `sources: [{type:"url", url}]` tu citations |
| Citation tren cau tra loi | `annotations: []` luon | `AnnotationURLCitation` `{url, title, start_index, end_index}` |
| `output_text` tren wire | ship o top-level response | bo — SDK tu tinh tu `output` |

`output_text` la **computed property** cua SDK:

```python
@property
def output_text(self) -> str:
    """Convenience property that aggregates all `output_text` items..."""
```

API khong gui field nay. Bo di khong lam SDK mat gi: client doc `.output_text` va
SDK tu tinh. Test cu them `body["output_text"] = "hi"` roi validate — **chinh
do la** lam no pass khi route ship mot field ngoai spec.

### Test
- `test_strict_alone_does_not_catch_an_undeclared_field` — pin ca hai co che
  tach biet. Comment ghi ro vi sao file nay dung `extra="forbid"`.
- Gate moi theo ma duong that: `ResponseFunctionWebSearch.model_validate(dumped,
  strict=True, extra="forbid")` tren payload **tu SDK client doc lai**, khong phai
  tren dict tay dung.

---

## Bug #29: `max_uses` Chi Doc, Khong Tinh — Cap Cua Client Bi Ve Qua Va Usage Dem Sai (2026-10-10)

### Muc do
Mot client dat `max_uses: 3` nhan **`usage.web_search_requests = 4`**. Client
dang tu so sanh chi phi cua minh se thay mot so lieu ma khong ai giai thich duoc.

### Root cause

`WebSearchTool20250305Param` khai bao `max_uses`, `allowed_domains`,
`blocked_domains`. Ca **ba** deu bi bo qua — router tu chay search nen day la
cho duy nhat noi co the lam chung.

Va loi cu hon nam o cho phat sinh so lieu: attempt bi cap chan **van duoc ghi vao
`server_tool_calls`** voi `result = "max_uses_exceeded"`, ma khong gi doc ra
`error_code` nen khong bi coi la failed:

```python
if rec.get("failed"):                      # False — "max_uses_exceeded" khong
    ...error_code: "unavailable"           # khop danh sach duoi
```

Hai he qua cung luc:
1. `usage.server_tool_use` dem 4 search cho 3 lan duoc phep.
2. Block tra ve co `content: []` — hinh dang cua **search thanh cong ma khong
   tim duoc gi**, khong phai cua mot attempt bi tu choi.

### Fix
`_server_tool_record` nhan `error_code` explicit. Attempt bi choi ghi
`"max_uses_exceeded"` — mot gia tri co that trong
`WebSearchToolResultErrorCode` cua SDK. So search **chay thu** moi tinh vao
`usage`; so **attempt** van giu trong blocks de client thay het.

Anthropic phan biet hai truong hop va router cung phai phan biet:

| Tinh huong | `content` |
|---|---|
| Chay, khong co ket qua | `[]` |
| Bi choi vi `max_uses` | `{"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}` |

### Bài học về cách kiem chung
Check hien dau tien so `usage.web_search_requests` voi **so block** — hai so
cung xuat phat tu mot cho sai nen chung khop nhau. Chi khi tach *"search da
chay"* (result la list) khoi *"attempt bi choi"* (result la error object) thi
sai lech moi lo ra. Mot kiem chung doc hai bien cung sai gia tri thi chung ta
khong kiem chung gi ca.

### Test
`tests/test_hosted_search_shape.py::TestRefusedSearchesAreNotCounted` — 6 test.
Bam lai tung fix: bo `error_code` → 5 fail; hardcode `"unavailable"` → 2 fail.

### Khac biet ro rang
`encrypted_content` cua Anthropic la du lieu **provider-side da ma hoa**. Router
khong the tai tao encryption that, nen no gui excerpt model da doc. Day la gioi
han cua mot gateway, va no duoc ghi ra day thay vi gia lao duoc ma hoa that.

---

## Bug #30: `google_search` Tren Gemini Native Chết 500 Khi Thieu `google-adk` (2026-10-10)

### Muc do
`POST /v1beta/models/{model}:generateContent` voi `tools: [{"google_search": {}}]`
tra **500**. Day la duong search **native** cua Google — client chi can khai bao
tool, Google lo phan con lai. Khong co gi sai ve protocol.

### Root cause

`_load_adk_runner()` chay khi `web_search=True`, truoc ca khi xem co can den ADK
hay khong:

```python
adk = _load_adk_runner() if web_search else None   # nem loi ngay
...
if adk is not None and not can_native_ground:       # quyet dinh o day, muon
```

`can_native_ground` cung yeu cau `is_lite`, nen `gemini-flash` (model mac dinh)
luon roi sang nhanh ADK. Vay client hoi native grounding tren mot model Gemini
— tai lieu cua chinh endpoint nay — van chet vi mot package optional chua cai,
du khong tai nao tren duong do can import no.

Comment ngay tren cung noi ro: *"Google decides grounding on its own when the
client asks for it with a google_search tool"*. Code lam nguoc lai.

### Fix
- `_adk_runner_for_search(model_id, has_media)` — chi resolve ADK khi branch ADK
  thu su tai can, va cache ca ket qua that bai.
- Bo `is_lite` khoi `can_native_ground`: Google ho tro grounding tren ca Flash
  va Lite. Khi ADK khong co, giu nhanh native thay vi tu choi.

### Test
`tests/test_native_gemini_errors.py::TestAdkIsNotSwallowed` viet lai thanh test
**hanh vi** thay vi test vi tri code. Hai test cu gan `src.index("_load_adk_runner()")
< src.index("for attempt in range")` — chung kiem mot vi tri, khong kiem mot
hành vi, va chính vi vay chung xanh khi endpoint chet.

---

## Bug #31: Streaming Anthropic Bo Qua Toan Bo Phan Hosted Search (2026-10-10)

### Muc do
Stream `/v1/messages` voi `web_search_20250305`: client **khong thay** block
`server_tool_use`, **khong thay** `web_search_tool_result`, **khong thay**
citations. Ma van tra 200 va `message_stop` dung — client chay xong mot luot
hop le roi khong co gi.

### Root cause

Logic search viet **hai lan**, mot trong `proxy_nonstream.py`, mot trong
`proxy_stream.py`, va hai ban do lech nhau ngay tu dau:

| | non-stream | stream |
|---|---|---|
| doc `max_uses` | co | **khong** |
| giu citations | co | **khong** |
| loc `blocked_domains` | co | **khong** |
| phat `server_tool_use` | co | **khong** |
| `usage.server_tool_use` | co | **khong** |

Khi recursion het, buffer con lai duoc emit ra **`tool_use`** cua client — lai
nhau cho mot search router da chay, hoac da bi `max_uses` tu choi.

### Fix
- `run_server_tool()` trong `anthropic_spec.py` — mot noi chay search, mot noi
  doc gioi han client, ca hai path dung chung. Docstring ghi ro vi sao phai
  gop: hai ban da lech truoc khi co test nao chan.
- Stream phat `server_tool_use` + `web_search_tool_result` bang content block
  thuong (dung hinh dang Anthropic mo ta cho streaming), kem `citations_delta`
  tren text block.
- `server_tool_calls` va `search_limits` truyen qua de quy, de `max_uses` dem
  ca luot chu khong dem theo tang.
- Search khong con xuyen ra `tool_use` client o ca hai path.

### Test
`TestTheStreamedSearchUsesTheHostedShape` + `TestTheStreamedSearchHonoursMaxUses`
— 14 test, chay proxy stream thật. Gate manh nhat: nap frame vao chinh
`accumulate_event` cua SDK roi validate `Message` strict + forbid.
Mutation: bo server-tool blocks → 2 fail; bo `citations_delta` → 1 fail;
bo counter → 4 fail.

---

## Bug #32: `response.completed` Bao `usage: null` (2026-10-10)

### Root cause
Chat chi gui chunk usage khi client xin, dung spec Chat Completions. Nhưng
`response.completed` cua Responses **luon** co usage. Client Responses khong
gui `stream_options` nen chunk do khong bao gi toi.

### Fix
Route Responses stream hoi `include_usage` cho tang chat. Client khong thay
chat chunk nao nen khong vi pham gi tren wire no nhin thay; chi dung de
`response.completed` co so lieu. Khi stream bi cat, adapter fallback sang uoc
luong — cung kieu uoc luong ma nhanh Anthropic da dung — vi `null` khi client
dang tinh tien se khong tinh gi ca.

### Bai hoc ve kiem chung
`Response.model_validate(client.model_dump())` **hong khong bao gi**: SDK them
computed property (`output_text.parsed`) ma API khong gui, roi pydantic bao loi
cho tung thanh vien cua union `output` — 159 loi cho mot response hoan toan hop
le. Phai validate **payload tren wire**. `tests/test_responses_conformance.py::
TestSearchCitationsInTheEnvelope` do cai nay, va harness live kiem bang raw
httpx chu khong phai SDK.

---

## Bug #33: `/v1/search` Loi Bi Long Hai Tang, Ly Do Khong Doc Duoc (2026-10-10)

### Muc do
Im lang theo hieu bieu. HTTP **400** dung, `error.message` dung — chi la nam sai
cho, noi client tim.

### Root cause

Route dung `HTTPException(detail={"error": {...}})`. FastAPI bo mot dict `detail`
vao them mot key `detail` nuaa:

```json
{"detail": {"error": {"message": "`query` parameter is required…",
                       "type": "invalid_request_error"}}}
```

Trong khi **moi route khac** trong `completions_routes.py` deu
`JSONResponse(content={"error": {...}})`, ra top-level. Client OpenAI-shaped doc
`body["error"]["message"]` — o day `body["error"]` khong ton tai.

Day la Bug #4 lap lai mot lan nua: nguyen han co that, chi nam ngoai noi client
doc. Test cu chi assert `status_code == 400` nen khong bao gio bat duoc.

### Fix
Ca 400 va 500 chuyen sang `JSONResponse` cung convention voi phan con lai.

### Test
`tests/test_search_endpoint.py::TestTheErrorEnvelopeIsReachable` — 4 test, gan
`body["error"]["message"]` va kiem `detail` **khong** con chua `error`.
Mutation: revert ve `HTTPException` → 2 fail.

### Them: results co chu nhung citations rong
Grounding co the tra ve prose ma khong kem chunk, nen `results` 879 ky tu dien
`citations: []`. Hop le — nhung im lang, va client nhin giong het mot lan search
khong tim duoc gi. Nay la warning, khong doi hanh vi: `[Search Endpoint] query
… produced N chars with 0 citations`. Khong bia them nguon, chi phan biet duoc
hai truong hop khi nguoi bao cao thieu nguon.

---

## Ghi chu kiem chung: mutation phai **thuc su** ap dung

Lan đầu mutation `HTTPException` bao "7 passed" — nghe nhu test khong co gia
tri. Hoa ra `String.Replace` khong khop whitespace nên **mutant chua bao gio duoc
ghi vao file**. `7 passed` do kiem chung **ma chua doi**.

Mot test gia lap xanh y het nhung khong chung minh gi, va no im lang hon ca mot
test do. Bay moi fix deu verify hai dieu: mutant co mat trong file khong, va
revert duoc thi co test do. `pyflakes` + `ast.parse` tren mutant la bao dam
khong phai.

---

## Bug #34: DuckDuckGo Van Bi Tru Quota, Va `auto` Mac Dinh Chay Google (2026-10-10)

### Muc do
Mot cu goi `/v1/search` **khong** goi Gemini nao, van ton hao RPM/TPM/RPD cua
account. Va client goi route ma khong khai engine nao se bi day xuong Google
grounding — cham hon 15 lan.

### Root cause

Hai nguyen nhan doc lap nhau, ca hai deu im lang.

**1. Tru quota nam tren nhanh engine.** `account_limiter.acquire()` nam *tren*
`if search_engine == "duckduckgo"`, nen mot lan scrape HTTP thuan — khong cham
key Gemini — van bi tinh tien. Do la mau thuan truc tiep voi README, noi rang
duong DuckDuckGo "tiet kiem 100% han ngach API Key cua Gemini".

Kiem chung don lap:

```
duckduckgo         limiter.acquire=1   LLM calls=0
google_grounding   limiter.acquire=1   LLM calls=1
```

**2. Mac dinh la `auto`, va `auto` bat dau bang Google.** Do la engine cham
nhat **va** la engine ton quota. Client goi route ma khong suy nghi chuyen
gi dang tra tien cho mot search ho chua chon.

Do chay cung mot cau hoi:

| Truy van | DuckDuckGo | Google grounding |
|---|---|---|
| AI/tech news Oct 2026 | 4236 ch, 5 nguon, 5.5s | 2337 ch, 4 nguon, 91.9s |
| React 19 release notes | 4740 ch, 5 nguon, 5.4s | 1663 ch, 4 nguon, 102.0s |
| Python asyncio 2026 | 4005 ch, 5 nguon, 4.8s | 2454 ch, 5 nguon, 64.0s |

DuckDuckGo nhieu nguon hon, text gap doi, nhanh gap 15–20 lan.

### Fix
- `charge_account_quota()` tach rieng, chi goi trong nhanh Google — nhanh nao
  that su co model call moi bi tinh tien.
- Mac dinh `/v1/search` doi sang `duckduckgo`. Do la ca hai yeu cau cua thay
  doi: nhanh hon 15×, khong ton quota, nen **doan sai cung khong ton gi**.
  Mac dinh `auto` thi client khong chon gi ca, con router chon cai dat nhat.
- Engine **sai** thi tra 400 kem danh sach hop le. Truoc do engine la rơi qua
  moi nhanh va tra 200 voi `results` rong — khong phan biet duoc voi mot search
  that su khong ra gi.
- `search_engine` bat buoc tren `/v1/search`. Thong bao loi nen ra het engine
  kem chi ro cai nao ton quota, de client khong can doc docs moi chon dung.

Cung nguyen tac da dung cho hosted tool: client tu khai bao, router khong tu
suy dien.

### Test
`TestOnlyTheGroundedSearchSpendsQuota` — 2 test, don lap, khong can cham
luong thuc. `TestTheEngineDefault` — 5 test: mac dinh la `duckduckgo`, mac
dinh khong bi tinh tien, engine sai bi 400, va engine explicit duoc hon.

### Khac biet can ro
`auto` **van con** va van ton quota neu Google tra loi. No chi con la lua chon
tuy bien, khong con la mac dinh.

---

## Bug #35: `/api/stats` Rò Rỉ Master Key — Lần 2, Vì Guard Đúng Vẫn Chưa Đủ (2026-10-10)

### Muc do
Nghiêm trọng nhất trong loạt này. Master key miễn **toàn bộ** quota (mục 6 cua
`account_auth.md`), nên đọc được một cái là leo thang đặc quyền trọn vẹn.

### Trieu chung
`GET /api/stats` khong co auth. Route bo sung usage stats bang cach ghep
`usage_logs.auth_key_prefix` (8 ky tu cuoi) voi `accounts.auth_key`, roi tra
`full_key` — **master key nguyen van**.

Do la mot lo chuoi, va moi link deu dung ve:

| File | Dong | Vai tro |
|---|---|---|
| `auth.py` | 197 | `_auth_key_prefix()` = `account["auth_key"][-8:]` |
| `account_manager.py` | 55-57 | login bang master key → `auth_key` giu nguyen master key |
| `account_manager.py` | 70 | login bang child token → `auth_key` **bi thay** bang token da compose |
| `dashboard_routes.py` | 763-766 | map 8 ky tu cuoi → `full_key: ak` |

Dong 70 la thu giai thich tai sao du lieu that khong lo: `usage.db` cua chinh
repo nay co **0/20** prefix trung voi master key, vi moi request deu dung child
token. Nguoi han khong thay vi chi doc code.

### Chung minh bang HTTP that
```
GET /api/stats   (khong header Authorization)   ->   HTTP 200
{"key_prefix": "6VjIPHDk", "account_name": "admin1",
 "full_key": "sk-8ANvyqoV-aZHnybv4vZREoK1e4IzO2vCp846VjIPHDk"}
>>> admin master key handed out: True
```

### Vua co auth, van lo
Fix dau tien them `_require_dashboard(request)`. **Khong du**, va day la phan
kho nhat cua bug nay:

```python
def _require_dashboard(request):
    payload = _verify_session_token(token)
    if not payload:
        raise HTTPException(401)
    return payload          # <-- khong xem tier

def _require_admin(request):            # ngay canh ben, da co san
    payload = _require_dashboard(request)
    if payload.get("tier") != "admin":
        raise HTTPException(403)
```

`/api/stats` in ra **moi account**. Bat buoc session chi chung minh "co mat
khau dung" — khong chung minh "la ai". Do la chuyen **authentication** sang
**authorization**, va chi mot test dang nhap bang tier yeu moi phan biet duoc:

```
[anonymous]     HTTP 401   leak=False
[free login]    HTTP 200   tier=free
[free -> stats] HTTP 200   >>> admin master key visible to FREE user: True
```

### Fix
- `_require_admin` thay cho `_require_dashboard`.
- Bo `full_key` khoi payload; tra `key_masked` = `sk-...{suffix}`. Khong doi
  ten field ma chi doi gia tri — mot ten field cu giong no se lam nguoi doc
  sau tu hieu rang gia tri do van la credential.
- `/api/ping-model` len `_require_admin`: no goi custom endpoint bang chinh
  `auth_key` cua endpoint do, nen user tier thap co the dot quota cua operator.
- Hai so sanh credential con lai chuyen sang `secrets.compare_digest`:
  `dashboard_routes.py` (duong login legacy, noi duy nhat trong duong auth con
  dung `==`) va `admin/helpers.py`.

### Vì sao UI van dung duoc
`TokenAnalysisTab` hien `k.full_key` de gan ten cho tung key. Sau khi mask,
`account_name` van o ngay canh nen chuc nang phan tich giu nguyen. Muon xem
key that: `GET /dashboard/admin/accounts/recovery`, admin only, UI bat nhan 2
lan — dung nguyen tac da viet trong `account_auth.md` muc 8b: "bang accounts la
thu admin chup man hinh, nen mot tam anh chup la mot danh sach credential".

### Scanner: `scripts/scan_security.py`
Bai test chan ha nghiem loi do la mot lan sua. Bai chan **lan sau** la scanner,
va no phai khong bao do. Ba rule, tat ca AST:

1. `unauthenticated-route` — route khong co guard nao reachable.
2. `weak-guard-for-account-wide-data` — tra du lieu cua account khac chi voi
   guard caller-scoped. **Phai co** guard admin **hoac** handler tu check tier.
3. `credential-in-payload` — dict literal chua credential, va no di vao
   container roi container do ra khoi ham.

Rule 3 phai theo data-flow, khong duoc chi nhin `return`. Hai shape that:

```python
return {"rows": rows}                  # container thoat ra
rows.append({"full_key": ak})           # credential da vao container
```

Va hinh thuc **rebind** ma chinh handler goc dung:
```python
enriched_top_keys.append({..., "full_key": full_key})
top_keys = enriched_top_keys            # doi ten roi moi tra
return {..., "top_keys": top_keys}
```
Alias phai resolve **sau** khi quet het, khong trong luc quet — neu resolve
trong luc quet thi ket qua phu thuoc thu tu dong, va source nay resolve sai.

### Ba false positive da lo, va vi sao
Tool bao do thi tool bi tat. Moi truong hop deu phai co ly do:

| False positive | Vi sao that | Cach lo |
|---|---|---|
| `/dashboard/login` | Tao `{"auth_key":...}` lam **ban ghi noi bo**, chi tra `token/name/tier` | Chi kiem credential **that thoat** khoi ham |
| `generateContent` (6 route) | Khong goi guard, nhung delegate `_handle_gemini_native` **co goi** | Resolve guard qua 3 tang delegate |
| `password == '1234'` | So sanh voi hang so de hoi "co phai default khong", khong phai doan | Bo qua khi mot ben la literal/ALL_CAPS |

`/dashboard/login` con nhe hon: `{"name": account.get("name")}` la **chon
field**, khong phai dua ca account ra ngoai. Ten dung lam `attr.value` cua
`Attribute` thi khong dem.

### Mutation
Bai fix loi 4 test do va scanner bao 2 finding. Unmask rieng `full_key` (giu
nguyen `_require_admin`) thi scanner van bat bang `credential-in-payload` — tức
hai rule doc lap nhau, khong trung nhau.

### Test
`tests/test_security_no_secret_leak.py` — 11 test, chay app that tren DB tam.
Trong do co 4 test nap cho scanner mot file **co lo** de chung minh no con
bat: neu scanner mat kha nang phat hien thi no phai do, vi scanner xanh
chung minh co gi ca.

---

## Bug #36: CI Do — Nguyên Nhân Không Phải Thiếu Dữ Liệu (2026-10-10)

### Muc do
Không phải bug production. Nhưng nó che giấu đúng thứ nguy hiểm nhất: **11 test
pass trên máy tôi và đỏ trên CI**, và lý do là máy tôi có `usage.db` còn sót.

### Bieu hien
`ci.yml` chay lai lan dau:
```
11 failed, 861 passed, 25 skipped, 11 warnings, 1 error in 89.26s
```

### Tai sao khong phai thieu mock data
Y tuong dau la "them mock data cho CI". Do la cach **sai**. Commit mot
`usage.db` nhị phân se:
- lech voi `schema.py` theo thoi gian ma khong ai biet,
- bi ghi de ngay khi bat ky test nao ghi vao no,
- trong giong du lieu mau nhung thuc ra la tai khoan that cua nguoi viet.

Dieu can sua la **test doc trang thai ma chi ton tai o may cua nguoi viet**.
Ca ba nhom lỗi deu cung mot kieu.

### 1. Thieu schema (`no such table: accounts`)
`usage.db` bi gitignore. `_db.py` chi bootstrap `key_status` va
`custom_endpoints` luc import — phan con lai do `init_config_tables()` tao, ma
`main.py` goi va pytest thi khong.

Do la ly do file pass o may: no co san `usage.db` voi `accounts` va
`account_keys` trong do. Mot file bi gitignore khong bao gio chay theo code.

Fix: session fixture trong `conftest.py` goi `init_config_tables()` mot lan.

### 2. Thieu `ROUTER_API_PASSWORD_KEY`
`set_password_db` chi ghi ban `password_enc` khi key duoc dat, nen test
recovery thay `None` tren checkout sach (2 fail). May co key trong `.env`.

Fix: session fixture dat gia tri `test-only-not-a-real-key` khi env var rong.

### 3. `test_openai_stream_usage_chunk` goi Gemini that (4 fail)
La file **duy nhất** trong toan bo suite goi provider that — nen cung la file
duy nhat khong chay duoc trong CI.

Mock chi mot ban, va phai dung cho ca hai:
```python
monkeypatch.setattr(config_mod, "GEMINI_API_KEYS", ["sk-test-dummy-key-for-ci"])
register_keys_in_db(config_mod.GEMINI_API_KEYS)
router.refresh_keys()
```
Chi patch config la **khong du**. `calculate_key_capacities_by_pool()` cong don
tren `router._key_status`, va dict do duoc dung tu DB. Khong co row thi
capacity = 0, roi `_apply_account_limit` het 429 *"tokens per minute limit
exceeded"* **truoc khi** provider duoc goi.

Va cache 1 giay cua capacity can xoa, neu test truoc lam nong no o luc key
list con rong.

Chunk gia phai co hinh dang **object**, khong phai dict:
```python
chunk = item["chunk"]
delta = chunk.choices[0].delta if chunk.choices else None
```
Dict o day tao ra stream **rong** chu khong phai stream loi — kho chay hon.

### Bug thật tim ra khi sua: 429 bi bao thanh 503
`_apply_account_limit` nem `HTTPException(429, ...)`. Route bat no nhu
`Exception` thuong roi tu suy lai status tu `str(e)`:

```python
if msg.startswith("quota_exhausted") or "rate_limited" in msg.lower():   # khong khop
...
return JSONResponse(status_code=503, content={"error": {"message":
        "Service temporarily unavailable", ...}})
```

Message that la *"Account rate limit exceeded: tokens per minute limit
exceeded"* — khong khop nhanh nao, nen ra 503.

**503 nghiem trong hon thong bao sai.** No bao client "server hong", va client
retry 503 ngay lap tuc. Mot lan cham luu bien thanh bao loi re-tra dam vao
chinh pool dang day. 429 la trang thai duy nhat nghia la *"chinh lai bi chan,
hay cho"*.

Fix: `HTTPException` da co san status + body dung, khong suy lai lai. Them
`_error_type()` cho truong hop `detail` khong phai dict, va bang phai phu het
moi 4xx ma route co the nem — mot code 4xx thieu entry se im lang thanh
`api_error`, ma client loc theo type se tinh no la loi server va retry.

### Mot fixture tu ghi vao DB that
`stub_provider` goi `register_keys_in_db` ma **khong** khai bao `temp_db`, nen
no duoc phep chay truoc khi `_db` bi tro ve file tam. Key do ghi vao `usage.db`
that. Guard trong `conftest.py` bat duoc — va chi bat trong checkout sach.

Fix: khai bao `temp_db` ro rang trong chinh fixture do, de thu tu khong con la
hy vong.

### Bai hoc ve cach xac nhan
Lo sai nay khong the thay bang `pytest` tren may. May co `.env` co key that,
co `usage.db` co schema, nen **hai** nguon can thiet biu dien cung luc deu
co do. Chi mot `git clone` moi + `pytest` moi phai ra.

Clone sach chay lai: **903 passed**, 0 loi, khong `.env`, khong network.

### Test
- `tests/test_error_status_mapping.py` — 6 test moi.
- `conftest.py` — 2 session fixture.
- `test_openai_stream_usage_chunk.py` — mock provider, 4/4 xanh.

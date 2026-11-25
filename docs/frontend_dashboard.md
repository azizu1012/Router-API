# Router API v2 — Frontend Web Dashboard & State Management

Tài liệu này trình bày kiến trúc, thiết kế và cơ chế quản lý trạng thái của ứng dụng quản trị (Admin/User Dashboard) nằm trong thư mục `frontend-src/` và được build ra `src/frontend/`.

---

## 1. Tổng Quan Kiến Trúc
Giao diện quản trị được thiết kế theo dạng Single Page Application (SPA) sử dụng:
* **Core**: React v18 + Vite.
* **Styling**: Tailwind CSS + DaisyUI (để xây dựng hệ thống component trực quan, glassmorphism cao cấp).
* **Icons**: Lucide React.
* **Localization**: Hỗ trợ 3 ngôn ngữ: Tiếng Việt (`vi`), Tiếng Anh (`en`), Tiếng Nhật (`ja`).

---

## 2. Quản Lý Trạng Thái Toàn Cục (State Management)
Toàn bộ trạng thái của dashboard được quản lý tập trung thông qua React Context tại [AppContext.jsx](file:///d:/AI_Projects/router_api/frontend-src/src/context/AppContext.jsx).

### Các state cốt lõi trong AppContext:
1. **`token`**: Token phiên làm việc hiện tại, được đồng bộ với `localStorage` để duy trì đăng nhập.
2. **`lang`**: Ngôn ngữ hiện tại (`vi`, `en`, `ja`), đồng bộ với `localStorage`.
3. **`theme`**: Giao diện hiển thị (Dark/Light mode).
4. **`activeTab`**: Tab hiện tại người dùng đang xem (Overview, Keys, Accounts, Endpoints, v.v.).
5. **`fontSize`**: Tỉ lệ scale cỡ chữ toàn hệ thống (90%, 100%, 115%, 130%).
6. **`tabData`**: Chứa toàn bộ dữ liệu phản hồi từ backend API để phân phối cho các tab con mà không cần gọi API riêng lẻ cho từng tab.

### Cơ chế Polling tối ưu (Periodic Sync):
Để đảm bảo dữ liệu hiển thị (số lượng request, trạng thái key, penalties, v.v.) luôn khớp với thời gian thực của backend, `AppContext` thiết lập một luồng polling định kỳ:
* **Tần suất**: Tự động gọi API `/dashboard/data` (hoặc `/dashboard/me` cho User thường) mỗi 5-10 giây một lần khi có token hợp lệ.
* **Tối ưu hóa**: Tất cả dữ liệu của các tab con (`ks` cho Keys, `accounts` cho Accounts, `penalties` cho Penalties, v.v.) đều được gộp chung vào một phản hồi API duy nhất. Cách tiếp cận này giúp giảm thiểu tối đa overhead của giao thức HTTP so với việc mỗi tab tự động polling API riêng lẻ.

---

## 3. Thiết Kế Responsive & Thích Ứng Thiết Bị (Adaptive Layout)
Dashboard có khả năng hiển thị tối ưu trên các dải màn hình khác nhau (từ Mobile, Tablet đến Desktop rộng) thông qua các lớp CSS thích ứng trong [App.jsx](file:///d:/AI_Projects/router_api/frontend-src/src/App.jsx):

* **Sidebar Động (Adaptive Sidebar)**:
  * Trên Desktop (`lg` trở lên): Sidebar hiển thị dạng cột đứng tại cạnh trái màn hình, hiển thị đầy đủ menu điều hướng và thông tin phiên.
  * Trên Mobile/Tablet (dưới `lg`): Sidebar tự động gập lại và biến thành một thanh điều hướng ngang (`navbar`) nằm trên cùng của trang, hỗ trợ cuộn ngang (`overflow-x-auto whitespace-nowrap`) để người dùng dễ dàng chuyển đổi giữa các tab bằng một tay.
* **Header Thích Ứng (Fluid Header)**:
  * Các nút thông tin hệ thống (như Eggs Tracker, User Profile) sẽ tự động ẩn bớt nhãn chữ trên màn hình nhỏ và chỉ giữ lại icon để tránh việc các nút dính vào nhau hoặc tràn viền màn hình.

---

## 4. Cơ Chế Co Cỡ Chữ Linh Hoạt (Font Size Scaling)
Để hỗ trợ khả năng tiếp cận và nâng cao trải nghiệm đọc dữ liệu bảng số liệu dày đặc:
* Một bộ điều chỉnh cỡ chữ (Font Size Selector) được tích hợp trong header ([ThemeLanguageSelector.jsx](file:///d:/AI_Projects/router_api/frontend-src/src/components/ThemeLanguageSelector.jsx)).
* Khi người dùng thay đổi kích thước, hệ thống sẽ gán trực tiếp tỉ lệ phần trăm tương ứng vào thẻ căn bản của trình duyệt:
  ```javascript
  document.documentElement.style.fontSize = `${fontSize}%`;
  ```
* Vì toàn bộ ứng dụng sử dụng đơn vị đo lường tương đối `rem` (cho cả `margin`, `padding`, `width`, `height` và `text-size`), việc thay đổi cỡ chữ ở cấp độ `html` root sẽ tự động scale đồng đều toàn bộ tỷ lệ hiển thị của layout mà không làm méo mó cấu trúc giao diện.

---

## 5. Quản lý Auth Token (AccountsTab & MyAccountTab)

Sau đợt refactor token, một account **không còn mang một `auth_key` duy nhất** mà sở hữu nhiều token có hạn mức riêng. UI được xây lại theo đúng mô hình đó:

| Tầng | Thành phần | Phạm vi |
|---|---|---|
| Quản lý account | `AccountsTab.jsx` → `TokenTable` (`scope="admin"`) | Admin cấu / sửa / khoá / thu hồi token của bất kỳ account nào |
| Tự phục vụ | `MyAccountTab.jsx` → `TokenTable` (`scope="user"`) | User tạo và siết chặt token của chính mình |
| Cấp mã | `InvitePanel` trong `AccountsTab` | Admin phát mã 4 số cho đăng ký tự do |

### Vì sao có `scope`

Hai phía **cố ý bất đối xứng** — đây là ranh giới quyền, không phải chi tiết UI:

| | Admin | User |
|---|---|---|
| `max_concurrency` | ≤ 64 | ≤ 6 |
| `rpm` / `tpm` / `rpd` | tuỳ ý trong trần | ≤ hạn mức account |
| Token của account khác | được | 404 |

Form hiển thị khoảng cho phép ngay dưới ô nhập và validate lần nữa trước khi gửi — nhưng **server vẫn clamp lại**, vì một client tự viết có thể gửi con số bất kỳ. `test_dashboard_tokens.py` kiểm chứng đúng điều đó.

### `min_interval_seconds` không phải lúc nào cũng chạy

Field này **chỉ có tác dụng khi `max_concurrency = 1`**. Chạy 6 slot song song thì khoảng cách giữa các lần bắt đầu luôn nhỏ hơn interval, nên nó bị bỏ qua.

UI disable ô nhập và giải thích ngay tại chỗ thay vì im lặng bỏ qua — một field trông có tác dụng nhưng không có là cách nhanh nhất để khiến admin tốn công chỉnh hiệu suất. Backend trả `interval_effective` để client không phải tự suy luận.

### Token được tải theo yêu cầu

`/dashboard/accounts` không nhúng token của mọi account — payload sẽ nhân lên theo số account. Thay vào đó `AccountDetailPanel` chỉ gọi `/dashboard/admin/accounts/keys` cho **account đang được chọn**.

### Bố cục tab Accounts (master–detail)

Bản cũ là một bảng 10 cột `table-fixed`: cột "Hành động" chỉ rộng 8% mà chứa 7 nút icon không nhãn, token mở ra ở **dưới cùng** trang (xa chỗ bấm), và mọi kết quả đều là `alert()`. Bản mới tách theo ý định của admin:

| Vùng | File | Nội dung |
|---|---|---|
| Header | `AccountsTab.jsx` | Nút **Mã mời** và **Thêm** (cả hai là panel gập, không chiếm chỗ khi không dùng) |
| Danh sách (trái) | `AccountsTab.jsx` | Chip lọc tier kèm số đếm, tìm kiếm, lọc trạng thái, sắp xếp; mỗi dòng: avatar, tên, tier, chấm trạng thái, biểu tượng "đang yêu cầu đổi MK" |
| Chi tiết (phải) | `components/AccountDetailPanel.jsx` | Hạn mức → Auth tokens → Bảo mật & đăng nhập → Vùng nguy hiểm |

- Từ `lg` trở lên là 2 cột; dưới `lg` danh sách xếp trên chi tiết và chọn một dòng sẽ tự cuộn tới phần chi tiết.
- Lựa chọn được giữ qua các lần poll; nếu account đang chọn bị xoá hoặc bị lọc mất, panel rơi về dòng đầu tiên đang hiển thị.
- Kết quả thao tác hiện ở banner trong trang. **Secret (master key mới, token mới) hiện ở banner riêng không tự tắt** — trước đây chúng nằm trong `alert()` nên bấm nhầm là mất.
- Xoá và cấp mới master key vẫn qua `window.confirm` vì là thao tác không hoàn tác được.

---

## 5b. Tab My Account và routing URL

| Tab | URL |
|---|---|
| Accounts (admin) | `/stats/accounts` |
| My Account | `/stats/my-account` |
| API Reference | `/stats/help` |

Hai tab Accounts và My Account là **hai trang khác nhau**: một là quản lý toàn bộ account (admin), một là hồ sơ + token + hạn mức của chính người đăng nhập. Chúng dùng chung một route server (`/stats/{path}` luôn trả `index.html`) và phân biệt hoàn toàn ở phía client qua `getTabFromPath` / `getPathFromTab` trong `AppContext.jsx`.

### Vì sao My Account từng không mở được

`MyAccountTab.jsx` gọi `setResetCountdown(...)` trong một `useEffect` và đọc `resetCountdown` trong thẻ hạn mức, nhưng **`useState` của nó không còn tồn tại**. Commit `1b0a62a` (gỡ UI search-engine) xoá khối `wsLoading` và xoá luôn dòng `resetCountdown` nằm ngay cạnh. Build vẫn thành công vì thiếu khai báo chỉ là lỗi lúc chạy: tab ném `ReferenceError` khi mount, React gỡ cả cây component và người dùng thấy trang trống. Xem Bug #12 trong [bug-logs.md](bug-logs.md).

Các lỗi routing đi kèm đã sửa cùng lúc:
- `help` không có trong bản đồ URL nên bấm API Reference đẩy `/stats` và reload rơi về Overview.
- `md` (Models) không nằm trong danh sách tab chỉ-admin nên user thường vào được bằng URL.
- Poll 1.5s của My Account ghi đè `user` bằng `{name, tier}` và làm mất cờ `must_change_password`, khiến banner đổi mật khẩu biến mất sau lần poll đầu.


## 5c. Tab API Reference (`ApiHelpTab`)

Danh mục endpoint **không nằm trong bundle JS** — nó được server trả từ `GET /api/help`
(`src/server/openai_server/api_help.py`). Lý do: một chuỗi curl viết trong bundle sẽ âm thầm
sai đi khi route đổi tên, còn trên server nó nằm cạnh `tests/test_api_help.py` — test so mọi
path với bảng route thật của app, nên đổi tên route làm đỏ CI thay vì ship một ví dụ 404.

### Base URL lấy từ chính trang đang mở

Ví dụ curl dùng **origin mà dashboard được phục vụ**, không phải hằng số:

* `public_origin(request)` đọc `X-Forwarded-Proto` / `X-Forwarded-Host` trước, rồi tới header
  `Host`, rồi `request.url` — nên một instance sau nginx với `https://api.example.com` hiện
  đúng `https://api.example.com`, không phải `127.0.0.1:58100`.
* Giá trị host đi vào một chuỗi shell trong ví dụ, nên nó **được validate bằng regex chứ không
  escape**: header không khớp dạng hostname thì bị bỏ và rơi về nguồn kế tiếp. Không "sửa" một
  header lạ.
* Response trả kèm `base_url` để tab hiện một dòng "ví dụ bên dưới dùng địa chỉ bạn đang mở
  trang này" — người đọc thấy ngay vì sao nó khác tài liệu trên mạng.

### Nút Copy hoạt động trên cả HTTP thường

`navigator.clipboard` là `undefined` ngoài secure context (https, hoặc http trên localhost).
Dashboard này thường chạy http trên IP LAN, nên nút Copy trước đây "không bấm được": promise bị
`catch {}` nuốt, không copy, không phản hồi. Nay mọi chỗ copy đi qua `src/utils/clipboard.js`
(`copyText`) — thử Clipboard API, fallback về `textarea` + `execCommand`, và **trả boolean** để
UI báo được `Copy failed` thay vì im lặng.

Dùng chung ở: `ApiHelpTab`, `PasswordReveal`, `AccountDetailPanel`, `TokenTable`, `AccountsTab`,
`ModelAliasesTab`.

### Ngôn ngữ

Mỗi chuỗi trong catalog là map `{vi, en, ja}` và được resolve theo `?lang=` của request.
Tab refetch khi đổi ngôn ngữ (biến `lang` nằm trong dependency của `useEffect`), vì phần văn xuôi
và câu mẫu trong ví dụ (`"Xin chào"` / `"Hello"` / `"こんにちは"`) đều đến từ server — refetch để
trang không bị nửa Việt nửa Anh. `tests/test_api_help.py::TestTranslations` fail nếu một chuỗi
thiếu bản dịch.

---

## 5d. Hợp đồng i18n: chuỗi mới phải đi qua `t()`

`t(key, lang)` trong `src/utils/i18n.js` trả `TR[lang][key] || TR['vi'][key] || key`. Hệ quả:
**một key thiếu bản dịch sẽ hiện tiếng Việt, không hiện tên key** — nên lỗi này rất khó thấy khi
dev người Việt test. Vì vậy:

* Mọi chuỗi hiển thị phải gọi `t()`. Một literal viết thẳng trong JSX là chỗ rò tiếng Việt.
  `ALIAS_TEMPLATES` trong `ModelAliasesTab` từng như vậy: `label` vừa hiển thị vừa được gửi lên
  server, nên đổi sang EN vẫn thấy tiếng Việt *và* lưu luôn nhãn tiếng Việt vào alias. Nay nó
  giữ `label_key` / `description_key`, resolve ở cả chỗ render lẫn chỗ submit.
* Bộ lọc và id giữ tiếng Anh (`'claude'`, `'openai'`, `'custom'`), chỉ nhãn hiển thị đi qua `t()`
  — so sánh không được phụ thuộc ngôn ngữ.
* Thông báo lỗi phía server trả **tiếng Anh kèm `reason_code`**; client map code sang bản dịch
  và chỉ rơi về chuỗi tiếng Anh khi gặp code lạ. Xem `PasswordReveal`.
* Tên ngôn ngữ trong `ThemeLanguageSelector` (`Tiếng Việt`, `日本語`) là endonym — cố ý không dịch.

## 6. Bảng Dữ liệu Keys & Cơ Chế Khôi Phục Lỗi Tự Động
Tab quản lý API Keys ([KeysTab.jsx](file:///d:/AI_Projects/router_api/frontend-src/src/tabs/KeysTab.jsx)) được thiết kế lại để giải quyết triệt để lỗi chồng chéo chữ trên màn hình nhỏ:

### Thiết kế bảng không cố định (Flexible Table Layout):
* Loại bỏ class `table-fixed` để trình duyệt tự động tính toán kích thước cột dựa trên nội dung thực tế. **`table-fixed` vẫn bị cấm** — cột giãn theo nội dung là điều kiện để không cột nào bị ép chữ xuống dòng giữa một cụm id.
* Thiết lập `min-w-[...]` cho các cột quan trọng (ví dụ: Key Code tối thiểu `180px`, Status tối thiểu `140px`, Actions tối thiểu `96px`).
* Bọc bảng trong thẻ `overflow-x-auto`, đảm bảo khi chiều rộng màn hình nhỏ hơn tổng kích thước tối thiểu của các cột, bảng sẽ xuất hiện thanh cuộn ngang mượt mà thay vì co cụm và đè chữ lên nhau.

#### Bổ sung: cột định danh dính (`table-pin`)

Hệ quả trực tiếp của hợp đồng trên là cột **đầu tiên** — cột nói *dòng này là gì* (tên token, tên model, alias) — bị cuộn ra khỏi mép trái khi bảng rộng hơn khung chứa. Người dùng nhìn sang phải để đọc số liệu nhưng mất luôn nhãn của dòng đang đọc.

Class `table-pin` (định nghĩa trong `index.css`) giải quyết việc này mà **không phá hợp đồng**: vẫn `overflow-x-auto`, vẫn `min-w-*`, vẫn không `table-fixed` — chỉ đổi thứ tự vẽ.

* `th:first-child, td:first-child { position: sticky; left: 0 }` giữ cột định danh ở mép trái.
* `background` của ô dính buộc phải có `!important` vì quy tắc `.table th` phía trên đặt `transparent !important`; một ô sticky không nền sẽ để nội dung cuộn xuyên qua.
* `tbody tr:hover td:first-child` đổi nền theo `--c-surface-2` để hover trên cả dòng vẫn thấy đều.
* `box-shadow: 1px 0 0 var(--c-line)` vẽ một hairline ở mép phải cột dính, để nội dung trôi bên dưới đọc là "cuộn xuống dưới" chứ không phải "bị cắt".
* `padding-inline: 0.625rem` (thay vì `1rem` mặc định của daisyUI) chỉ áp cho `.table-pin`. Trên bảng 11 cột, 1rem mỗi bên tốn 32 px/cột — đó là phần lớn khoảng tràn. Các bảng đã vừa khung thì giữ padding rộng hơn.

`table-pin` hiện dùng cho bảng Tokens ở Accounts và bảng Model Config.

#### Ngân sách bề rộng của bảng Model Config

Bảng Model Config có 11 cột và **vừa khít** pane 1134 px ở 1440×900 — trước đây tràn 1314/1134. Ba thay đổi đưa nó về đích:

* **Gộp ALIAS + DISPLAY thành một ô định danh hai dòng** (tên hiển thị ở trên, alias bằng mono ở dưới). Ba cột trước đó là ba tên cho cùng một model; giờ còn hai — và cột upstream id vẫn giữ riêng vì với custom endpoint nó *thật sự* là một cái tên khác.
* Thu `min-w-[...]` của các cột số liệu về đúng nhu cầu nội dung, và giới hạn `<select>` chọn pool của custom endpoint ở `max-w-[6.5rem]`.
* Padding dày hơn ở mục `table-pin` phía trên.

Cột MODEL vẫn có thể cắt đuôi một id dài (`gemini-3.8-fla…`) — đó là chủ ý: nó luôn có `title` để rê chuột đọc đủ, và id đầy đủ nằm ngay trong ô ALIAS ở cột thứ nhất.

### Quản lý trạng thái Suy giảm & Cơ chế Tự động Hồi phục (Failure Decay):
* **Trạng thái Suy giảm (Degraded)**: Xảy ra khi một key dính lỗi liên tiếp từ 3 lần trở lên (`consecutive_failures >= 3`). Key bị dính lỗi nhiều thường do đụng hạn mức rate limit (HTTP 429) của gói miễn phí dưới tải cao.
* **Nút Reset thủ công**: Tích hợp nút `RefreshCw` kế bên mỗi key trên giao diện để quản trị viên có thể bấm xóa lỗi liên tiếp và giải phóng trạng thái cooldown của key đó ngay lập tức.
* **Cơ chế Hồi phục tự động (Starvation Prevention)**:
  * Do thuật toán chọn key của router (`Double Random`) chỉ bốc key từ Top 50% khỏe mạnh nhất, các key có chỉ số lỗi cao sẽ nằm ở Bottom 50% và bị "đói" yêu cầu (không bao giờ được gọi lại để chạy thành công và tự reset bộ đếm lỗi).
  * Backend đã bổ sung cơ chế tự động quét hồi phục: Nếu một key (hoặc một model của key đó) đã hết thời gian đóng băng và ở trạng thái rảnh rỗi (idle) trong **5 phút (300 giây)**, bộ đếm chỉ số lỗi liên tiếp sẽ tự động được reset về `0` cả trong bộ nhớ đệm lẫn database `usage.db`.
  * Cơ chế này đảm bảo các key gặp lỗi tạm thời sẽ luôn tự động quay trở lại hoạt động bình thường sau thời gian nghỉ ngơi mà không cần quản trị viên can thiệp.

---

## 7. Hệ thống Log Stream (Live Terminal) & Phân quyền Kênh Log theo Tier

Hệ thống Log Stream thời gian thực được xây dựng bằng xterm.js và WebSocket, cho phép theo dõi hoạt động của hệ thống ngay trên giao diện web.

### 7.1. Cấu trúc Component
* [LogTerminal.jsx](file:///d:/AI_Projects/router_api/frontend-src/src/components/LogTerminal.jsx): Khởi tạo instance terminal của `@xterm/xterm`, hỗ trợ auto-resize thông qua `@xterm/addon-fit`, syntax highlighting ANSI cho các cấp độ log (`ERROR`, `WARN`, `INFO`, `DEBUG`). Khi mount, component tự động gọi `/dashboard/logs/history` với header `X-Dashboard-Token` để lấy 200 dòng lịch sử gần nhất, đồng thời lắng nghe sự kiện WebSocket qua channel `log:<channel>`.
* [LogHistoryModal.jsx](file:///d:/AI_Projects/router_api/frontend-src/src/components/LogHistoryModal.jsx): Hộp thoại điều khiển stream, cho phép tạm dừng (Pause), xóa màn hình (Clear), tải file log về máy (Download), chọn kênh log và lọc theo endpoint cụ thể.

### 7.2. 5 Kênh Log & Phân quyền Phục vụ (Role-Based Access Control - RBAC)

| Kênh | File tương ứng | Nội dung | Quyền truy cập |
|---|---|---|---|
| `proxy` | `logs/proxy.log` | Chuyển đổi giao thức client (Anthropic / OpenAI), routing, response streams | **Tất cả (Admin, Free, Premium)** |
| `api` | `logs/api_calls.log` | Lịch sử gọi ra downstream API, latency, HTTP status | **Tất cả (Admin, Free, Premium)** |
| `system` | `logs/system.log` | Vận hành server FastAPI, lifecycle worker, memory, stats | **Chỉ Admin (`tier == "admin"`)** |
| `keys` | `logs/keys.log` | Xoay vòng key Gemini, trạng thái cooldown, rate limiter, masking | **Chỉ Admin (`tier == "admin"`)** |
| `web` | `logs/web.log` | Hoạt động đăng nhập, đổi mật khẩu, phân quyền dashboard | **Chỉ Admin (`tier == "admin"`)** |

### 7.3. Bảo mật Hai Tầng (Two-Tier Enforcement)
1. **Tầng HTTP API (`/dashboard/logs/history`)**:
   * Kiểm tra session qua `_require_dashboard`.
   * Nếu user thường truy vấn `keys`, `system`, hoặc `web` → trả về `403 Forbidden` (`Kênh log '...' chỉ dành cho Quản trị viên (Admin)`).
2. **Tầng WebSocket (`/dashboard/ws`)**:
   * Khi client gửi bản tin subscribe (`{"type": "subscribe", "channels": ["log:keys", ...]}`), server giải mã token kết nối và kiểm tra tier.
   * Nếu không phải admin, các kênh nhạy cảm bị từ chối đăng ký và server gửi lại bản tin `{ "type": "error", "message": "Permission denied for log channels: ..." }`.
3. **Phía Giao diện (Client-Side UX)**:
   * Dropdown chọn kênh trong [LogHistoryModal.jsx](file:///d:/AI_Projects/router_api/frontend-src/src/components/LogHistoryModal.jsx) tự động lọc bỏ các kênh quản trị khi người dùng không phải là admin, ngăn chặn việc hiển thị nhầm lẫn.

### 7.4. Cơ chế Buffer & Fallback Chống Mất Dữ Liệu
* **Pre-population**: Khi [LogWatcher.py](file:///d:/AI_Projects/router_api/src/server/realtime/log_watcher.py) khởi động, nó tự động đọc 1000 dòng đuôi file gần nhất từ đĩa để nạp vào buffer trong RAM. Khi user vừa mở modal, log lập tức hiển thị thay vì chờ phát sinh request mới.
* **Disk Tail Fallback**: Khi client yêu cầu số lượng dòng lớn hơn buffer hiện có trong RAM, server tự động đọc phần còn thiếu trực tiếp từ đĩa.
* **Kênh Chuẩn Hóa (`normalize_channel`)**: Hỗ trợ linh hoạt mọi định dạng channel như `proxy`, `log:proxy`, `proxy.log`, hoặc `log:proxy:endpoint` đảm bảo client và backend luôn đồng bộ.

---

## 8. Quản Lý Models & Pools (ModelsTab) & Đồng Bộ Hai Chiều ENV ↔ DB

Tab quản lý Model ([ModelsTab.jsx](file:///d:/AI_Projects/router_api/frontend-src/src/tabs/ModelsTab.jsx)) dành riêng cho Quản trị viên (`/stats/models`):

### 8.1. Hiển thị & Cấu hình Đa tầng
- **Trực quan hoá Pool & Members:** Hiển thị danh sách 6 model Flash (`gemini-flash-38` đến `gemini-flash-25`), 3 model Lite (`gemini-flash-35-lite`, `gemini-flash-lite`, `gemini-flash-25-lite`) và các custom endpoint gán vào pool.
- **Trạng thái lưu trữ (`in_db`):** Huy hiệu nhận biết model đang chạy theo mặc định từ ENV hay đã có bản ghi tuỳ biến lưu trong SQLite DB `model_config`.
- **Cấu hình giới hạn:** Cho phép admin chỉnh sửa trực tiếp `RPM`, `TPM`, `RPD`, `Context Length`, `Priority`, và trạng thái ẩn/hiện (`hidden`).

### 8.2. Cơ chế Đồng bộ Hai Chiều Thực tế (Two-Way Sync)
- **Khi Admin lưu trên WebUI (`/dashboard/admin/models/save`):**
  - Backend cập nhật dữ liệu vào bảng `model_config` của SQLite `usage.db`.
  - Tự động gọi `update_env_var()` để **ghi ngược lại các biến môi trường tương ứng vào file `.env`** trên đĩa.
  - Tự động tính toán lại tổng giới hạn của các virtual pool (`GEMINI_FLASH_RPM/TPM/RPD`) và đồng bộ vào `.env`.
- **Khi sửa file `.env` từ máy chủ:**
  - Background worker `_watch_env_file()` phát hiện thay đổi trong vòng 3 giây, tự động nạp lại `dotenv`, reload cấu hình bộ nhớ và gọi `sync_env_to_db()` để cập nhật đồng bộ xuống SQLite DB.

### 8.3. Panel "Thành viên Pool" — bật/tắt model con

Google yêu cầu paid tier cho một số model (Flash 3.7 / 3.8), còn key free chỉ tới 3.6. Panel này cho admin tắt chúng ở UI thay vì sửa `.env` rồi restart.

| Vùng | Nội dung |
|---|---|
| Header mỗi pool | Tên pool + badge `N/M hoạt động` |
| Mỗi member | Toggle bật/tắt, RPM, alias gạch ngang khi đang tắt |

Toggle gọi `POST /dashboard/admin/pools/toggle-member` và **áp dụng ngay không cần restart** — `reload_model_config()` xoá cache `ModelPool` nên member vừa tắt rời khỏi pool ở lượt request kế tiếp.

Route trả 400 nếu tắt member cuối cùng của một pool: pool rỗng nghĩa là `ModelPool.acquire()` hết member và mọi request vào pool alias sẽ `TimeoutError` sau 120s.

Bảng Models bên dưới có thêm cột **Trạng thái** để nhất quán với toggle — đọc `enabled` từ `GET /dashboard/admin/models`, nơi cũng trả `active_members` cho từng pool.

Cột **Alias** của bảng này là một ô định danh hai dòng: tên hiển thị ở trên, alias dùng khi gọi API ở dưới (mono). Trước đây alias và tên hiển thị là hai cột riêng, nhưng chúng gần như luôn nói cùng một model — gộp lại vừa đọc nhanh hơn vừa đủ chỗ để bảng 11 cột vừa khít pane. Chi tiết ngân sách bề rộng ở §6.

## 9. Trang trí nền & Easter egg

`CanvasParticles.jsx` vẽ toàn bộ trang trí nền bằng canvas, **không có DOM** — mọi thứ nằm sau `#particles-canvas` với `pointer-events: none`. Mỗi theme có một bộ hạt riêng:

| Theme | Nội dung nền | Thiên thể góc dưới-phải |
|---|---|---|
| `dark` | Sao nhấp nháy (90 hạt, ~20 % hình chữ thập) + sao băng thỉnh thoảng | Trăng lưỡi liềm |
| `light` | Chấm sáng nhẹ, không sao băng | Mặt trời vàng |
| `valentine` | Cánh hoa anh đào rơi, tự rụng từng cánh | Trăng lưỡi liềm |

Thiên thể đó **vừa là trang trí vừa là điểm bấm easter egg**: bấm 5 lần vào nó (mặt trời khi ở theme sáng, trăng khi ở theme tối) sẽ mở hiệu ứng tương ứng. Vị trí neo ở góc dưới-phải (`ORB_MARGIN_X = 96`, `ORB_MARGIN_Y = 84`) chứ không phải góc trên-phải như trước — chỗ cũ trùng đúng dải nút hành động của `PageHeader` và bị nút "Tải lại" cắt đôi trên năm tab. Bán kính vùng bấm là 45 px, tách rời khỏi bán kính vẽ, nên hoạ tiết có thể nhỏ mà vẫn dễ bấm.

Bộ đếm `Trứng N/5` trên thanh trên cùng mở bảng theo dõi đủ 5 egg (Konami, Savings, Sun, Moon, Claude) kèm gợi ý cho cái chưa tìm ra. Trạng thái lưu ở `localStorage._eggs`.



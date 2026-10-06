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


## 6. Bảng Dữ liệu Keys & Cơ Chế Khôi Phục Lỗi Tự Động
Tab quản lý API Keys ([KeysTab.jsx](file:///d:/AI_Projects/router_api/frontend-src/src/tabs/KeysTab.jsx)) được thiết kế lại để giải quyết triệt để lỗi chồng chéo chữ trên màn hình nhỏ:

### Thiết kế bảng không cố định (Flexible Table Layout):
* Loại bỏ class `table-fixed` để trình duyệt tự động tính toán kích thước cột dựa trên nội dung thực tế.
* Thiết lập `min-w-[...]` cho các cột quan trọng (ví dụ: Key Code tối thiểu `180px`, Status tối thiểu `140px`, Actions tối thiểu `96px`).
* Bọc bảng trong thẻ `overflow-x-auto`, đảm bảo khi chiều rộng màn hình nhỏ hơn tổng kích thước tối thiểu của các cột, bảng sẽ xuất hiện thanh cuộn ngang mượt mà thay vì co cụm và đè chữ lên nhau.

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
* **Pre-population**: Khi [LogWatcher.py](file:///d:/AI_Projects/router_api/src/server/log_watcher.py) khởi động, nó tự động đọc 1000 dòng đuôi file gần nhất từ đĩa để nạp vào buffer trong RAM. Khi user vừa mở modal, log lập tức hiển thị thay vì chờ phát sinh request mới.
* **Disk Tail Fallback**: Khi client yêu cầu số lượng dòng lớn hơn buffer hiện có trong RAM, server tự động đọc phần còn thiếu trực tiếp từ đĩa.
* **Kênh Chuẩn Hóa (`normalize_channel`)**: Hỗ trợ linh hoạt mọi định dạng channel như `proxy`, `log:proxy`, `proxy.log`, hoặc `log:proxy:endpoint` đảm bảo client và backend luôn đồng bộ.


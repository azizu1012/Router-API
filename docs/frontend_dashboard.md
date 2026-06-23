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
  * Trên Desktop (`lg` trở lên): Sidebar hiển thị dạng cột đứng ở cạnh trái màn hình, hiển thị đầy đủ menu điều hướng và thông tin phiên.
  * Trên Mobile/Tablet (dưới `lg`): Sidebar tự động gập lại và biến thành một thanh điều hướng ngang (`navbar`) nằm ở trên cùng của trang, hỗ trợ cuộn ngang (`overflow-x-auto whitespace-nowrap`) để người dùng dễ dàng chuyển đổi giữa các tab bằng một tay.
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

## 5. Bảng Dữ Liệu Keys & Cơ Chế Khôi Phục Lỗi Tự Động
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

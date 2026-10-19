# Router API — Tài Liệu Tính Năng Đã Gỡ Bỏ & Lưu Trữ (Deprecated & Archived Features)

Tài liệu này tổng hợp toàn bộ các tính năng, cơ chế, kiến trúc và đề xuất trước đây đã bị **gỡ bỏ**, **thay thế hoàn toàn** hoặc **tạm dừng triển khai**. Mục đích là giữ cho các tài liệu kiến trúc chính (`architecture_overview.md`, `routing_and_resilience.md`, `model_aliases_feature.md`) luôn tinh gọn, chính xác 100% với mã nguồn đang chạy, không để thông tin cũ và mới bị chồng chéo hay xung đột.

---

## 1. Google Search Grounding & Gemini Native Search Tool

### Trạng thái: Đã gỡ bỏ hoàn toàn (Deprecated & Removed)
### Thay thế bằng: In-process DuckDuckGo (DDGS) Scraper + MCP Server (`/mcp`)

* **Lý do gỡ bỏ**:
  1. **Độ trễ quá lớn (High Latency)**: Cơ chế Google Search Grounding qua Gemini API thường mất từ 5–15 giây cho một lượt gọi, gây nghẽn nghiêm trọng cho các AI Agent tương tác liên tục (như Claude Code hay OpenCode).
  2. **Tiêu hao cạn kiệt Quota**: Mỗi lần search qua Google model tiêu tốn 1 lượt request và lượng token khổng lồ từ pool API Key của Gemini, dễ gây ra bão 429 trên toàn hệ thống.
  3. **Không hỗ trợ tùy biến tham số**: Client không thể can thiệp vào các tham số tìm kiếm, giới hạn ký tự bằng chứng hoặc trích xuất DOM có cấu trúc.
* **Cơ chế hiện tại**:
  - Toàn bộ pipeline tìm kiếm web nội bộ chuyển sang sử dụng **DuckDuckGo (DDGS)** chạy hoàn toàn in-process (`src/tools/search/`).
  - Tiết kiệm 100% quota key Gemini, tốc độ phản hồi tính bằng mili-giây, trích xuất Markdown có cấu trúc (bảng, danh sách, khối code) và tính điểm xếp hạng độ tin cậy nguồn tin theo tiêu chuẩn Exa.
  - Nâng cấp thêm **MCP Server (`/mcp`)** chuẩn Model Context Protocol để client tự chủ động cấu hình sâu tham số tìm kiếm và ngữ cảnh.

---

## 2. Ghi Đè Search Engine Cấp Tài Khoản (`account.get("search_engine")`)

### Trạng thái: Đã gỡ bỏ (Deprecated & Removed)
### Thay thế bằng: Phân giải nghiêm ngặt theo Dialect và Request Payload

* **Lý do gỡ bỏ**:
  - Trước đây, cấu hình `search_engine` lưu trong bảng `accounts` của SQLite từng nằm ở mức ưu tiên giữa payload client và mặc định hệ thống.
  - Tuy nhiên, giao diện Dashboard không hề có nút cập nhật trường này, dẫn đến việc các row cũ bị ghim giá trị `'disabled'` sẽ âm thầm đè bẹp (override) request của client dù client đã cố ý truyền `web_search: true`.
* **Cơ chế hiện tại**:
  - Chỉ có 3 mức phân giải rõ ràng (được kiểm chứng bởi `tests/test_search_engine_precedence.py`):
    1. Client chỉ định rõ `search_engine` trong request body → Tôn trọng yêu cầu client.
    2. Request dạng hosted-search (`/v1/responses` với `tools: [{"type": "web_search"}]`) → Chạy tìm kiếm nội bộ Router trước.
    3. Mọi request khác → Mặc định chạy DuckDuckGo nội bộ không tốn quota Gemini.

---

## 3. Kiến Trúc Một Key Duy Nhất Cho Một Tài Khoản (`accounts.auth_key`)

### Trạng thái: Đã thay thế (Superseded)
### Thay thế bằng: Structured Multi-Token (`account_keys`)

* **Lý do thay thế**:
  - Trước đây, một tài khoản chỉ sở hữu duy nhất 1 chuỗi `auth_key`. Khi muốn giới hạn quyền cho từng thiết bị hoặc từng sub-agent khác nhau, admin phải tạo nhiều account riêng biệt.
  - Không thể cấp hạn mức độc lập (RPM, TPM, RPD, Concurrency) cho từng môi trường làm việc của cùng một người dùng.
* **Cơ chế hiện tại**:
  - Mỗi account sở hữu nhiều token dạng `sk-<name>-<6 ký tự>` lưu trong bảng `account_keys`.
  - Mỗi token có 4 lớp bảo vệ hạn mức độc lập (Concurrency, RPM, TPM, RPD).
  - Cột `accounts.auth_key` cũ được giữ lại độc quyền làm **Master Key** (chuỗi 46 ký tự `sk-<43 ký tự urlsafe>`) — miễn trừ mọi rào cản quota để phục vụ công tác cứu hộ, quản trị hệ thống.

---

## 4. Các Mô Hình Gemini Cũ Đã Bị Khai Tử (Sunsetted Models: 2.0, 2.5, 3.5 Flash Standard, 3.7)

### Trạng thái: Đã ngừng hoạt động (Sunsetted / Discontinued)

Các model sau đã bị Google Developer API chính thức khai tử hoặc bị router loại bỏ vĩnh viễn khỏi danh sách phục vụ:
* `gemini-2.0-flash` & `gemini-2.0-flash-lite`: Khai tử hoàn toàn bởi Google.
* `gemini-2.5-flash` & `gemini-2.5-flash-lite`: Khai tử theo mốc thời gian Google Developer API (sunset date 2026-10-16).
* `gemini-3.5-flash` (`gemini-flash-35`, bản standard/thường): Bản preview thử nghiệm trung gian của Gemini thế hệ 3, đã chính thức khai tử và ngừng hỗ trợ. *(Lưu ý: Bản `gemini-flash-35-lite` vẫn giữ lại hoạt động bình thường trong pool Flash Lite).*
* `gemini-3.7-flash` (`gemini-flash-37`): Model thường xuyên gặp lỗi 503 "High Demand" upstream từ Google, bị áp đặt giới hạn tài khoản trả phí (Paid Tier) khắt khe gây lỗi liên tục cho key free; đã được khai tử và gỡ bỏ hoàn toàn khỏi pool routing.

### Cơ chế kiểm soát kỹ thuật trong Router:
Hệ thống sử dụng cơ chế kiểm tra tập trung `is_model_sunset(alias_or_model_id)` tại `src/core/config/api_config.py` để chủ động bảo vệ:
1. **Lọc thành viên Pool (`active_pool_members`)**: Tự động loại bỏ toàn bộ các model khai tử khỏi danh sách xoay vòng của `gemini-flash` và `gemini-flash-lite`.
2. **Cấp phát Key (`reserve_key`)**: Chặn ngay từ đầu nếu request chỉ định model đã khai tử, log warning và trả về `None`.
3. **Phân giải Model (`_resolve_model`)**: Trả mã lỗi `HTTP 400 Bad Request` kèm message thông báo `"Model '<alias>' has been discontinued."`
4. **Danh mục Model (`list_models`)**: Ẩn hoàn toàn khỏi danh sách trả về qua endpoint `/v1/models`.

Router API hiện tập trung toàn bộ năng lực định tuyến vào các model thế hệ 3.x còn hiệu lực:
* **Flash Pool**: `gemini-flash-38`, `gemini-flash-36`, `gemini-flash-30`.
* **Flash Lite Pool**: `gemini-flash-35-lite`, `gemini-flash-lite` (`gemini-3.1-flash-lite`).

---

## 5. Bảng Giá Mô Phỏng Cũ Trong `model_prices`

### Trạng thái: Đã cập nhật và xóa bỏ giá ảo

* **Lý do**:
  - Trong các phiên bản ban đầu, bảng `model_prices` sử dụng mức giá cứng mô phỏng cũ ($0.0025 / $0.01 per 1k tokens).
* **Cơ chế hiện tại**:
  - Khi khởi động, hệ thống tự động kiểm tra và đồng bộ lại mức giá thực tế theo niêm yết của Google Developer API (ví dụ: Flash 3.x là $0.00015 input / $0.00060 output per 1k tokens).

---

## 6. Các Đề Xuất Chưa Triển Khai Của Model Aliases (Unimplemented / Deferred Proposals)

### Trạng thái: Đề xuất lưu trữ (Không có trong mã nguồn hiện tại)

Tài liệu gốc `docs/model_aliases_feature.md` từng liệt kê một số ý tưởng mở rộng ở mục "Future Enhancements". Các tính năng này **chưa từng được code** và được tách riêng về đây để tránh gây hiểu lầm rằng hệ thống đã hỗ trợ:
1. **Regex Aliases (Wildcards)**: Khớp mẫu tên model dạng `"gpt-4*"`. Hiện tại router chỉ so khớp chuỗi tuyệt đối (`alias_name = ?`).
2. **Conditional Routing**: Định tuyến dựa trên điều kiện thuộc tính request (ví dụ: `max_tokens_gt: 8000` thì tự đổi sang model context dài).
3. **Alias Chains**: Cho phép alias trỏ lồng nhau (`my-model` → `gpt-4` → `gemini-flash`). Hiện tại router chỉ resolve 1 bước.
4. **Cột theo dõi & Hết hạn**: Các trường `usage_count`, `last_used_at`, `expires_at` trong bảng `model_aliases`.
5. **Cột Audit**: `created_by` và `updated_by` trong bảng `model_aliases`.

---

## 7. Giả Lập Lỗi HTTP 200 SSE Cho Main Agent (Defensive 200 OK Anti-pattern)

### Trạng thái: Đã gỡ bỏ cho luồng Main Agent

* **Lý do gỡ bỏ**:
  - Trước đây, khi gặp lỗi 429 hoặc 503, proxy bắt ngoại lệ và trả lời client bằng chuỗi SSE text `⚠️ [Hệ thống quá tải tạm thời]...` ở mã trạng thái HTTP 200 OK.
  - Điều này khiến các Autonomous Agent (như Claude Code CLI) tưởng nhầm đây là câu trả lời của mô hình, nhồi nhét đoạn lỗi vào prompt history, gây bẩn context và lặp vô hạn.
* **Cơ chế hiện tại**:
  - Phân tách luồng triệt để: Main Agent nhận đúng mã HTTP 429/503 để client tự backoff retry tự nhiên.
  - Chỉ có Sub-Agent chạy ngầm song song mới được áp dụng cơ chế bắt lỗi mô phỏng text 200 OK để tránh crash CLI ngoài ý muốn.

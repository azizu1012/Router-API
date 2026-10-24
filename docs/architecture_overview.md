# Router API v2 — Kiến Trúc Tổng Quan & Quyết Định Thiết Kế

## 1. Giới Thiệu

Router API v2 là một hệ thống gateway thông minh được thiết kế để quản lý và định tuyến các yêu cầu đến nhiều nhà cung cấp Mô hình Ngôn ngữ Lớn (LLM) khác nhau (như Gemini, Claude, và các Custom Endpoints). Mục tiêu chính là cung cấp một lớp trừu tượng (abstraction layer) vững chắc, đảm bảo tính khả dụng cao, khả năng chống chịu lỗi (resilience) và phân phối tải hiệu quả, ngay cả dưới áp lực cao.

Hệ thống được phát triển với sự hỗ trợ của AI và tái sử dụng mã nguồn, dẫn đến một số quyết định thiết kế độc đáo nhằm ưu tiên tính thực chiến và tốc độ triển khai.

## 2. Cấu Trúc Dự Án

```text
router_api/
├── .env                          # Gemini keys + model config
├── .env.example                  # Template for env file config
├── README.md                     # Quick start + client config
├── docs/                         # Thư mục chứa tài liệu kiến trúc
│   ├── architecture_overview.md  # File kiến trúc này
│   └── routing_and_resilience.md # Chi tiết cơ chế chống lỗi
├── DEPLOY_DOMAIN.md              # Caddy và Nginx reverse proxy deployment guide
├── AGENTS.md                     # OpenCode agent task management instructions
├── CLAUDE.md                     # Claude Code developer instructions
├── requirements.txt              # Project Python dependencies
├── main.py                       # Uvicorn startup script với auto port-freeing
├── usage.db                      # SQLite config DB (accounts, endpoints, key_status, key_penalties)
├── usage_logs.db                 # SQLite telemetry DB for token tracking
├── logs/                         # Rotating file logs (daily auto-clean)
└── src/                          # Mã nguồn Python chính
    ├── api/                      # Các Proxy chuyển đổi định dạng
    │   ├── claude_proxy/         # Anthropic→Gemini proxy
    │   └── opencode_proxy/       # OpenCode→Gemini proxy (OpenAI-compatible)
    ├── logical_HQ_translator/    # Compression, messages, model resolution, normalization, streaming
    ├── core/                     # Các module cốt lõi của Router
    │   ├── config/               # Cấu hình API & .env (api_config.py)
    │   ├── orchestration/        # TRUNG TÂM: pool loop, key rotation, quota, retry (pool_manager.py)
    │   ├── config_n_logg/        # Cấu hình & Ghi nhật ký
    │   ├── accounts/             # Quản lý tài khoản
    │   ├── limits/               # Giới hạn Rate Limit, Account, Token (gemini/limiter.py)
    │   ├── providers/            # Gemini (facade, manager, caller), Custom Endpoints
    │   ├── runtime/              # Tool cache, sse format, request_context (ContextVar)
    │   └── router/               # APIRouter, ModelPool, KeyResolver
    ├── backend/                  # Tầng DB SQLite (accounts/, endpoints/, keys/, models/)
    ├── console/                  # CLI admin (cli.py, interactive/)
    └── server/                   # FastAPI Server, routes, realtime (log_watcher, websocket, stats)
```

## 3. Các Thành Phần Chính & Vai Trò

### 3.1. APIRouter (`src/core/router/core/router.py`)
*   **Vai trò**: Là trái tim của hệ thống định tuyến và quản lý tài nguyên. `APIRouter` là một Singleton, đảm bảo toàn bộ ứng dụng sử dụng cùng một thể hiện để quản lý trạng thái toàn cục của các API Key và Model.
*   **Trạng thái Key In-memory (`_key_status`):** `APIRouter` duy trì một dictionary `_key_status` trong bộ nhớ, chứa trạng thái chi tiết của tất cả các API Key (enabled, usage, active requests, frozen_until, consecutive_failures, last_success, per_model failures/frozen_until). Đây là nguồn dữ liệu chính cho các quyết định định tuyến trong thời gian chạy.
    *   **Khởi tạo:** Khi khởi động, `_key_status` được tải từ cơ sở dữ liệu SQLite (`src/backend/keys/status.py`) và hợp nhất với trạng thái mặc định cho các key mới hoặc chưa đầy đủ thông tin.
    *   **Đồng bộ hóa:** Mọi cập nhật trạng thái key (ví dụ: `acquire_key`, `record_success`, `record_failure`, `freeze_key`, `release_key`) sẽ thay đổi trạng thái in-memory và sau đó kích hoạt các thao tác ghi **bất đồng bộ** vào SQLite thông qua các hàm `atomic_reserve_key`, `atomic_release_key`, `atomic_freeze_key`, `atomic_record_success` trong `src/backend/keys/status.py`. Điều này đảm bảo tính bền vững của dữ liệu mà không làm chặn luồng xử lý chính.
*   **Chọn Model**: Chịu trách nhiệm chọn mô hình phù hợp nhất dựa trên ưu tiên, tình trạng sức khỏe và giới hạn.
*   **Vòng Đời Key Reservation (`reservation_id`) & Idempotent Release**: `reserve_key` cấp phát một token `reservation_id` duy nhất và lưu vào dictionary `_reservations`. `release_key` chỉ giải phóng đúng 1 lần cho mỗi `reservation_id`, triệt tiêu hoàn toàn lỗi underflow hoặc kẹt giữ chỗ `active_requests` (nguyên nhân từng gây delay 1s–3s ở hot path). Tự động dọn dẹp các reservation hết hạn quá 1800s.
*   **Truy Vết Chuyển Đổi Trạng Thái (Authoritative Transitions)**: `APIRouter.record_attempt_failure()` đóng vai trò là cửa ngõ duy nhất ghi nhận lỗi thất bại của key, ghi log và lưu vết có cấu trúc vào bảng `key_transitions` (kèm `transition_id`, `request_id`, `key_suffix`, `duration_ms`, `error_type`).
*   **Phân Lập Phạm Vi Lỗi (Scoped Failure Handling)**: Các lỗi cục bộ của key (`invalid_key`, `permission_denied`) chỉ đóng băng duy nhất key vi phạm. `update_model_health` chỉ được gọi khi gặp lỗi cấp hạ tầng/nhà cung cấp, bảo vệ điểm sức khỏe của Model Alias luôn đạt 100%.
*   **Emergency Freeze Bảo Vệ**: `freeze_all_keys()` bị hạn chế ở cấp cứu hộ khẩn cấp, bắt buộc truyền `actor` và `reason` rõ ràng.
*   **Giới hạn tốc độ**: Tương tác với `src/core/limits/gemini/limiter.py` để thực thi các giới hạn RPM/TPM.
*   **Theo dõi sức khỏe Model**: `update_model_health` điều chỉnh điểm số của mô hình dựa trên thành công/thất bại.

### 3.2. PoolManager (`src/core/orchestration/pool_manager.py`)
*   **Vai trò**: Điều phối vòng lặp xoay vòng (rotation) qua các pool member, quản lý slot concurrency qua `ModelPool`, và thực hiện retry khi gặp lỗi tạm thời. Đây là module trung tâm xử lý logic retry, failover và quản lý pool một cách "monolithic" (tập trung). Nó là thành phần điều phối chính trong việc lựa chọn key/model, quản lý quota và xử lý vòng lặp retry.
*   **Chức năng chính**:
    *   **Vòng lặp Pool**: Chứa logic chính cho việc thử lại và xoay vòng qua các thành viên trong pool (ví dụ: các phiên bản khác nhau của Gemini Flash).
    *   **Slot Concurrency**: Sử dụng `ModelPool` (`src/core/router/pool.py`) để quản lý slot-based concurrent workers — `acquire`/`release` với `asyncio.Lock()` cho mỗi member. Model ngoài `MODEL_POOLS` chạy standalone (không qua acquire/release).
    *   **Phân loại lỗi**: Sử dụng `_classify_error` để phân loại các loại lỗi khác nhau từ các nhà cung cấp LLM.
    *   **Gọi API thống nhất**: Cung cấp các phương thức `call_nonstream` và `call_stream` để thực hiện các cuộc gọi API đến LLM, xử lý logic chọn key và retry.

### 3.3. KeyResolverMixin (`src/core/router/core/key_resolver.py`)
*   **Vai trò**: Cung cấp logic để giải quyết (resolve) các API Key, bao gồm các cơ chế như Circuit Breaker và Adaptive Cooldown. Được thiết kế dưới dạng Mixin để `APIRouter` có thể kế thừa các chức năng này.
*   **Chức năng chính**:
    *   **Circuit Breaker**: Kiểm tra trạng thái đóng/mở của circuit cho từng key để tránh gửi yêu cầu đến các key bị lỗi liên tục.
    *   **Adaptive Cooldown**: Tính toán thời gian đóng băng key dựa trên lý do lỗi và số lần thất bại liên tiếp.
    *   **Chọn Key**: Chứa logic phức tạp để chọn key phù hợp nhất từ các key khả dụng, bao gồm cả thuật toán Double Random.

### 3.4. Proxy Layer (`src/api/claude_proxy/`, `src/api/opencode_proxy/`)
*   **Vai trò**: Các proxy này (ClaudeProxy và OpenCodeProxy) chịu trách nhiệm chuyển đổi định dạng yêu cầu/phản hồi giữa các client (ví dụ: Claude Code, OpenCode) và định dạng nội bộ của Router API, sau đó ủy quyền cho `PoolManager` để xử lý logic cốt lõi.
*   **Chức năng chính**:
    *   **Chuyển đổi Payload**: Chuyển đổi định dạng yêu cầu đầu vào sang định dạng chung và chuyển đổi phản hồi từ LLM về định dạng mong muốn của client.
    *   **Xử lý Stream**: Cung cấp các executor riêng cho luồng stream và non-stream.

### 3.5. Backend (`src/backend/`)
*   **Vai trò**: Lớp truy cập cơ sở dữ liệu SQLite.
*   **Chức năng chính**:
    *   **`_db.py`**: Quản lý kết nối DB dùng chung, **và tạo các bảng đọc lúc import**. Khởi tạo bootstrap DDL chuẩn xác đồng bộ với `schema.py`.
    *   **`schema.py`**: Schema đầy đủ + bộ tự động `ALTER TABLE` migration qua `_SCHEMA_COLUMN_REGISTRY` (tự động thêm cột mới như `last_transition_*` của `key_status`, bảng `key_transitions`, `endpoint_transitions`), chạy trong startup hook.
    *   **`keys/status.py`**: Các thao tác nguyên tử (atomic) để cập nhật trạng thái key (reserve, release, freeze, disable), lưu vết chuyển đổi trạng thái (`record_key_transition_db`), và truy vấn lịch sử chuyển đổi (`query_key_transitions_db`, `query_endpoint_transitions_db`).
    *   **`accounts.py`, `endpoints.py`, `model_prices.py`**: CRUD cho tài khoản, custom endpoints và giá model thực tế chuẩn Google Gemini API (với cơ chế auto-migration loại bỏ giá ảo cũ).
    *   **`model_config.py`**: Quản lý cấu hình per-model trong bảng `model_config` (RPM, TPM, RPD, pool_name, priority) và cung cấp hàm đồng bộ hai chiều `sync_env_to_db()`.
    *   **`account_keys.py`**: Auth token theo `sk-<name>-<6 ký tự>`, web credential, invite code.

#### Cơ chế đồng bộ hai chiều ENV ↔ DB (Two-Way Synchronization)

Hệ thống thiết lập nguyên tắc: **`.env` là Source of Truth chính cho runtime, Database SQLite (`usage.db`) là nơi lưu trữ trạng thái và phục vụ Admin WebUI**:
- **Chiều `.env` → DB:** Khi file `.env` được chỉnh sửa, background watcher (`_watch_env_file` trong `app_init.py`) phát hiện file change, reload `os.environ`, cập nhật `AVAILABLE_MODELS` và tự động gọi `sync_env_to_db()` để ghi đè cập nhật vào bảng `model_config`.
- **Chiều DB → `.env`:** Khi Admin chỉnh sửa cấu hình model trên Dashboard UI (`/dashboard/admin/models/save`), backend lưu vào DB, gọi `update_env_var()` để ghi ngược lại các biến `GEMINI_*_RPM/TPM/RPD/MODEL` vào file `.env`, đồng thời tự động cập nhật lại aggregate limits của các virtual pool (`GEMINI_FLASH_*`).


#### Vì sao `_db.py` cũng phải tạo bảng & Cơ chế Tự động Nâng cấp DB (Auto-Migration)

`APIRouter` query `keys/status` **ngay lúc nó được khởi tạo**, và `custom_endpoint_manager` dựng cache ở module level — cả hai đều xảy ra trong lúc import, trước mọi startup hook. Một máy chưa có `usage.db` sẽ fail `no such table: key_status` trước khi app kịp tồn tại.

Vì vậy `_db.conn()` tạo sẵn hai bảng đó ngay lần mở connection đầu tiên. Ranh giới này **có thật** và dễ vỡ: `CREATE TABLE IF NOT EXISTS` không sửa được bảng đã có sai shape, nên nếu định nghĩa ở `_db.py` lệch với `schema.py` thì bản bootstrap thắng, `CREATE` của `schema.py` thành no-op, và bất kỳ cột nào không có `ALTER` migration sẽ không bao giờ được tạo. `tests/test_schema_bootstrap.py` so DDL của hai bên để chặn điều đó.

**Cơ chế Tự động Migrate & Nâng cấp DB (`auto_migrate_schema`):**
Thay vì yêu cầu người dùng hoặc quản trị viên phải chạy các tệp script migration riêng lẻ bên ngoài mỗi khi codebase cập nhật, hàm `init_config_tables()` trong `src/backend/schema.py` được tích hợp bộ tự động phát hiện và đồng bộ schema:
1. Duyệt qua bảng đăng ký cột chuẩn (`_SCHEMA_COLUMN_REGISTRY`).
2. Quét cấu trúc bảng thực tế trong SQLite bằng `PRAGMA table_info(table_name)`.
3. Tự động sinh và thực thi câu lệnh `ALTER TABLE {table_name} ADD COLUMN ...` cho bất kỳ cột nào còn thiếu trên database hiện có.
4. Quá trình diễn ra hoàn toàn tự động, nguyên tử và idempotent ngay khi server khởi động.

`main.py` gọi `init_config_tables()` trước khi bind port, nên lỗi schema nổi ra lúc khởi động chứ không phải trên request đầu tiên.

### 3.6. Logical HQ Translator (`src/logical_HQ_translator/`)
*   **Vai trò**: Chứa các bộ chuyển đổi và tiện ích dùng chung giữa các proxy, đặc biệt là liên quan đến việc xử lý message, định dạng và các công cụ hỗ trợ AI Agent.
*   **Chức năng chính**:
    *   **`model/`**: Giải quyết bí danh mô hình, custom endpoint và quản lý việc lấy key.
    *   **`messages/`**: Chuyển đổi message giữa các protocol và cắt context khi vượt giới hạn.
    *   **`normalization/`**: Chuẩn hóa văn bản streaming và trích xuất XML thinking.
    *   **`streaming/`**: Tiện ích SSE, cache usage mô phỏng và đồng bộ model theo session.
    *   **`compression/`**: Lọc và định dạng output của các công cụ (git diff, status, ls, grep) cho AI Agent.

### 3.7. Providers (`src/core/providers/`)
*   **Vai trò**: Cung cấp các giao diện thống nhất để tương tác với các nhà cung cấp LLM khác nhau và quản lý custom endpoints.
*   **Chức năng chính**:
    *   **`gemini/`**: Chứa toàn bộ provider Gemini theo domain (facade, manager, caller, request/response format, pool, error classification và thinking).
    *   **`custom_endpoint/manager.py`**: Quản lý các custom endpoints (CRUD, pool, health).

### 3.8. ModelPool (`src/core/router/pool.py`)
*   **Vai trò**: Quản lý slot-based concurrent workers cho pool model. Là Singleton per pool (dùng `get_or_create`), đảm bảo mỗi pool member chỉ xử lý một request tại một thời điểm thông qua `asyncio.Lock()`.
*   **Chức năng chính**:
    *   **`acquire(skip, timeout)`**: Chọn một member rảnh rỗi. Ưu tiên custom endpoint members trước, sau đó fallback về Gemini members. Nếu tất cả đều busy, chờ 50ms rồi thử lại đến `timeout` giây.
    *   **`release(member)`**: Giải phóng lock của member, cho phép request khác sử dụng.
    *   **`sync_custom_members()`**: Thêm custom endpoint members vào pool đã tồn tại (xử lý cache lạnh khi config thay đổi sau khi pool được khởi tạo).
*   **Tích hợp với PoolManager**: `PoolManager` gọi `ModelPool.acquire()` trước khi gửi request qua pool member, và `release()` trong `finally` block để đảm bảo luôn giải phóng slot.

### 3.9. Giao Thức Từ Provider Tới Endpoint

Hệ thống Router API được thiết kế như một lớp trừu tượng, chấp nhận các định dạng yêu cầu khác nhau (OpenAI-compatible, Anthropic-like) và định tuyến chúng đến các endpoint backend (Gemini native, Custom Endpoint) với các cơ chế chuyển đổi định dạng rõ ràng.

*   **OpenCode Proxy (`src/api/opencode_proxy/handler/proxy.py`):**
    *   **Vai trò:** Chuyển đổi định dạng yêu cầu tương thích OpenAI (từ OpenCode) sang định dạng tương thích Gemini.
    *   **Chức năng:** Chuẩn bị tin nhắn, chèn công cụ tìm kiếm web (`WebSearch`) nếu được kích hoạt, định dạng phản hồi từ Gemini trở lại định dạng OpenCode.
    *   **Ủy quyền:** Tất cả logic cốt lõi (quản lý key, pool, retry, quota) được ủy quyền cho `PoolManager`.

*   **Claude Proxy (`src/api/claude_proxy/handler/proxy.py`):**
    *   **Vai trò:** Chuyển đổi định dạng yêu cầu giống Anthropic (từ Claude Code) sang định dạng tương thích Gemini.
    *   **Chức năng:** Chuẩn bị tin nhắn, định dạng phản hồi từ Gemini trở lại định dạng giống Claude.
    *   **Ủy quyền:** Tất cả logic cốt lõi được ủy quyền cho `PoolManager`.

*   **Gemini Native Endpoint:**
    *   Các yêu cầu được định tuyến trực tiếp đến API Gemini thông qua `src/core/providers/gemini/facade.py`.
    *   `gemini/facade.py` chịu trách nhiệm tương tác với Gemini SDK hoặc các endpoint HTTP của Gemini, và chuẩn hóa phản hồi.

*   **Custom Endpoint:**
    *   Các Custom Endpoint được quản lý bởi `src/core/providers/custom_endpoint/manager.py`.
    *   **Quản lý (CRUD):** Các endpoint có thể được thêm, cập nhật, xóa, bật/tắt thông qua các hàm trong `src/backend/endpoints/manager.py`. Điều này cho phép quản lý linh hoạt các nhà cung cấp LLM bên ngoài.
    *   **Kiểm tra sức khỏe (Health Checks):** `CustomEndpointManager` thực hiện các kiểm tra sức khỏe định kỳ (`ping_endpoint`) đến `/models` endpoint của Custom Endpoint. Kết quả ping được lưu vào bộ nhớ đệm (`_ping_cache`) để tránh gọi API liên tục.
    *   **Circuit Breaker:** Một cơ chế circuit breaker (`_circuit_breaker`) được triển khai để đóng băng tạm thời các Custom Endpoint bị lỗi liên tiếp. Thời gian đóng băng sẽ tăng lên theo số lần lỗi, giúp hệ thống không gửi yêu cầu đến các endpoint không ổn định.
    *   **Phát hiện Model:** Hàm `fetch_models` tự động khám phá các model có sẵn từ Custom Endpoint bằng cách gọi API `/models` của nó và cập nhật vào cấu hình của endpoint.
    *   **Gán cho Pool/Tài khoản:** Custom endpoint là first-class pool member thông qua `pool_assignments`. Khi gán endpoint vào pool (VD: `endpoint assign my-provider pool gemini-flash:flash`), model của endpoint trở thành member của pool đó, được ModelPool quản lý slot concurrency như các Gemini members khác. Nếu model không có trong `MODEL_POOLS`, endpoint fallback về standalone mode (không qua pool acquire/release).
    *   **Xử lý Payload khi Fallback:** Khi một yêu cầu ban đầu đi qua các Proxy Layer (ClaudeProxy hoặc OpenCodeProxy), nó đã được dịch về một định dạng payload nội bộ dùng chung. Nếu `PoolManager` chọn một Custom Endpoint và sau đó Custom Endpoint này báo lỗi và hệ thống fallback về bể API Key của Gemini nội bộ, `GeminiFacade` (`src/core/providers/gemini/facade.py`) sẽ nhận lại đúng payload gốc của hệ thống. `GeminiFacade` chịu trách nhiệm tái cấu trúc payload (Strip/Convert các tham số đặc thù của OpenAI như `response_format` hay cấu trúc `tools` dạng function call) sang cấu hình tương thích với Gemini SDK/HTTP native một cách tự động. Logic dispatch được chia trong package `gemini/`, với `facade.py` làm entrypoint và các module request/response riêng, đảm bảo cấu trúc `tool_calls` và các tham số khác được dịch đúng cách.

### 3.10. Web Search & Evidence Pipeline (`src/tools/` & `src/api/opencode_proxy/handler/`)
*   **Vai trò:** Cung cấp khả năng tìm kiếm web grounded (`search_with_citations`, `web_search`) tốc độ cao, trích dẫn chính xác nguồn tin và bằng chứng chi tiết mà không phụ thuộc vào dịch vụ trả phí bên ngoài.
*   **Thứ tự ưu tiên công cụ tìm kiếm (Engine Precedence):**
    *   DuckDuckGo: Local search qua DDGS và tải trang bất đồng bộ song song (`asyncio.gather`), không tiêu tốn quota key Gemini.
*   **Trích xuất cấu trúc Markdown & Code (Markdown-first Extraction - `src/tools/search/text.py`):**
    *   Hàm `_extract_main_text` phân tích DOM HTML qua BeautifulSoup và chuyển đổi thành Markdown có cấu trúc:
        *   Thẻ tiêu đề `h1..h6` thành `# Heading`.
        *   Thẻ danh sách `li` thành Markdown bullet `- Item`.
        *   Bảng HTML (`table`, `tr`, `th`, `td`) thành Markdown Table `| Col1 | Col2 |`.
        *   Khối code `<pre><code>` được bảo toàn nguyên vẹn kèm language identifier: ` ```python\ncode\n``` `.
        *   Loại bỏ triệt để các thành phần rác: cookie banners, navbars, footer, TOC, author bio, ads, newsletter widgets.
*   **Trích xuất Highlight thông minh & Khép kín Code Block (Query-Density Window - `src/tools/search/text.py`):**
    *   Hàm `_extract_query_highlight` quét mật độ từ khóa của truy vấn trên từng đoạn văn trong tài liệu.
    *   Chọn đoạn văn có điểm số trùng khớp cao nhất làm tâm và mở rộng vùng lân cận cho đến khi đạt ngưỡng ký tự bằng chứng (`search_evidence_max_chars`).
    *   Tự động phát hiện và khép kín code fence (chống vỡ markdown khi cắt ngang khối code).
*   **Mỏ neo thời gian thực & Bóc tách ngày xuất bản chuẩn xác (`src/tools/search/text.py` & `src/tools/search/ranking.py`):**
    *   Đóng dấu mỏ neo thời gian thực của máy chủ hệ thống ngay đầu kết quả: `[Mỏ neo thời gian thực: Sunday, 11 October 2026 — 06:00 PM]`. Giúp LLM không bị ảo giác ngày tháng do training cutoff.
    *   Trích xuất ngày xuất bản có thẩm quyền từ OpenGraph (`article:published_time`), JSON-LD (`datePublished`), và thẻ HTML5 `<time datetime="...">`, gắn huy hiệu `[📅 DD/MM/YYYY — X ngày trước]` vào từng nguồn tin.
*   **Thuật toán Xếp hạng Cấp độ Exa (Exa-Grade Ranking - `src/tools/search/ranking.py` & `src/tools/search/data.py`):**
    *   **Phân tầng Thẩm quyền Tên miền (Domain Authority Tiers):**
        *   *Tier 1 (+2.5 điểm):* Tài liệu chính thức, repo, chuẩn mực kỹ thuật và AI labs (`github.com`, `deepmind.google`, `*.google.com`, `*.anthropic.com`, `*.openai.com`, `ietf.org`, `w3.org`, `modelcontextprotocol.io`, `arxiv.org`, `docs.*`, `.edu`, `.gov`).
        *   *Tier 2 (+1.0 điểm):* Báo chí công nghệ uy tín (`theverge.com`, `arstechnica.com`, `reuters.com`, `stackoverflow.com`).
        *   *Tier 4 (-3.0 điểm):* Trang SEO rác, tin đồn, clickbait farm (`tokencalc.dev`, `whichllm.io`, `projedefteri.com`, `hackaigc.com`, `shattered.io`, ...).
    *   **Độ tươi Liên kết (Coupled Freshness):** Chỉ thưởng điểm độ tươi (năm hiện tại, tháng, mốc thời gian) khi xuất hiện **cùng câu** với các từ khóa truy vấn, loại bỏ tình trạng nhồi nhét từ khóa năm ("2026") của trang tin đồn.
    *   **Mật độ Thông tin Kỹ thuật (Technical Information Density):** Tăng điểm cho các trang chứa bảng thông số so sánh Markdown, khối code cú pháp, hoặc số liệu benchmark định lượng (`ms`, `%`, token).
    *   **Hồ sơ Truy vấn Động (Dynamic Profiles):** Tự động phân loại `tech_spec`, `realtime_news`, `code_howto`, `general` để áp dụng trọng số chấm điểm tối ưu theo bản chất câu hỏi.
*   **Cấu hình linh hoạt qua biến môi trường (`src/tools/search/engine/manager.py`):**
    *   `SEARCH_GROUNDED_TOP_LINKS`: Số liên kết web tải sâu song song (mặc định: `8`).
    *   `SEARCH_TOP_RESULTS_LIMIT`: Số lượng kết quả tìm kiếm tối đa (mặc định: `10`).
    *   `SEARCH_DEEP_READ_MAX_CHARS`: Giới hạn ký tự tối đa khi cào sâu một trang (mặc định: `8000`).
    *   `SEARCH_EVIDENCE_MAX_CHARS`: Độ dài đoạn trích xuất bằng chứng cho mỗi nguồn (mặc định: `2500`).
*   **Quy tắc bảo vệ cho Agent kế nhiệm:**
    *   **KHÔNG** nén văn bản cào về thành plain-text một dòng bằng `re.sub(r"\s+", " ", text)` (sẽ làm mất bảng, danh sách và cấu trúc mà LLM cần).
    *   **KHÔNG** đổi logic trích đoạn về `evidence[:evidence_max]` từ đầu trang (sẽ dính rác header/menu/intro).
    *   Giữ nguyên chữ ký hàm `search_with_citations(query: str) -> Tuple[str, List[Dict[str, str]]]`.

### 3.11. Định Tuyến Gemma 4 Standalone & Chính Sách Chống Cướp Model (Anti-Hijacking Policy)
*   **Vận Hành Standalone:**
    *   Gemma 4 (`gemma-4-26b-a4b-it` và `gemma-4-31b-it`) vận hành độc lập (ngoài `MODEL_POOLS`), sử dụng trực tiếp các API key Google AI Studio/Gemini.
    *   Hạn mức context an toàn được áp đặt là `GEMMA_CONTEXT_LENGTH = 14000` tokens.
    *   Cơ chế thinking: Hỗ trợ các mức suy luận `low`, `medium`, `high`, `minimal`. Mọi cấu hình gửi lên Google API bắt buộc phải sanitize sang `thinking_level` (Google API sẽ trả 400 nếu nhận `thinking_budget`).
*   **Chính Sách Chống Cướp Model Native (Anti-Hijacking Policy):**
    *   Các model Native (`gemma`, `gemini`, `flash`, hoặc nằm trong `AVAILABLE_MODELS`/`MODEL_POOLS`) được bảo vệ nghiêm ngặt: **tuyệt đối không bao giờ** bị chiếm quyền bởi các Custom Endpoint cấp tài khoản không có pool_assignments (như `9router`).
    *   Custom Endpoint tại Step 1 của `_resolve_model` chỉ được nhận request khi: (1) Model được yêu cầu là model bên ngoài (không phải native) VÀ (2) Tên model khớp chính xác với danh sách model mà Custom Endpoint đó hỗ trợ (`_match_endpoint_model`). Nghiêm cấm hành vi gán ép model đầu tiên `[0]` một cách tùy tiện.
    *   `router.resolve_model_alias` chuẩn hóa tự động mọi biến thể tên gọi từ client (`gemma-4-27b`, `gemma-27b`, `gemma-31b`, `gemma-4-31b-it`) về đúng alias chuẩn của hệ thống (`gemma-4-26b` hoặc `gemma-4-31b`).

**Luồng chung:** Tất cả các proxy đều ủy quyền cho `PoolManager`. `PoolManager` sau đó sử dụng `APIRouter` để giải quyết key và `gemini/facade` để thực hiện cuộc gọi API thực tế đến Gemini hoặc Custom Endpoint, và cuối cùng xử lý phản hồi trước khi trả về cho proxy để định dạng lại cho client.

## 4. Luồng Yêu Cầu API Tổng Quan

```mermaid
graph TD
    Client[Client Request (Claude Code/OpenCode)] --> API_Server[FastAPI Server]
    API_Server --> Route_Handler[Route Handlers (opencode_routes.py / completions/ / standard_routes/)]
    Route_Handler --> Auth_Limit[Authentication & Account Limiter]
    Auth_Limit --> PoolManager[PoolManager: Orchestrates Key/Model Selection & Retry Loop]
    PoolManager --> APIRouter[APIRouter: Resolves Model, Acquires Key]
    APIRouter --> KeyResolver[KeyResolverMixin: Circuit Breaker, Adaptive Cooldown, Double Random Key Selection]
    KeyResolver --> Backend_KeyStatus[Backend/Key Status DB]
    APIRouter --> RateLimiter[Gemini Rate Limiter]
    PoolManager --> ModelPool[ModelPool: Slot-based Concurrent Workers]
    ModelPool --> Proxy_Handler[Proxy Handler (e.g., opencode_proxy/handler/proxy.py)]
    Proxy_Handler --> GeminiFacade[Gemini Facade (SDK / Custom Endpoint)]
    GeminiFacade --> External_LLM[External LLM APIs (Gemini, Claude, Custom)]
    External_LLM --> GeminiFacade_Response[Gemini Facade: Parses LLM Response]
    GeminiFacade_Response --> Proxy_Handler_Format[Proxy Handler: Formats Response for Client]
    Proxy_Handler_Format --> PoolManager_Record[PoolManager: Records Success/Failure]
    PoolManager_Record --> APIRouter_Update[APIRouter: Updates Key/Model Status]
    APIRouter_Update --> Client_Response[Response to Client]
```

## 5. Cơ Chế Định Tuyến & Chống Lỗi (Resilience Mechanisms)

Các cơ chế này được thiết kế để đảm bảo hệ thống hoạt động ổn định và hiệu quả, đặc biệt trong môi trường tải cao và khi đối mặt với các lỗi từ nhà cung cấp LLM. Chi tiết cụ thể có thể xem trong `docs/routing_and_resilience.md`.

*   **Xử lý lỗi 429 và 503 (Soft Handling vs Hard Freeze)**: Phân loại lỗi thành tạm thời và vĩnh viễn để áp dụng các chiến lược đóng băng key và hình phạt khác nhau, từ cooldown ngắn đến đóng băng dài hạn.
*   **Thuật toán Double Random & Jitter**:
    *   **Priority Score Jitter**: Nhiễu ngẫu nhiên ±5% vào điểm ưu tiên để phá vỡ thế khóa cứng (lock-step) khi các key có điểm xấp xỉ nhau.
    *   **Candidate Pool Sizing**: Lọc lấy nhóm ứng viên khỏe nhất với kích thước linh hoạt (`pool_size = max(2, (total+1)//2)` khi có từ 2 key trở lên), đảm bảo không bao giờ bị rơi vào tình trạng chỉ chọn duy nhất key đầu tiên khi pool nhỏ (2-3 keys).
    *   **Dispatch Micro-Jitter**: Thêm độ trễ vi mô (5-25ms) khi có nhiều yêu cầu đồng thời hoặc khi retry nhằm phân tán đỉnh xung đột (burst collision) ở tầng mạng trước khi gửi request tới nhà cung cấp.
    *   **Retry Timing Jitter**: Ngẫu nhiên hóa thời gian chờ giữa các lần thử lại với công thức Exponential Backoff kèm độ lệch ngẫu nhiên $\pm 20\%$ trong `_retry_delay` để chống "Thundering Herd".
*   **Chế độ Phòng Chống Nghẽn Tải Cực Cao (Extreme Checking Logic)**: Khi gặp lỗi liên tục, hệ thống sẽ siết chặt hạn ngạch và ưu tiên các yêu cầu nhỏ để hạ nhiệt.
*   **Cơ chế Lan Truyền Lỗi HTTP Chuẩn (Proper HTTP Error Propagation)**: Khi có lỗi rate limit / timeout, proxy lan truyền ngoại lệ lên FastAPI để trả về mã HTTP thật (`429 Rate Limit` hoặc `503 Overloaded`). Client tự backoff retry ở tầng client và không nhồi nội dung lỗi vào lịch sử chat.
*   **Cơ chế Pool Concurrency (ModelPool slot-based workers)**: Mỗi pool member có lock riêng (`asyncio.Lock`), đảm bảo chỉ một request xử lý tại một thời điểm. Custom endpoint là first-class member qua `pool_assignments`. Model ngoài `MODEL_POOLS` chạy standalone (không qua acquire/release).

## 6. Các Quyết Định Thiết Kế & Trade-offs (Chủ ý về Coupling & Complexity)

Trong quá trình phát triển, đặc biệt với việc tái sử dụng code và hỗ trợ từ AI, một số quyết định đã được đưa ra để ưu tiên tính thực chiến và tốc độ, dù có thể làm tăng độ Coupling và Cognitive Complexity:

*   **Coupling giữa Core Router và Translator**: Việc `APIRouter` phụ thuộc vào các hàm từ `src.logical_HQ_translator` (ví dụ: `_resolve_model`) là một chủ ý. Điều này giúp tích hợp nhanh các logic chuyển đổi và giải quyết mô hình phức tạp vào ngay luồng định tuyến cốt lõi, đặc biệt khi các yêu cầu từ các proxy khác nhau cần được chuẩn hóa trước khi đến `PoolManager`. Dù vi phạm nguyên tắc phân tầng truyền thống, nó giúp giảm thiểu boilerplate code và tăng tốc độ phát triển tính năng.
*   **Monolithic `PoolManager`**: `PoolManager` đảm nhận nhiều trách nhiệm (xoay vòng key, retry, quota check, phân loại lỗi, quản lý slot concurrency qua `ModelPool`) trong một lớp duy nhất. Quyết định này được đưa ra để:
    *   **Đơn giản hóa luồng điều khiển**: Tất cả logic xử lý lỗi và retry được tập trung tại một nơi, dễ theo dõi và điều chỉnh hơn trong môi trường production thực tế, nơi mà các lỗi từ LLM API thường rất khó đoán.
    *   **Tối ưu hóa hiệu suất**: Tránh overhead của việc gọi qua lại giữa nhiều dịch vụ nhỏ hơn, đặc biệt quan trọng trong các ứng dụng có độ trễ thấp.
    *   **Khả năng tái sử dụng nhanh**: Dễ dàng được các Proxy khác nhau ủy quyền mà không cần tái tạo lại logic phức tạp.
    *   **Cấp slot concurrency riêng**: Việc quản lý slot-based concurrent worker được tách biệt thành `ModelPool` nhưng vẫn do `PoolManager` gọi, giữ nguyên architecture tập trung.
Những quyết định này phản ánh triết lý "practicality over purity" (thực tế hơn lý thuyết) trong một dự án có yêu cầu cao về hiệu suất và khả năng chống chịu lỗi, đặc biệt khi có yếu tố tái sử dụng code và phát triển nhanh với AI.

## 7. Quản Lý Trạng Thái (Memory vs SQLite)

Hệ thống sử dụng một chiến lược kết hợp để quản lý trạng thái, tận dụng cả bộ nhớ trong (in-memory) và cơ sở dữ liệu SQLite để đạt được hiệu suất và tính bền vững.

*   **Trạng thái In-memory (thời gian thực, ngắn hạn):** Các giới hạn tốc độ dựa trên phút (RPM, TPM) được quản lý hoàn toàn trong bộ nhớ trong mỗi instance `GeminiRateLimiter` (`src/core/limits/gemini/limiter.py`). Các cấu trúc dữ liệu `deque` được bảo vệ bằng `asyncio.Lock` để đảm bảo an toàn luồng (thread-safety) trong cùng một tiến trình (process).
*   **Trạng thái In-memory với ghi vào DB không đồng bộ (dài hạn):** Các thông tin sử dụng key hàng ngày (`_rpd_count` và `today` trong `keys/status`) và các hình phạt key (`_score_penalties`) được lưu giữ trong bộ nhớ sau khi tải ban đầu từ DB. Mọi thay đổi quan trọng được ghi không đồng bộ vào SQLite thông qua một `ThreadPoolExecutor(max_workers=1)` (`src/backend/keys/status.py`). Cơ chế này đảm bảo các thao tác ghi vào DB không làm chặn luồng xử lý chính.

**Lưu ý về hiệu suất:**
*   `ThreadPoolExecutor(max_workers=1)` giúp giảm thiểu tắc nghẽn I/O trực tiếp trên luồng chính. Tuy nhiên, nếu có một lượng lớn các cập nhật key hoặc thay đổi hình phạt *khác nhau* xảy ra đồng thời, worker đơn lẻ này có thể trở thành nút thắt cổ chai, dẫn đến việc xếp hàng đợi các tác vụ ghi vào DB.
*   Việc đọc dữ liệu chủ yếu từ bộ nhớ sau khi tải ban đầu giúp giảm đáng kể số lần truy cập DB, tránh các tắc nghẽn đọc.

## 8. Giải Quyết Tranh Chấp Khóa SQLite trong Môi Trường Đa Tiến Trình

**Vấn đề:**
Cơ chế khóa hiện tại sử dụng `threading.Lock` (hoặc `threading.RLock`) trong `src/backend/_db.py` chỉ đảm bảo an toàn luồng *trong cùng một tiến trình*. Nếu hệ thống được triển khai với nhiều worker (ví dụ: Uvicorn với `workers > 1`), mỗi worker sẽ có một `ThreadPoolExecutor` và `_LOCK` riêng, không phối hợp với nhau giữa các tiến trình. Điều này có thể dẫn đến lỗi `sqlite3.OperationalError: database is locked` khi nhiều tiến trình cố gắng ghi vào cùng một file SQLite đồng thời.

**Kết luận:**
Cơ chế khóa hiện tại **chưa đủ** để ngăn chặn lỗi `database is locked` trong các triển khai Uvicorn đa worker. Để đảm bảo tính đúng đắn và khả năng mở rộng trong môi trường đa tiến trình, cần có một cơ chế khóa liên tiến trình (cross-process locking) hoặc xem xét sử dụng một hệ quản trị cơ sở dữ liệu mạnh mẽ hơn (như PostgreSQL).

**Khuyến nghị:**
Hiện tại, hệ thống được thiết kế để hoạt động ổn định nhất trong môi trường **đơn worker**. Nếu yêu cầu triển khai đa worker, cần bổ sung cơ chế khóa liên tiến trình (ví dụ: sử dụng thư viện `filelock` hoặc các khóa tư vấn cấp hệ điều hành) hoặc chuyển sang một hệ quản trị cơ sở dữ liệu hỗ trợ đồng thời cao hơn.

## 9. Các Biến Môi Trường Chính

| Variable | Default | Mô tả |
|----------|---------|-------|
| `GEMINI_API_KEY_1..N` | — | Các khóa API Gemini |
| `ROUTER_API_PORT` | `58100` | Cổng Server |
| `ROUTER_API_MAX_RETRIES` | `5` | Số lần thử lại tối đa ở chế độ độc lập |
| `ROUTER_API_REQUEST_TIMEOUT_SEC` | `300` | Timeout tổng cho request non-stream |
| `ROUTER_API_STREAM_OPEN_TIMEOUT_SEC` | `30` | Timeout mở stream provider trước khi pool xoay member |
| `POOL_SWAP_FAILURES` | `5` | Số lỗi tạm thời trước khi swap thành viên pool |
| `POOL_RETRY_SECONDS` | `30` | Ngân sách thời gian retry của một pool request |
| `POOL_MAX_ATTEMPTS` | `15` | Số lần thử lại tối đa trong vòng lặp pool |
| `KEY_429_COOLDOWN_SECONDS` | `15` | Thời gian đóng băng key sau rate-limit |
| `KEY_INVALID_COOLDOWN_SECONDS` | `3600` | Thời gian đóng băng key sau lỗi invalid/billing |
| `FREE_KEY_END` | — | Index cuối của các key free tier |
| `PREMIUM_KEY_END` | — | Index cuối của các key premium tier |
| `GEMINI_FLASH_35_MODEL` | `gemini-3.5-flash` | ID model backend cho flash-35 |
| `GEMINI_FLASH_30_MODEL` | `gemini-3-flash-preview` | ID model backend cho flash-30 |
| `GEMINI_FLASH_25_MODEL` | `gemini-2.5-flash` | ID model backend cho flash-25 |

| `MODEL_CONTEXT_LENGTH` | `220000` | Độ dài context tối đa cho tính toán dung lượng pool |
| `ROUTER_API_PASSWORD_KEY` | — | Khóa bí mật dùng để mã hóa hai chiều mật khẩu web trong DB (AES-GCM/Fernet) |

## 10. Web Search Architecture (Cách A & Cách B)

Hệ thống Router API v2 hỗ trợ tìm kiếm Web linh hoạt và mạnh mẽ, được chia làm hai cơ chế thiết kế độc lập nhằm phục vụ các nhu cầu khác nhau:

### 10.1. Cách A: Client-side Search (API tìm kiếm độc lập)
Dành cho các ứng dụng client hoặc Agent độc lập chạy local (ví dụ: Claude Code, Cline, Roo Code) muốn tự quản lý logic tìm kiếm của mình.
* **Endpoint**: `POST /v1/search` và `/search`.
* **Xác thực**: Kiểm tra token thông qua `Bearer` token tương tự như API Completions.
* **Payload đầu vào**:
  ```json
  {
    "query": "từ khóa tìm kiếm",
    "search_engine": "duckduckgo"
  }
  ```
* **Dữ liệu trả về**: Trả về dữ liệu tìm kiếm dưới dạng cấu trúc JSON bao gồm kết quả đã định dạng (`results`) và danh sách các nguồn thông tin nguồn gốc (`citations`):
  ```json
  {
    "status": "success",
    "query": "từ khóa tìm kiếm",
    "results": "[Web Search Results] ... (nội dung định dạng)",
    "citations": [
      { "title": "Tiêu đề bài viết", "url": "https://example.com/..." }
    ]
  }
  ```

### 10.2. Cách B: Server-side Tool Use (Gọi hàm qua LLM)
Dành cho các completions client truyền thống. Router đóng vai trò làm cầu nối (bridge) xử lý tool gọi hàm tự động.
* **Cơ chế**: Khi client gửi request Completions kèm cờ kích hoạt tìm kiếm (ví dụ: `web_search: true`), Router sẽ tự động định nghĩa và tiêm (inject) schema công cụ `WebSearch` vào tham số `tools` gửi lên LLM.
* **Luồng chạy**:
  1. LLM nhận diện công cụ `WebSearch` trong danh sách `tools`.
  2. Khi gặp câu hỏi cần thông tin thực tế, LLM sinh ra Tool Call: `WebSearch(query="...")`.
  3. Router bắt được Tool Call này từ LLM, tự động chạy tìm kiếm trên server qua DuckDuckGo.
  4. Router trả kết quả tìm kiếm lại cho LLM dưới dạng tin nhắn của tool (`role: tool`), giúp LLM có đầy đủ ngữ cảnh để trả lời câu hỏi gốc.

### 10.3. Cơ chế cấu hình & Độ ưu tiên (Precedence)
Các thiết lập tìm kiếm có độ ưu tiên 3 tầng chặt chẽ (được ghim chặt bởi test suite `tests/test_search_engine_precedence.py`):
1. **Ưu tiên cao nhất (Client Payload Override)**: Nếu trong request body gửi lên có chứa cấu hình tường minh (`"search_engine": "duckduckgo"` hoặc cờ tắt `"web_search": false`), hệ thống sẽ lập tức tuân thủ yêu cầu này của Client.
2. **Ưu tiên thứ hai (Hosted Search Dialect)**: Các request dạng hosted search (như `/v1/responses` với `tools: [{"type": "web_search"}]`) sẽ sử dụng dialect tìm kiếm Router nội bộ trước.
3. **Mặc định toàn hệ thống**: Mọi request khác đều tự động chuyển hướng về công cụ tìm kiếm nội bộ DuckDuckGo (`duckduckgo`).

*(Lưu ý: Cơ chế ghi đè ở cấp độ database account `account.get("search_engine")` trước đây đã bị loại bỏ hoàn toàn do gây xung đột đè nhầm request client; xem chi tiết tại [deprecated.md](deprecated.md)).*

### 10.4. Tiết kiệm 100% Quota Key Gemini qua DuckDuckGo & Nâng cấp MCP Server
Hệ thống sử dụng **DuckDuckGo nội bộ (DDGS)** thay thế hoàn toàn cho Google Search Grounding:
* **Lý do loại bỏ Google Grounding**: Google Grounding API phản hồi quá chậm (mất 5–15 giây cho một truy vấn), ngốn cạn kiệt quota API key của Gemini và gây lỗi 429 dây chuyền.
* **Tối ưu DuckDuckGo**: Router chỉ gọi trực tiếp scraper DuckDuckGo in-process ở local (`src/tools/search/providers/duckduckgo_provider.py`), không qua sub-agent hay model trung gian. Tiết kiệm 100% hạn ngạch (quota) của key Gemini và phản hồi nhanh gấp hàng chục lần.
* **Nâng cấp MCP Server (`/mcp`)**: Để các Agent (Claude Code, OpenCode, Cursor...) có thể tự do điều chỉnh nhiều tham số tìm kiếm sâu mà không phụ thuộc vào completions proxy, hệ thống cung cấp endpoint MCP Server độc lập tại `/mcp` (xem chi tiết tại [mcp_server.md](mcp_server.md)).

---

## 11. Kiến Trúc Log Streaming & Quản Trị Phân Quyền (RBAC)

Hệ thống cung cấp giải pháp streaming nhật ký thời gian thực kết hợp lưu trữ và kiểm soát truy cập nghiêm ngặt:

```text
Logger (Python standard / RotatingFileHandler)
    │
    ▼ Ghi vào file đĩa (logs/*.log)
LogWatcher (async file watcher + RAM deque buffer)
    │
    ├─ Preload 1000 dòng đuôi khi khởi động
    ├─ Broadcast dòng mới qua WebSocketManager
    │
    ▼
WebSocket /dashboard/ws & HTTP /dashboard/logs/history
    │
    ├─ Kiểm tra Quyền (RBAC Policy):
    │   ├─ Admin: Toàn quyền xem 5 kênh (proxy, api, system, keys, web)
    │   └─ User (free/premium): Chỉ được xem proxy và api; chặn keys, system, web
    │
    ▼
Frontend WebUI (LogTerminal.jsx + xterm.js)
```

1. **Chuẩn hóa kênh (`normalize_channel`)**: Hỗ trợ đầy đủ các biến thể từ client: `proxy`, `log:proxy`, `proxy.log`, `keys:endpoint`.
2. **Khả năng phục hồi dữ liệu**: Kết hợp buffer trong bộ nhớ RAM (truy xuất siêu tốc) và fallback đọc từ đĩa cứng khi client yêu cầu lịch sử lớn hoặc khi ứng dụng vừa mới khởi động.
3. **Tuân thủ phân tầng kiến trúc**: `LogWatcher` và `WebSocketManager` nằm ở tầng server transport (`src/server/`), không phụ thuộc ngược hoặc vi phạm quy tắc tầng dưới (`tests/test_layering.py`).



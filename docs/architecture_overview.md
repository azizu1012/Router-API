# Router API v2 — Kiến Trúc Tổng Quan & Quyết Định Thiết Kế

## 1. Giới Thiệu

Router API v2 là một hệ thống gateway thông minh được thiết kế để quản lý và định tuyến các yêu cầu đến nhiều nhà cung cấp Mô hình Ngôn ngữ Lớn (LLM) khác nhau (như Gemini, Claude, và các Custom Endpoints). Mục tiêu chính là cung cấp một lớp trừu tượng (abstraction layer) vững chắc, đảm bảo tính khả dụng cao, khả năng chống chịu lỗi (resilience) và phân phối tải hiệu quả, ngay cả dưới áp lực cao.

Hệ thống được phát triển với sự hỗ trợ của AI và tái sử dụng mã nguồn, dẫn đến một số quyết định thiết kế độc đáo nhằm ưu tiên tính thực chiến và tốc độ triển khai.

## 2. Cấu Trúc Dự Án

```text
d:\AI_Projects\router_api/
├── .env                          # Gemini keys + model config
├── .env.example                  # Template for env file config
├── README.md                     # Quick start + client config
├── docs/                         # Thư mục chứa tài liệu kiến trúc
│   └── architecture_overview.md  # File kiến trúc này
│   └── routing_and_resilience.md # Chi tiết cơ chế chống lỗi
├── DEPLOY_DOMAIN.md              # Caddy và Nginx reverse proxy deployment guide
├── AGENTS.md                     # OpenCode agent task management instructions
├── CLAUDE.md                     # Claude Code developer instructions
├── opencode.json                 # OpenCode configuration
├── requirements.txt              # Project Python dependencies
├── main.py                       # Uvicorn startup script với auto port-freeing
├── usage.db                      # SQLite config DB (accounts, endpoints, key_status, key_penalties)
├── usage_logs.db                 # SQLite telemetry DB for token tracking
├── logs/                         # Rotating file logs (daily auto-clean)
└── src/                          # Mã nguồn Python chính
    ├── api/                      # Các Proxy chuyển đổi định dạng
    │   ├── claude_proxy/         # Anthropic→Gemini proxy
    │   └── opencode_proxy/       # OpenCode→Gemini proxy (OpenAI-compatible)
    ├── logical_HQ_translator/    # Các bộ chuyển đổi & tiện ích dùng chung
    ├── core/                     # Các module cốt lõi của Router
    │   ├── api_config.py         # Định nghĩa toàn bộ model
    │   ├── pool_manager.py       # TRUNG TÂM: pool loop, key rotation, quota, retry
    │   ├── config_n_logg/        # Cấu hình & Ghi nhật ký
    │   ├── accounts/             # Quản lý tài khoản
    │   ├── limits/               # Giới hạn Rate Limit & Account Limiter
    │   ├── providers/            # Gemini Facade, Custom Endpoints, Search Manager
    │   └── router/               # APIRouter, ModelPool, KeyResolver
    ├── backend/                  # Tầng DB SQLite
    ├── console/                  # CLI admin
    └── server/                   # FastAPI Server, WebSocket Manager
```

## 3. Các Thành Phần Chính & Vai Trò

### 3.1. APIRouter (`src/core/router/core/router.py`)
*   **Vai trò**: Là trái tim của hệ thống định tuyến và quản lý tài nguyên. `APIRouter` là một Singleton, đảm bảo toàn bộ ứng dụng sử dụng cùng một thể hiện để quản lý trạng thái toàn cục của các API Key và Model.
*   **Trạng thái Key In-memory (`_key_status`):** `APIRouter` duy trì một dictionary `_key_status` trong bộ nhớ, chứa trạng thái chi tiết của tất cả các API Key (enabled, usage, active requests, frozen_until, consecutive_failures, last_success, per_model failures/frozen_until). Đây là nguồn dữ liệu chính cho các quyết định định tuyến trong thời gian chạy.
    *   **Khởi tạo:** Khi khởi động, `_key_status` được tải từ cơ sở dữ liệu SQLite (`src/backend/key_status.py`) và hợp nhất với trạng thái mặc định cho các key mới hoặc chưa đầy đủ thông tin.
    *   **Đồng bộ hóa:** Mọi cập nhật trạng thái key (ví dụ: `acquire_key`, `record_success`, `record_failure`, `freeze_key`, `release_key`) sẽ thay đổi trạng thái in-memory và sau đó kích hoạt các thao tác ghi **bất đồng bộ** vào SQLite thông qua các hàm `atomic_reserve_key`, `atomic_release_key`, `atomic_freeze_key`, `atomic_record_success` trong `src/backend/key_status.py`. Điều này đảm bảo tính bền vững của dữ liệu mà không làm chặn luồng xử lý chính.
*   **Chọn Model**: Chịu trách nhiệm chọn mô hình phù hợp nhất dựa trên ưu tiên, tình trạng sức khỏe và giới hạn.
*   **Giới hạn tốc độ**: Tương tác với `src/core/limits/gemini_rate_limiter.py` để thực thi các giới hạn RPM/TPM.
*   **Theo dõi sức khỏe Model**: `update_model_health` điều chỉnh điểm số của mô hình dựa trên thành công/thất bại.

### 3.2. PoolManager (`src/core/pool_manager.py`)
*   **Vai trò**: Điều phối vòng lặp xoay vòng (rotation) qua các pool member và thực hiện retry khi gặp lỗi tạm thời. Đây là module trung tâm xử lý logic retry, failover và quản lý pool một cách "monolithic" (tập trung). Nó là thành phần điều phối chính trong việc lựa chọn key/model, quản lý quota và xử lý vòng lặp retry.
*   **Chức năng chính**:
    *   **Vòng lặp Pool**: Chứa logic chính cho việc thử lại và xoay vòng qua các thành viên trong pool (ví dụ: các phiên bản khác nhau của Gemini Flash).
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
    *   **`_db.py`**: Quản lý kết nối DB dùng chung.
    *   **`key_status.py`**: Các thao tác nguyên tử (atomic operations) để cập nhật trạng thái key (reserve, release, freeze, disable).
    *   **`accounts.py`, `endpoints.py`, `model_prices.py`**: CRUD cho tài khoản, custom endpoints và giá model.

### 3.6. Logical HQ Translator (`src/logical_HQ_translator/`)
*   **Vai trò**: Chứa các bộ chuyển đổi và tiện ích dùng chung giữa các proxy, đặc biệt là liên quan đến việc xử lý message, định dạng và các công cụ hỗ trợ AI Agent.
*   **Chức năng chính**:
    *   **`model_resolver.py`**: Giải quyết bí danh mô hình và quản lý độ đồng thời của key.
    *   **`format_normalizer.py`**: Chuẩn hóa văn bản streaming và trích xuất XML thinking.
    *   **`rtk.py`**: Lọc và định dạng output của các công cụ (git diff, status, ls, grep) cho AI Agent.

### 3.7. Providers (`src/core/providers/`)
*   **Vai trò**: Cung cấp các giao diện thống nhất để tương tác với các nhà cung cấp LLM khác nhau và quản lý custom endpoints.
*   **Chức năng chính**:
    *   **`gemini_facade.py`**: Facade chính để gọi Gemini SDK hoặc Custom Endpoint và chuyển đổi output sang định dạng tương thích OpenAI.
    *   **`gemini/`**: Chứa các module cụ thể cho Gemini API (Manager, Caller, Pool, Error classification).
    *   **`custom_endpoint_manager.py`**: Quản lý các custom endpoints (CRUD, pool, health).

### 3.8. Giao Thức Từ Provider Tới Endpoint

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
    *   Các yêu cầu được định tuyến trực tiếp đến API Gemini thông qua `src/core/providers/gemini_facade.py`.
    *   `gemini_facade` chịu trách nhiệm tương tác với Gemini SDK hoặc các endpoint HTTP của Gemini, và chuẩn hóa phản hồi.

*   **Custom Endpoint:**
    *   Các Custom Endpoint được quản lý bởi `src/core/providers/_custom_endpoint_manager.py`.
    *   **Quản lý (CRUD):** Các endpoint có thể được thêm, cập nhật, xóa, bật/tắt thông qua các hàm trong `src/backend/endpoints.py`. Điều này cho phép quản lý linh hoạt các nhà cung cấp LLM bên ngoài.
    *   **Kiểm tra sức khỏe (Health Checks):** `CustomEndpointManager` thực hiện các kiểm tra sức khỏe định kỳ (`ping_endpoint`) đến `/models` endpoint của Custom Endpoint. Kết quả ping được lưu vào bộ nhớ đệm (`_ping_cache`) để tránh gọi API liên tục.
    *   **Circuit Breaker:** Một cơ chế circuit breaker (`_circuit_breaker`) được triển khai để đóng băng tạm thời các Custom Endpoint bị lỗi liên tiếp. Thời gian đóng băng sẽ tăng lên theo số lần lỗi, giúp hệ thống không gửi yêu cầu đến các endpoint không ổn định.
    *   **Phát hiện Model:** Hàm `fetch_models` tự động khám phá các model có sẵn từ Custom Endpoint bằng cách gọi API `/models` của nó và cập nhật vào cấu hình của endpoint.
    *   **Gán cho Pool/Tài khoản:** Các Custom Endpoint có thể được gán vào các pool cụ thể và liên kết với các tài khoản người dùng, cho phép kiểm soát chi tiết việc định tuyến và sử dụng.
    *   **Xử lý Payload khi Fallback:** Khi một yêu cầu ban đầu đi qua các Proxy Layer (ClaudeProxy hoặc OpenCodeProxy), nó đã được dịch về một định dạng payload nội bộ dùng chung. Nếu `PoolManager` chọn một Custom Endpoint và sau đó Custom Endpoint này báo lỗi và hệ thống fallback về bể API Key của Gemini nội bộ, `GeminiFacade` (`src/core/providers/gemini_facade.py`) sẽ nhận lại đúng payload gốc của hệ thống. `GeminiFacade` chịu trách nhiệm tái cấu trúc payload (Strip/Convert các tham số đặc thù của OpenAI như `response_format` hay cấu trúc `tools` dạng function call) sang cấu hình tương thích với Gemini SDK/HTTP native một cách tự động. Logic này được triển khai trong các hàm phụ trợ của `gemini_facade.py` (ví dụ: `_sdk_acompletion` cho Gemini native, `_http_acompletion` cho OpenAI-compatible), đảm bảo cấu trúc `tool_calls` và các tham số khác được dịch đúng cách.

**Luồng chung:** Tất cả các proxy đều ủy quyền cho `PoolManager`. `PoolManager` sau đó sử dụng `APIRouter` để giải quyết key và `gemini_facade` để thực hiện cuộc gọi API thực tế đến Gemini hoặc Custom Endpoint, và cuối cùng xử lý phản hồi trước khi trả về cho proxy để định dạng lại cho client.

## 4. Luồng Yêu Cầu API Tổng Quan

```mermaid
graph TD
    Client[Client Request (Claude Code/OpenCode)] --> API_Server[FastAPI Server]
    API_Server --> Route_Handler[Route Handlers (opencode_routes.py / standard_routes.py)]
    Route_Handler --> Auth_Limit[Authentication & Account Limiter]
    Auth_Limit --> PoolManager[PoolManager: Orchestrates Key/Model Selection & Retry Loop]
    PoolManager --> APIRouter[APIRouter: Resolves Model, Acquires Key]
    APIRouter --> KeyResolver[KeyResolverMixin: Circuit Breaker, Adaptive Cooldown, Double Random Key Selection]
    KeyResolver --> Backend_KeyStatus[Backend/Key Status DB]
    APIRouter --> RateLimiter[Gemini Rate Limiter]
    PoolManager --> Proxy_Handler[Proxy Handler (e.g., opencode_proxy/handler/proxy.py)]
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
*   **Thuật toán Double Random**:
    *   **Timing Jitter**: Ngẫu nhiên hóa thời gian chờ giữa các lần thử lại để tránh "Thundering Herd".
    *   **Priority Selection Randomization**: Chọn ngẫu nhiên key từ nhóm tốt nhất để phân tán tải, giảm thiểu xung đột hạn ngạch.
*   **Chế độ Phòng Chống Nghẽn Tải Cực Cao (Extreme Checking Logic)**: Khi gặp lỗi liên tục, hệ thống sẽ siết chặt hạn ngạch và ưu tiên các yêu cầu nhỏ để hạ nhiệt.
*   **Cơ chế Lan Truyền Lỗi HTTP Chuẩn (Proper HTTP Error Propagation)**: Tách biệt luồng xử lý lỗi cho Main Agent (trả về lỗi HTTP thật) và Sub-Agent (trả về phản hồi mô phỏng 200 OK) để tối ưu hóa hành vi client và tránh làm bẩn context.
*   **Quyết Định Định Tuyến Sub-Agent Linh Hoạt (Dynamic Sub-Agent Model Selection)**: Cho phép cấu hình mô hình sub-agent động, tránh hardcode và đảm bảo tính nhất quán giữa tính toán quota và định tuyến thực tế.

## 6. Các Quyết Định Thiết Kế & Trade-offs (Chủ ý về Coupling & Complexity)

Trong quá trình phát triển, đặc biệt với việc tái sử dụng code và hỗ trợ từ AI, một số quyết định đã được đưa ra để ưu tiên tính thực chiến và tốc độ, dù có thể làm tăng độ Coupling và Cognitive Complexity:

*   **Coupling giữa Core Router và Translator**: Việc `APIRouter` phụ thuộc vào các hàm từ `src.logical_HQ_translator` (ví dụ: `_resolve_model`) là một chủ ý. Điều này giúp tích hợp nhanh các logic chuyển đổi và giải quyết mô hình phức tạp vào ngay luồng định tuyến cốt lõi, đặc biệt khi các yêu cầu từ các proxy khác nhau cần được chuẩn hóa trước khi đến `PoolManager`. Dù vi phạm nguyên tắc phân tầng truyền thống, nó giúp giảm thiểu boilerplate code và tăng tốc độ phát triển tính năng.
*   **Monolithic `PoolManager`**: `PoolManager` đảm nhận nhiều trách nhiệm (xoay vòng key, retry, quota check, phân loại lỗi) trong một lớp duy nhất. Quyết định này được đưa ra để:
    *   **Đơn giản hóa luồng điều khiển**: Tất cả logic xử lý lỗi và retry được tập trung tại một nơi, dễ theo dõi và điều chỉnh hơn trong môi trường production thực tế, nơi mà các lỗi từ LLM API thường rất khó đoán.
    *   **Tối ưu hóa hiệu suất**: Tránh overhead của việc gọi qua lại giữa nhiều dịch vụ nhỏ hơn, đặc biệt quan trọng trong các ứng dụng có độ trễ thấp.
    *   **Khả năng tái sử dụng nhanh**: Dễ dàng được các Proxy khác nhau ủy quyền mà không cần tái tạo lại logic phức tạp.
*   **Sử dụng `ContextVar` cho Sub-Agent Context**: Việc sử dụng `is_sub_agent_context` (`src/core/router/core/router.py`) là một giải pháp tình thế có chủ ý để thay đổi hành vi của `freeze_key` trong ngữ cảnh Sub-Agent. Thay vì refactor toàn bộ interface của `APIRouter` để truyền context, `ContextVar` cung cấp một cách nhanh chóng để "luồn lách" qua các tầng kiểm soát mà không cần thay đổi chữ ký hàm, giúp triển khai tính năng Sub-Agent nhanh chóng.
*   **Xử lý lỗi "Defensive Over-engineering"**: Việc luôn cố gắng trả về phản hồi mô phỏng 200 OK cho client (trừ Main Agent) khi có lỗi là một chiến lược phòng thủ mạnh mẽ. Điều này giúp hệ thống cực kỳ lỳ lợm khi gặp lỗi từ LLM, giảm thiểu khả năng client bị crash và cung cấp trải nghiệm người dùng mượt mà hơn, dù có thể làm khó khăn cho việc debug nội bộ.

Những quyết định này phản ánh triết lý "practicality over purity" (thực tế hơn lý thuyết) trong một dự án có yêu cầu cao về hiệu suất và khả năng chống chịu lỗi, đặc biệt khi có yếu tố tái sử dụng code và phát triển nhanh với AI.

## 7. Quản Lý Trạng Thái (Memory vs SQLite)

Hệ thống sử dụng một chiến lược kết hợp để quản lý trạng thái, tận dụng cả bộ nhớ trong (in-memory) và cơ sở dữ liệu SQLite để đạt được hiệu suất và tính bền vững.

*   **Trạng thái In-memory (thời gian thực, ngắn hạn):** Các giới hạn tốc độ dựa trên phút (RPM, TPM) được quản lý hoàn toàn trong bộ nhớ trong mỗi instance `GeminiRateLimiter` (`src/core/limits/gemini_rate_limiter.py`). Các cấu trúc dữ liệu `deque` được bảo vệ bằng `asyncio.Lock` để đảm bảo an toàn luồng (thread-safety) trong cùng một tiến trình (process).
*   **Trạng thái In-memory với ghi vào DB không đồng bộ (dài hạn):** Các thông tin sử dụng key hàng ngày (`_rpd_count` và `today` trong `key_status`) và các hình phạt key (`_score_penalties`) được lưu giữ trong bộ nhớ sau khi tải ban đầu từ DB. Mọi thay đổi quan trọng được ghi không đồng bộ vào SQLite thông qua một `ThreadPoolExecutor(max_workers=1)` (`src/backend/key_status.py`). Cơ chế này đảm bảo các thao tác ghi vào DB không làm chặn luồng xử lý chính.

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

""" + """## 9. Các Biến Môi Trường Chính

| Variable | Default | Mô tả |
|----------|---------|-------|
| `GEMINI_API_KEY_1..N` | — | Các khóa API Gemini |
| `ROUTER_API_PORT` | `58100` | Cổng Server |
| `ROUTER_API_MAX_RETRIES` | `5` | Số lần thử lại tối đa ở chế độ độc lập |
| `POOL_SWAP_FAILURES` | `5` | Số lỗi tạm thời trước khi swap thành viên pool |
| `POOL_MAX_ATTEMPTS` | `15` | Số lần thử lại tối đa trong vòng lặp pool |
| `KEY_429_COOLDOWN_SECONDS` | `15` | Thời gian đóng băng key sau rate-limit |
| `KEY_INVALID_COOLDOWN_SECONDS` | `3600` | Thời gian đóng băng key sau lỗi invalid/billing |
| `FREE_KEY_END` | — | Index cuối của các key free tier |
| `PREMIUM_KEY_END` | — | Index cuối của các key premium tier |
| `GEMINI_FLASH_35_MODEL` | `gemini-3.5-flash` | ID model backend cho flash-35 |
| `GEMINI_FLASH_30_MODEL` | `gemini-3-flash-preview` | ID model backend cho flash-30 |
| `GEMINI_FLASH_25_MODEL` | `gemini-2.5-flash` | ID model backend cho flash-25 |
| `OPENCODE_SUB_AGENT_MODEL` | `gemini-flash-lite` | Ghi đè model cho Sub-agent của OpenCode |
| `SUB_AGENT_MODEL` | `gemini-flash-lite` | Ghi đè model cho Sub-agent (fallback) |
| `MODEL_CONTEXT_LENGTH` | `220000` | Độ dài context tối đa cho tính toán dung lượng pool |

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
    "search_engine": "auto" | "duckduckgo" | "google_grounding"
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
  3. Router bắt được Tool Call này từ LLM, tự động chạy tìm kiếm trên server (qua Google Grounding hoặc DuckDuckGo).
  4. Router trả kết quả tìm kiếm lại cho LLM dưới dạng tin nhắn của tool (`role: tool`), giúp LLM có đầy đủ ngữ cảnh để trả lời câu hỏi gốc.

### 10.3. Cơ chế cấu hình & Độ ưu tiên (Precedence)
Các thiết lập tìm kiếm có độ ưu tiên rõ ràng giữa Client (Request Body) và Web (Account Configuration):
1. **Ưu tiên cao nhất (Client Override)**: Nếu trong request payload gửi lên có chứa cấu hình explicit (như `"search_engine": "duckduckgo"` hoặc cờ tắt tìm kiếm `"web_search": false`), hệ thống sẽ chạy ngay theo cấu hình này của Client và bỏ qua cấu hình trên Dashboard.
2. **Ưu tiên thứ hai (Web/Database Account settings)**: Nếu Client không chỉ định gì, hệ thống sẽ lấy cấu hình tìm kiếm mặc định của tài khoản người dùng được lưu trên database (`account.get("search_engine")`).
3. **Mặc định**: Nếu cả hai đều không thiết lập, công cụ tìm kiếm mặc định sẽ là `"auto"`.

### 10.4. Tiết kiệm Quota thông qua DuckDuckGo
Hệ thống hỗ trợ công cụ tìm kiếm `"duckduckgo"`. Khi cấu hình giá trị này (hoặc qua Web/Database hoặc qua Client payload):
* Router chỉ gọi trực tiếp scraper DuckDuckGo ở local (`src/tools/duckduckgo.py`).
* Không chạy qua sub-agent Gemini Flash Lite và không gọi Google Grounding API.
* Giúp **tiết kiệm 100% hạn ngạch (quota) API Key của Gemini** và tối ưu hóa chi phí vận hành.


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
*   **Chức năng chính**:
    *   **Quản lý Key**: `acquire_key`, `record_success`, `record_failure`, `freeze_key`, `release_key` tương tác chặt chẽ với tầng `backend/key_status.py` để lưu trữ và truy xuất trạng thái khóa.
    *   **Chọn Model**: Chịu trách nhiệm chọn mô hình phù hợp nhất dựa trên ưu tiên, tình trạng sức khỏe và giới hạn.
    *   **Giới hạn tốc độ**: Tương tác với `src/core/limits/gemini_rate_limiter.py` để thực thi các giới hạn RPM/TPM.
    *   **Theo dõi sức khỏe Model**: `update_model_health` điều chỉnh điểm số của mô hình dựa trên thành công/thất bại.

### 3.2. PoolManager (`src/core/pool_manager.py`)
*   **Vai trò**: Điều phối vòng lặp xoay vòng (rotation) qua các pool member và thực hiện retry khi gặp lỗi tạm thời. Đây là module trung tâm xử lý logic retry, failover và quản lý pool một cách "monolithic" (tập trung).
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

## 7. Các Biến Môi Trường Chính

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

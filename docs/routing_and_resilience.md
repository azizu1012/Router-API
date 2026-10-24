# Kiến Trúc Định Tuyến & Cơ Chế Chống Lỗi (Rate Limit & Resilience Guide)

Tài liệu này giải thích chi tiết các giải pháp kỹ thuật được triển khai trong Router API v2 để đối phó với hiện tượng nghẽn mạng, lỗi Rate Limit (429), lỗi dịch vụ không khả dụng (503), và cơ chế phân phối tải đa luồng có độ tin cậy cao.

---

## 1. Tổng Quan Luồng Request qua Hệ Thống

Khi một Client (như Claude Code hoặc OpenCode) gửi yêu cầu đến Gateway, luồng xử lý đi qua các tầng sau:

```mermaid
graph TD
    Client[Client Request] --> Proxy[Proxy Layer: Claude/OpenCode Proxy]
    Proxy -->|Chuyển đổi Payload| PM[PoolManager: Xoay vòng & Retry]
    PM -->|Yêu cầu Key| KR[KeyResolver: Đọc trạng thái key]
    KR -->|Double Random| KeySelected[Chọn Key tối ưu từ RAM & DB]
    KeySelected -->|Thực hiện Call| Facade[Gemini Facade]
    PM -.->|Pool Mode| MP[ModelPool: Acquire Slot]
    MP --> Facade
    Facade -->|Thành công| ClientSuccess[Trả về Client]
    Facade -->|Lỗi 429/503| PMRetry[PoolManager: Phạt Key & Backoff Retry]
    PMRetry -.->|Release Slot| MP
```

1. **Proxy Layer (Tầng chuyển đổi):** [ClaudeProxy](file:///d:/AI_Projects/router_api/src/api/claude_proxy/handler/proxy.py) và [OpenCodeProxy](file:///d:/AI_Projects/router_api/src/api/opencode_proxy/handler/proxy.py) chỉ đóng vai trò chuyển đổi định dạng request/response từ client thành định dạng dùng chung của hệ thống. Tầng này không chứa logic chọn key hay retry.
2. **PoolManager (Tầng điều phối vòng lặp):** [PoolManager](file:///d:/AI_Projects/router_api/src/core/orchestration/pool_manager.py) quản lý vòng lặp xoay vòng (rotation) qua các pool member và thực hiện retry khi gặp lỗi tạm thời. Khi chạy ở pool mode, PoolManager gọi `ModelPool.acquire()` để lấy slot cho mỗi member.
3. **KeyResolver & Router (Tầng quyết định):** [KeyResolver](file:///d:/AI_Projects/router_api/src/core/router/core/key_resolver.py) thực hiện việc tính toán độ ưu tiên (Priority Score), kiểm tra giới hạn RPM/TPM thực tế của từng key, và chọn key.

## 2. Cơ Chế Pool Concurrency (ModelPool)

Để kiểm soát độ đồng thời (concurrency) cho các pool model, hệ thống sử dụng **ModelPool** (`src/core/router/pool.py`) — một Singleton per pool với cơ chế slot-based workers:

- **Mỗi pool member có một `asyncio.Lock` riêng**, đảm bảo tại một thời điểm chỉ một request được xử lý bởi member đó. Điều này ngăn chặn tình trạng quá tải key do nhiều request đồng thời.
- **Custom endpoint là first-class citizen** trong pool: ModelPool ưu tiên chọn custom endpoint members trước Gemini members khi có member rảnh (`acquire`).
- **Model ngoài `MODEL_POOLS` chạy standalone** — không qua `acquire`/`release`, cho phép mỗi key tự quản lý concurrency riêng.
- **`sync_custom_members()`** giải quyết vấn đề cache lạnh: khi custom endpoint được thêm/gán vào pool sau khi ModelPool đã khởi tạo, hàm này cập nhật member list và tạo lock mới mà không cần khởi tạo lại pool.
- **PoolManager** gọi `ModelPool.acquire()` trước khi gửi request và `release()` trong `finally` block, đảm bảo slot luôn được giải phóng.

---



## 3. Cơ Chế Xử Lý Lỗi 429 và 503 (Soft Handling vs Hard Freeze)

Để duy trì hiệu suất cao, hệ thống phân loại lỗi thành hai nhóm chính: **Lỗi tạm thời (Transient Errors)** và **Lỗi vĩnh viễn (Permanent Errors)**.

### Phân Loại Chi Tiết

Hệ thống sử dụng một cách tiếp cận hai tầng để phân loại lỗi: đầu tiên là phân loại lỗi cụ thể của nhà cung cấp (hiện tại là Gemini), sau đó là so khớp dựa trên văn bản cho các lỗi chung.

#### **Tầng 1: Phân loại lỗi Gemini cụ thể (`classify` trong `src/core/providers/gemini/error.py`)**
Đây là tầng phân loại chính xác nhất cho các lỗi cụ thể của Gemini. Nó trích xuất `code` (mã HTTP), `status` (chuỗi trạng thái GRPC/Google Cloud), và `message` từ các ngoại lệ và phân loại theo thứ bậc:

1.  **Mã trạng thái HTTP:** Ưu tiên phân loại dựa trên các mã HTTP (400, 401, 403, 404, 429, 500, 503, 504).
2.  **Chuỗi trạng thái:** Nếu không có mã HTTP rõ ràng, kiểm tra các chuỗi trạng thái như "RESOURCE_EXHAUSTED", "PERMISSION_DENIED", "INVALID_ARGUMENT", "UNAVAILABLE".
3.  **So khớp chuỗi fallback:** Đối với các lỗi ít cấu trúc hơn, sử dụng regex/so khớp từ khóa trong `message`.

#### **Tầng 2: Phân loại lỗi chung (`_classify_error` trong `src/core/orchestration/pool_manager.py`)**
Đây là tầng phân loại thứ hai, hoạt động như một lưới an toàn. Nó gọi `classify(e)` từ tầng 1 trước. Nếu tầng 1 trả về "unknown", nó sẽ tiếp tục thực hiện so khớp dựa trên văn bản với các từ khóa rộng hơn để bắt các lỗi không được phân loại cụ thể bởi tầng Gemini.

**Bảng Ánh Xạ Phân Loại Lỗi:**

| Loại Lỗi (Text/Code Signature) | Lý Do Phân Loại | Mô Tả | Xử Lý |
| :------------------------------ | :-------------- | :---- | :---- |
| Mã HTTP 400, "invalid_argument", "bad_request" | `bad_request` | Yêu cầu không đúng định dạng hoặc tham số không hợp lệ. | Freeze Key + Thử Key Tiếp |
| Mã HTTP 401, "api key invalid", "unauthorized" | `invalid_key` | API Key không hợp lệ hoặc thiếu. | Hard Freeze |
| Mã HTTP 403, "permission denied" | `permission_denied` | Không có quyền truy cập chung. | Hard Freeze |
| Mã HTTP 403, "denied access" | `project_denied` | Không có quyền truy cập vào dự án cụ thể. | Freeze Key + Thử Key Tiếp |
| Mã HTTP 404, "not found" | `unavailable` | Tài nguyên không tìm thấy hoặc model không khả dụng. | Soft Handling |
| Mã HTTP 429, "rate limit", "resource exhausted" | `rate_limit` | Vượt quá giới hạn yêu cầu hoặc token mỗi phút (RPM/TPM). | Soft Handling |
| Mã HTTP 429, "quota exceeded", "daily" | `project_quota_429` | Vượt quá giới hạn yêu cầu mỗi ngày (RPD). | Soft Handling |
| Mã HTTP 500, 503, 504, "unavailable", "overloaded", "timeout" | `unavailable` | Dịch vụ không khả dụng, lỗi máy chủ, hoặc timeout. | Soft Handling |
| "provider search", "web_search", "tool is not allowed" | `search_backend` | Lỗi liên quan đến công cụ tìm kiếm hoặc provider search. | Soft Handling |
| Mọi lỗi khác                    | `unknown`       | Lỗi không xác định. | Soft Handling (mặc định) |

### Sự khác biệt trong cách xử lý:

| Đặc tính | Lỗi Tạm Thời (429, 503, Timeout) | Lỗi Vĩnh Viễn (invalid_key, billing_error) |
| :--- | :--- | :--- |
| **Trạng thái key** | **Soft Handling**: Cooldown cực ngắn (`KEY_429_COOLDOWN_SECONDS` ≈ 8-15 giây). | **Hard Freeze**: Đóng băng dài hạn (`KEY_INVALID_COOLDOWN_SECONDS` = 3600 giây). |
| **Xếp hạng Priority** | Áp dụng hình phạt giảm điểm tạm thời (`apply_error_penalty` trừ điểm score) để tránh chọn lại ngay. | Đóng băng vĩnh viễn trên RAM & DB, không cho phép chọn. |
| **Xử lý Pool** | Không tăng chỉ số lỗi vĩnh viễn của thành viên pool, chỉ tích lũy bộ đếm tạm thời để chuẩn bị swap model qua `model_health` khi vượt quá giới hạn. | Gọi `record_failure` ngay lập tức để giảm `model_health` score và swap sang model thành viên khác hoặc custom endpoint khác. |
| **Độ trễ Retry** | Áp dụng thuật toán **Timing Jitter** rồi thử lại. | Chuyển đổi key hoặc model ngay lập tức. |

---

## 4. Thuật Toán Double Random & Jitter (Tránh Thundering Herd & Key Collision)

Khi hệ thống xử lý đồng thời hàng chục request (high concurrency), nếu tất cả các luồng đều chọn key "tốt nhất" (Greedy Selection) hoặc thử lại cùng một lúc (Synchronized Retry), hệ thống sẽ ngay lập tức kích hoạt lỗi 429 hàng loạt trên key đó (Rate Limit Cascade). 

Để giải quyết triệt để vấn đề này, Router API v2 triển khai thuật toán **Double Random kết hợp Jitter đa tầng** gồm các lớp bảo vệ độc lập:

### Lớp 1: Ngẫu nhiên hóa thời gian chờ (Timing Jitter)
Trong hàm `_retry_delay`, thay vì chờ một khoảng thời gian cố định dạng lũy thừa ($1s, 2s, 4s...$), hệ thống tính toán một độ lệch ngẫu nhiên (Jitter) khoảng $\pm 20\%$ dựa trên khoảng cách cơ sở:

```python
def _retry_delay(attempt: int) -> float:
    if attempt >= getattr(config, "POOL_SWAP_FAILURES", 5) * 2:
        return random.uniform(0.3, 0.7)
    interval = getattr(config, "GEMINI_API_KEY_INTERVAL", 2.5)
    max_cooldown = getattr(config, "KEY_429_COOLDOWN_SECONDS", 15) * 2
    base = min(interval * (2 ** max(0, attempt)), max_cooldown)
    jitter = random.uniform(-base * 0.2, base * 0.2)
    return max(0.5, base + jitter)
```
*Tác dụng:* Phân tán thời điểm gửi lại request của các luồng đang chờ, tránh hiện tượng "Thundering Herd" (nhiều client cùng ùa vào gửi yêu cầu tại cùng một mili-giây).

### Lớp 2: Nhiễu Điểm Ưu Tiên (Priority Score Jitter) & Nhóm Ứng Viên Khỏe
Trong hàm `_select_key_double_random` (`src/core/router/core/key_resolver.py`):
1. **Priority Score Jitter**: Thêm độ lệch ngẫu nhiên $\pm 5\%$ vào điểm Priority của từng key. Điều này ngăn chặn hiện tượng hai key có điểm sát nhau (ví dụ: 100 và 99.9) bị khóa cứng vào một thứ tự cố định ở mọi luồng song song.
2. **Candidate Pool Sizing linh hoạt**: Sắp xếp danh sách key theo thứ tự: ít `active_requests` nhất -> điểm ưu tiên đã có jitter cao nhất -> ít lỗi liên tục nhất.
   - Khi có 1 hoặc 2 key: `pool_size = total_cand` (đảm bảo khi chỉ có 2 key, cả 2 key đều có cơ hội được bốc ngẫu nhiên, khắc phục lỗi `int(2 * 0.5) = 1` từng khiến hệ thống chỉ chọn duy nhất key đầu tiên).
   - Khi có từ 3 key trở lên: lấy nhóm 50% khỏe nhất làm tròn lên `max(2, (total_cand + 1) // 2)`.
3. **Random Selection**: Chọn ngẫu nhiên một key từ nhóm ứng viên này (`random.choice`).

```python
# Sizing nhóm ứng viên khỏe nhất và chọn ngẫu nhiên
total_cand = len(candidates_with_priority)
if total_cand <= 2:
    pool_size = total_cand
else:
    pool_size = max(2, (total_cand + 1) // 2)

chosen_cand = random.choice(candidates_with_priority[:pool_size])
```

### Lớp 3: Độ Trễ Vi Mô Khi Gọi API (Dispatch Micro-Jitter)
Khi có nhiều request đồng thời cùng đổ dồn vào 1 key (`active_requests > 1`) hoặc trong các lần thử lại (`attempt > 0`), hệ thống chèn một độ trễ vi mô từ 5–25ms trước khi phát tín hiệu gọi API ra bên ngoài (`PoolManager._resolve_and_call`). Khoảng trễ cực ngắn này hoàn toàn không làm chậm trải nghiệm của người dùng nhưng đủ để tách rời các xung đột gói tin (packet bursts) ở tầng mạng và gateway nhà cung cấp.

---

## 5. Chế Độ Phòng Chống Nghẽn Tải Cực Cao (Extreme Checking Logic)

Khi một request bị lỗi liên tục và số lần thử lại đạt tới giới hạn nghiêm trọng (`attempt >= 10`), hệ thống sẽ kích hoạt bộ lọc bảo vệ cực đoan (**Extreme Checking**):

1. **Siết chặt hạn ngạch (Throttling down to 70%):** RPM và TPM tối đa cho phép của key bị ép giảm xuống còn 70% công suất thiết kế.
2. **Không chấp nhận chia sẻ tải:** Chỉ những key hoàn toàn rảnh rỗi (`active_requests == 0`) và không trong thời gian cooldown mới được phép sử dụng.
3. **Ưu tiên yêu cầu nhỏ:** Đối với các prompt lớn vượt giới hạn 70% TPM, yêu cầu sẽ bị chặn ngay lập tức nếu key có bất kỳ hoạt động nào trong vòng 60 giây qua, bảo vệ key khỏi bị quá tải hoàn toàn.

Cơ chế này hoạt động như một van an toàn (Safety Valve) giúp hạ nhiệt hệ thống khi đang xảy ra tình trạng bão request 429 trên diện rộng.


---

## 6. Cơ Chế Giới Hạn Tải Trượt (Sliding Window Deque) trong Concurrency

Trong `GeminiRateLimiter` (`src/core/limits/gemini/limiter.py`), các hàng đợi hai đầu (deque) được sử dụng để theo dõi số lượng yêu cầu (RPM) và token (TPM) trong cửa sổ trượt 60 giây. Để đảm bảo an toàn luồng và tính nguyên tử (atomicity) của các thao tác kiểm tra và cập nhật giới hạn, phương thức `acquire_quota` sử dụng một `asyncio.Lock()`.

**Tác dụng:** `asyncio.Lock` bảo vệ toàn bộ khối mã kiểm tra giới hạn (chiều dài deque, tổng token) và cập nhật (thêm yêu cầu/token mới vào deque). Điều này ngăn chặn tình trạng race condition, đảm bảo rằng các thao tác này diễn ra một cách tuần tự và nhất quán, ngay cả khi có nhiều yêu cầu đồng thời trong cùng một tiến trình.

---

## 7. Cơ chế Lan Truyền Lỗi HTTP Chuẩn (Proper HTTP Error Propagation)

### Vấn đề trước refactor:
Trước đó, khi gặp lỗi Rate Limit (429) hoặc Overloaded (503), Claude Proxy luôn bắt ngoại lệ (exception) ở tầng handler bên trong và trả lời client bằng một chuỗi SSE text thông báo lỗi ở mã trạng thái **HTTP 200 OK**:
`⚠️ [Hệ thống quá tải tạm thời / System Overloaded] ⚠️ ...`

Đối với các ứng dụng chat thông thường, điều này giúp hiển thị giao diện thân thiện cho người dùng. Tuy nhiên, đối với **Agent tự trị (như Claude Code)**, đây là một **anti-pattern nghiêm trọng** vì:
- Agent coi thông báo lỗi này là *phản hồi văn bản hợp lệ* từ mô hình và cố gắng "trò chuyện" tiếp, dẫn đến việc nhồi nhét nội dung lỗi vào lịch sử chat (Context Bloat).
- Agent lặp lại yêu cầu liên tục trong một vòng lặp vô hạn (Infinite Loop), khiến dung lượng context tăng vọt nhưng chỉ số token metadata của API response giả lập lại rất nhỏ (làm thanh hiển thị context của người dùng báo sai chỉ số, ví dụ `0.1k/200k`).

### Giải pháp khắc phục:
- **Lan truyền lỗi HTTP thật:** Loại bỏ try-except giả lập ở Proxy. Khi có lỗi rate limit / timeout, hệ thống lan truyền (propagate) ngoại lệ lên FastAPI để trả về mã lỗi HTTP thật (`429 Rate Limit` hoặc `503 Overloaded`). Client (Claude Code CLI) khi thấy mã lỗi HTTP này sẽ tự động backoff retry ở tầng client một cách tự nhiên mà không làm bẩn context.

---

## 8. Cơ Chế Tính Điểm Key (Priority Scoring)

Logic tính điểm key ưu tiên nằm trong hàm `get_key_priority` (từ `src/core/limits/gemini/limiter.py`), được sử dụng bởi `KeyResolverMixin` để chọn key hiệu quả nhất.

#### **Các yếu tố được xem xét khi tính điểm:**

*   **`active_requests` (Số yêu cầu đang hoạt động):** Đây là yếu tố quan trọng nhất để cân bằng tải theo thời gian thực. Key có ít yêu cầu đang hoạt động hơn sẽ có ưu tiên cao hơn.
*   **`frozen_until` (Thời gian đóng băng):** Các key đang bị đóng băng (chưa hết thời gian `frozen_until`) sẽ có điểm ưu tiên rất thấp hoặc bị loại bỏ hoàn toàn khỏi danh sách ứng viên.
*   **`consecutive_failures` (Số lỗi liên tiếp):** Key có số lỗi liên tiếp gần đây sẽ bị phạt điểm. Số lỗi càng nhiều, hình phạt càng nặng.
*   **`last_success` (Lần thành công gần nhất):** Key có yêu cầu thành công gần đây hơn sẽ được ưu tiên, phản ánh tính ổn định hiện tại của key.
*   **`_score_penalties` (Hình phạt điểm):** Các hình phạt động được áp dụng cho key vì các lý do lỗi khác nhau (ví dụ: `rate_limit`, `unavailable`) sẽ làm giảm điểm của key đó. Các hình phạt này có thời gian hết hạn và được quản lý trong bộ nhớ và đồng bộ với DB.
*   **`model_priority` (Ưu tiên model):** Một điểm ưu tiên cơ bản có thể cấu hình cho các bí danh model khác nhau (được định nghĩa trong `MODEL_PRIORITY` từ `src/core/config/api_config.py`).
*   **Tier (Hạng của tài khoản):** Hạng của tài khoản người dùng (free, premium, admin) ảnh hưởng đến việc những key nào được xem xét. Key của hạng cao hơn có thể được ưu tiên truy cập vào các pool key chất lượng hơn.

#### **Logic tổng thể:**

Hàm `get_key_priority` kết hợp tất cả các yếu tố này để tạo ra một điểm số số học toàn diện. Điểm số càng cao, key càng được ưu tiên. Điểm này sau đó được sử dụng trong thuật toán `_select_key_double_random` (`src/core/router/core/key_resolver.py`) để sắp xếp và chọn key, đảm bảo rằng các yêu cầu được phân phối một cách hiệu quả và linh hoạt nhất, tối ưu hóa hiệu suất và khả năng chống chịu lỗi của hệ thống.

---

## 9. Effort → Thinking Level và Sự Tôn Trọng Dialect Đích (Cross-Mapping)

Client gõ "suy nghĩ kỹ tới mức nào" bằng bốn cách khác nhau, nhưng tất cả đều có một ý nghĩa. Tầng proxy gộp cả bốn về một `thinking_level` duy nhất cho Gemini, nhưng **truyền nguyên bản** khi đích đến là Custom Endpoint để tôn trọng mô hình của bên thứ ba.

| Client gửi | Nguồn | → Đích: Gemini 3 | → Đích: Gemini 2.5 | → Đích: Custom OpenAI | → Đích: Custom Anthropic |
|---|---|---|---|---|---|
| `output_config.effort` | Anthropic Messages API (Claude Code) | `thinking_level` | `thinking_budget` | KHÔNG GỬI GÌ | KHÔNG GỬI GÌ |
| `thinking.budget_tokens` | Anthropic thinking block | xem bảng ngưỡng | `thinking_budget` | KHÔNG GỬI GÌ | `thinking` block nguyên bản |
| `reasoning_effort` | OpenAI Chat Completions | `thinking_level` | `thinking_budget` | `reasoning_effort` nguyên bản | KHÔNG GỬI GÌ |
| `thinking_level` | field riêng của router | trực tiếp | `thinking_budget` | KHÔNG GỬI GÌ | KHÔNG GỬI GÌ |

### Tôn Trọng Endpoint Đích (Pass-through) và Rủi Ro Cross-Mapping
- **Custom Endpoint như một hộp đen**: Router không thể biết Endpoint đích chạy model gì (gpt-4o không hỗ trợ `reasoning_effort`, Claude 3.5 không hỗ trợ `adaptive`, vLLM có thể từ chối các field lạ). Việc Router tự động dịch chéo (ví dụ dịch từ `reasoning_effort` sang `{"type": "enabled"}`) sẽ ngay lập tức khiến các model này văng lỗi HTTP 400 Bad Request, dẫn tới việc Custom Endpoint bị Circuit Breaker đóng băng 120s ngay từ request đầu tiên ("chưa làm gì đã báo Hệ thống quá tải tạm thời").
- **Nguyên tắc "Chỉ Pass-through"**: 
  - **Đích Anthropic**: Router **bảo toàn nguyên văn** chuỗi JSON `thinking` mà client gửi thay vì tự phán đoán. Nếu Client không gửi `thinking` block, tuyệt đối không tự chế block này. Router tự động xóa `reasoning_effort` (nếu Client là OpenAI) để tránh lỗi 400.
  - **Đích OpenAI**: Router truyền nguyên bản `reasoning_effort`. Tuyệt đối không tự chế `reasoning_effort` nếu Client không yêu cầu. Router tự động xóa `thinking` block (nếu Client là Anthropic) để tránh lỗi 400.

### Gộp Cho Gemini
Gemini 3 nhận enum `minimal/low/medium/high`; Gemini 2.5 chỉ có `thinking_budget` nên level được quy đổi qua `low=1024`, `medium=2048`, `high=4096`.

**Vì sao phải gộp.** `max` không phải từ nào Gemini có, nhưng cả Anthropic lẫn OpenAI đều dùng nó cho đỉnh của thang đo — nên nó hợp nhất về `high`. Một effort bị bỏ qua không phải chuyện vô hại: `low` đến nơi như `high` tốn latency và token ở **mọi** lượt gọi, và người dùng không có cách nào biết vì response vẫn hợp lệ.

Thứ tự ưu tiên khi chuyển đổi cho Gemini: `thinking_level` riêng → `output_config.effort` → `thinking.budget_tokens` → `reasoning_effort`. `thinking: {"type": "disabled"}` là ngoại lệ duy nhất được giữ nguyên — nó tắt thinking thay vì hạ mức.

Ngưỡng `budget_tokens` khi chuyển sang Gemini 3:

| Ngưỡng | Level |
|---|---|
| ≥ 8192 | `high` |
| ≥ 4096 | `medium` |
| < 4096 | `low` |

Test: `tests/test_effort_mapping.py` — pin cả hai chiều, kể cả việc effort cao hơn không bao giờ cho ra thinking ít hơn.

### Đặc Thù Gemma 4 (Google AI Studio)
- **Cơ chế suy nghĩ**: Gemma 4 (`gemma-4-26b-a4b-it`, `gemma-4-31b-it`) tích hợp sẵn quy trình suy luận (thinking process). Tuy nhiên, Google AI Studio **nghiêm ngặt chỉ cho phép bật hoặc tắt** thông qua `thinkingLevel`: `"high"` (bật) hoặc `"minimal"` (tắt).
- **Cấm kỵ `thinkingBudget`**: Gemma 4 **hoàn toàn không hỗ trợ** tham số `thinkingBudget` (Google AI Studio sẽ văng lỗi HTTP 400 `Thinking budget is not supported for this model.`), đồng thời từ chối các mức `low` hay `medium` (văng lỗi HTTP 400 `Thinking level is not supported for this model.`).
- **Chuẩn hóa tự động (`sanitize_thinking_for_gemma`)**:
  - Bất kỳ `thinking_budget > 0` hoặc mức thinking hợp lệ nào từ client gửi lên đều được router chuẩn hóa thành `thinking_level="high"`.
  - Tắt thinking (`none`, `off`, `false`, `minimal` hoặc `thinking_budget=0`) được chuẩn hóa thành `thinking_level="minimal"`.
  - Router tuyệt đối loại bỏ trường `thinking_budget` khỏi payload gửi tới Google API khi gọi Gemma.

---

## 10. Lịch sử thay đổi

### 2026-07-06 — `bad_request` & `project_denied` không còn là Hard Freeze
- **File:** `src/core/providers/gemini/utils.py` — `handle_error()`
- **Trước:** `bad_request` và `project_denied` raise `RuntimeError` ngay lập tức → FastAPI trả 500, không thử key khác.
- **Sau:** Freeze key 3600s (`KEY_INVALID_COOLDOWN_SECONDS`), apply penalty, `return` → thử key tiếp theo trong pool.
- **Lý do:** Một key invalid/denied không có nghĩa tất cả key đều invalid. Router nên thử key khác trước khi bỏ cuộc.

### 2026-10-06 — `output_config.effort` được map, effort cũ không còn bị đảo ngược
- **File:** `src/api/opencode_proxy/handler/proxy.py` — `_extract_thinking_params()`
- **Trước:** `output_config` không được đọc (mọi effort của Claude Code rơi về `low`); `reasoning_effort: high` → `low`; `reasoning_effort: medium` → `include_thoughts: False` tức **tắt** thinking; `budget_tokens` cao nhất chỉ lên `medium`; `thinking: disabled` bị `return {}` nuốt mất.
- **Sau:** cả bốn nguồn quy về một level; `max` → `high`; `disabled` giữ nguyên.
- **Lý do:** effort là tín hiệu người dùng đưa ra, và map sai nó không tạo lỗi nào để báo — model vẫn trả lời, chỉ là nghĩ sai mức.

### 2026-10-06 — `retry_attempt` luôn bằng 0 trong luồng stream
- **File:** `src/core/orchestration/pool_manager.py` — `call_stream()`
- **Trước:** `pool_try = -1` nằm **trong** vòng `while`, ngay trên `pool_try += 1` → biến thành 0 mỗi vòng. `retry_attempt` truyền vào `_resolve_model()` và `reserve_key()` luôn bằng 0. Trong `call_nonstream()` cùng biến đó nằm **ngoài** vòng lặp nên tăng đúng.
- **Sau:** đặt `pool_try = -1` trước vòng `while`, khớp với `call_nonstream`.
- **Hệ quả:** `retry_attempt` là tham số bật **Extreme Checking** ở attempt ≥ 10 (mục 5). Luồng stream lặp quá 10 lần vẫn không bao giờ bật van an toàn đó, trong đúng lúc tải nặng nhất — trong khi non-stream thì có. Hai nhánh là bản sao của nhau nên lệch nhau là sai.
- **Test:** `tests/test_retry_counter.py` pin bất biến `seen == list(range(len(seen)))` cho cả hai đường. Đưa lại lỗi thì 4 test đỏ.

### 2026-10-07 — Mở rộng Pool 6 Flash & 3 Lite, Dynamic Resolution và Đồng bộ Hai chiều ENV ↔ DB
- **Files:** `src/core/config/api_config.py`, `src/core/limits/account_limiter/capacity.py`, `src/backend/models/config.py`, `src/server/openai_server/routes/app_init.py`
- **Thay đổi:**
  - **Mở rộng Pool:** `gemini-flash` bao gồm 6 model thành viên (`gemini-flash-38`, `37`, `36`, `35`, `30`, `25`); `gemini-flash-lite` gồm 3 model (`gemini-flash-35-lite`, `gemini-flash-lite`, `gemini-flash-25-lite`).
  - **Loại bỏ Hardcode:** `capacity.py` chuyển sang hàm `_resolve_pool_model_ids()` tự động suy luận danh sách backing models từ `MODEL_POOLS` & `AVAILABLE_MODELS`.
  - **Tính toán Pool động:** `api_config.py` tự động tính tổng RPM/TPM/RPD từ các members và tự sinh chuỗi nhãn hiển thị `display` dựa trên thành viên hiện có.
  - **Đồng bộ hai chiều ENV ↔ DB:** File watcher tự động đẩy thay đổi từ `.env` vào SQLite `model_config`, đồng thời Admin UI khi cập nhật DB sẽ tự ghi ngược lại file `.env`.

### 2026-10-15 — Chuẩn Hóa Vòng Đời Reservation, Truy Vết Metadata Xuyên Suốt & Tối Ưu Hiệu Năng Phản Hồi
- **Files:** `src/core/router/core/router.py`, `src/core/router/core/key_resolver.py`, `src/core/orchestration/pool_manager.py`, `src/core/runtime/request_context.py`, `src/backend/keys/status.py`, `src/backend/schema.py`, `src/backend/_db.py`, `src/server/openai_server/routes/app_init.py`
- **Thay đổi:**
  - **Idempotent Key Reservation (`reservation_id`):** `reserve_key()` cấp phát thẻ định danh duy nhất cho từng lượt mượn key. `release_key()` kiểm tra `reservation_id` để đảm bảo mỗi lượt mượn chỉ được trả đúng một lần. Triệt tiêu hoàn toàn lỗi underflow hoặc kẹt giữ chỗ `active_requests` khiến request sau bị block 1s–3s trong acquire loop.
  - **Giảm độ trễ từ vài giây xuống mili-giây:** Request mới đến lấy được key rảnh ngay tức thì trong < 1ms mà không bị rơi vào vòng lặp chờ phòng thủ (`backoff/sleep`).
  - **Cô lập lỗi Key-local:** Lỗi xác thực/cục bộ (`invalid_key`, `permission_denied`) chỉ freeze key vi phạm, không còn làm tụt điểm `_model_health` của cả model alias.
  - **Lan truyền Request ID & Metadata Transition:** Tích hợp `request_context.py` dùng `ContextVar` truyền `request_id` từ HTTP ingress xuyên qua các coroutine bất đồng bộ tới `PoolManager`. Mọi thay đổi trạng thái của key và custom endpoint đều được ghi nhận kèm metadata vào bảng `key_transitions` và `endpoint_transitions` trong SQLite.
  - **Provider-Aware RTK:** Tách biệt nhánh nén RTK: `internal` nén cả user/tool khi cần hạ tải context; `custom` chỉ nén technical tool results để không làm nghẽn hoặc nuốt reasoning block của model ngoài.

---

## 11. Các Biến Môi Trường Chính

### Virtual Pools & Backing Model Limits
| Biến Môi Trường | Mặc Định | Mô Tả |
|---|---|---|
| `GEMINI_FLASH_RPM` | _(dynamic)_ | Tổng RPM cho `gemini-flash` pool — tự động cộng dồn từ members đang active qua `_recompute_pool_aggregates()` |
| `GEMINI_FLASH_TPM` | _(dynamic)_ | Tổng TPM cho `gemini-flash` pool — dynamic, không hardcode |
| `GEMINI_FLASH_RPD` | `235` | Tổng RPD cho `gemini-flash` pool (50+15+50+50+50+20) |
| `GEMINI_FLASH_LITE_RPM` | _(dynamic)_ | Tổng RPM cho `gemini-flash-lite` pool — dynamic |
| `GEMINI_FLASH_LITE_TPM` | _(dynamic)_ | Tổng TPM cho `gemini-flash-lite` pool — dynamic |
| `GEMINI_FLASH_LITE_RPD` | `540` | Tổng RPD cho `gemini-flash-lite` pool (20+500+20) |
| `POOL_SWAP_FAILURES` | `5` | Số lần thất bại liên tiếp trước khi đổi sang pool member tiếp theo |
| `POOL_MAX_ATTEMPTS` | `15` | Số lần thử lại tối đa qua toàn bộ các pool members |
| `POOL_RETRY_SECONDS` | `120` | Ngân sách thời gian retry của một pool request (cho phép xoay qua nhiều key/member) |
| `ROUTER_API_STREAM_OPEN_TIMEOUT_SEC` | `180` | Giới hạn mở stream trước khi xoay member; tách khỏi timeout request tổng |
| `ROUTER_API_REQUEST_TIMEOUT_SEC` | `600` | Giới hạn thời gian tổng thể của upstream HTTP request (10 phút) |
| `STREAM_STALL_TIMEOUT` | `900.0` | Thời gian im lặng tối đa giữa 2 chunk liên tiếp (15 phút, chống stall cho deep thinking) |
| `COMMITTED_STREAM_CAP` | `1800.0` | Tổng thời lượng tối đa cho một stream sau khi đã nhận chunk đầu tiên (30 phút) |
| `CUSTOM_ENDPOINT_TIMEOUT` | `900` | Timeout tổng cho custom endpoint upstream (15 phút) |
| `CUSTOM_ENDPOINT_CHUNK_TIMEOUT` | `600.0` | Timeout đọc từng chunk cho custom endpoint stream (10 phút) |
| `KEY_429_COOLDOWN_SECONDS` | `15` | Thời gian cooldown khi key dính rate limit tạm thời |
| `KEY_INVALID_COOLDOWN_SECONDS` | `3600` | Thời gian đóng băng khi key invalid |
| `MODEL_CONTEXT_LENGTH` | `185000` | Ngữ cảnh an toàn client (Spoofing: bảo vệ hạn mức 250k TPM Google AI Studio) |
| `GEMMA_CONTEXT_LENGTH` | `14000` | Ngữ cảnh an toàn cho Gemma 4 (bảo vệ hạn mức 16k TPM Google AI Studio) |

---

## 12. Safe Context Spoofing, Combo Model Mix & RTK Truncation

### 12.1. Vấn Đề TPM Google AI Studio vs Physical Context Window
Mặc dù các mô hình Gemini Flash có dung lượng Attention Context vật lý là 1,048,576 tokens, Google AI Studio trên Free Tier áp đặt hạn mức thông lượng nghiêm ngặt **250,000 Tokens Per Minute (TPM)** tính theo cửa sổ lăn 60 giây. Nếu một request đơn lẻ (hoặc tổng các request trong 1 phút) vượt quá 250k tokens, Google lập tức trả về mã lỗi `HTTP 429 ResourceExhausted`.

Để giải quyết triệt để vấn đề này mà không cần cắt cụt mù quáng:
1. **Pre-emptive Context Spoofing (185k & 14k):** Gateway báo cho client (Claude Code, OpenCode) thông qua `/v1/models` và Anthropic completion headers rằng context length của Flash/Lite là **185,000 tokens** (và Gemma 4 là **14,000 tokens**). Khi prompt client đạt ~140k–150k, client tự động kích hoạt tính năng compact/summarize tự thân, tạo ra khoảng đệm an toàn 35k–45k tokens bảo vệ tuyệt đối hạn mức 250k TPM.
2. **RTK (Real-Time Kernel) Smart Truncation:** Nâng cấp bộ lọc `rtk.py` xử lý thông minh cả 2 điều kiện:
   - Cắt tỉa theo dòng: các khối dữ liệu $\ge 250$ dòng được giữ lại 50 dòng đầu và 50 dòng cuối.
   - Cắt tỉa theo byte: các khối dữ liệu đơn dòng hoặc ít dòng nhưng kích thước $> 8192$ bytes được giữ lại 4000 ký tự đầu và 2000 ký tự cuối kèm thông báo cắt tỉa.
   - Hỗ trợ đầy đủ `role: "tool"` (OpenAI format) và block `tool_result` (Anthropic format).

### 12.2. Combo Model Mix & Quy Tắc MIN Context
Tính năng Combo Model Mix cho phép người dùng tự mix nhiều System Models và Custom Endpoints thành một Model Alias duy nhất với 2 chiến lược:
- **Waterfall:** Thử lần lượt từng target theo thứ tự cấu hình, tự động chuyển sang target tiếp theo khi target trước gặp lỗi hoặc hết quota.
- **Load Balance:** Phân phối request ngẫu nhiên có trọng số (weighted random) giữa các targets.

**Quy tắc MIN Context Length:**
Khi người dùng kết hợp các model trong Combo (ví dụ Gemini Flash 185k và Custom Endpoint 64k), context length trả về cho client được tự động tính theo công thức:
$$\text{context\_length} = \min_{i}(\text{Target}_i.\text{context\_length})$$
Quy tắc này đảm bảo khi xảy ra fallback từ Target A sang Target B, prompt không bao giờ bị tràn context window của target nhỏ nhất.

### 12.3. Multi-Dialect Stream Keep-Alive & Chống Timeout Tác Vụ Suy Luận Sâu (Cherry Studio & Claude Code)
Để đối phó với các tác vụ khó đòi hỏi suy luận kéo dài (deep thinking) hoặc các model lý luận phức tạp thường tạm dừng phát chunk từ 60–180 giây:

1. **Chuẩn Hóa Timeout Hào Phóng Cho Router:**
   - Nâng `STREAM_STALL_TIMEOUT` lên **900.0s** (15 phút).
   - Nâng `_COMMITTED_STREAM_CAP` lên **1800.0s** (30 phút).
   - Nâng timeout Custom Endpoint lên **900.0s** (chunk timeout: 600.0s).
   - Nâng `REQUEST_TIMEOUT_SECONDS` lên **600.0s**, `STREAM_OPEN_TIMEOUT_SECONDS` lên **180.0s**.

2. **Cơ Chế Multi-Dialect Keep-Alive:**
   - Gửi ngay comment `: keepalive-init\n\n` khi thiết lập kết nối SSE HTTP 200.
   - Định kỳ phát comment `: keepalive\n\n` mỗi 2.5s–3.0s nếu upstream chưa có token mới.
   - Trong `_custom_safe_stream()` (OpenAI Chat API), phát kèm chunk rỗng `data: {"choices": [{"delta": {}}]}` mỗi 2.5s để reset timeout của client fetch/EventSource.

3. **Lưu Ý Cấu Hình Cherry Studio:**
   - Cherry Studio mặc định cấu hình timeout nhà cung cấp là **120 giây** ("Thời gian chờ của nhà cung cấp" / "Provider timeout").
   - Khi chạy các tác vụ suy luận phức tạp hoặc prompt dài, người dùng cần vào **Cài đặt (Settings) -> Nhà cung cấp (Providers) -> Router API -> Nâng Timeout lên 600s - 1800s** (hoặc đặt 0 / tắt timeout) để tránh Cherry Studio tự ý ngắt kết nối khi server vẫn đang tính toán hợp lệ.

4. **Khắc Phục Lỗi Fallback Giả Sau WebSearch Trong Claude Code Proxy:**
   - **Stream sớm (Early Streaming):** Không đệm toàn bộ phản hồi tổng hợp sau tìm kiếm nếu văn bản bắt đầu bằng câu tự nhiên (khác tiền tố `[Web Search Results`).
   - **Nhận diện Echo chính xác:** `is_raw_search_context()` chỉ lọc những phản hồi lặp lại nguyên khối `[Web Search Results` mà không có câu trả lời tổng hợp phía sau (`len(after_footer) < 50`).
   - **Bảo toàn Tool Call:** Nếu sau khi tìm kiếm, model sinh ra tool call mới (như `Read`, `Bash`, `Agent`), hệ thống giữ nguyên và phát `tool_use`, không kích hoạt nhầm fallback `"Mình đã thực hiện tìm kiếm nhưng model chưa tạo được phần tổng hợp. Vui lòng thử lại."`.

---

## 13. Chuẩn Hóa Vòng Đời Key Reservation & Bước Nhảy Vọt Hiệu Năng (Latency Drop From Seconds to Milliseconds)

### 13.1. Phân Tích Nguyên Nhân Nghẽn Tải Ở Code Cũ (Root Cause Analysis)
Trước khi refactor, hệ thống thường xuyên gặp hiện tượng phản hồi bị delay từ 1 đến vài giây ở hot path (ngay cả khi server không chịu tải lớn). Nguyên nhân gốc rễ:
1. **Trạng Thái "Kẹt Phanh Ảo" do Untracked Key Release:**
   - Khi request được điều phối, router tăng `active_requests += 1` cho key được chọn.
   - Khi request kết thúc hoặc retry/exception bất đồng bộ, các hàm `release_key()` bị gọi không kèm định danh lượt mượn. Một số trường hợp bị double release hoặc bị lọt không release, dẫn đến việc `active_requests` bị lệch (khác 0 dù key đã rảnh).
   - Trong lần gọi tiếp theo, hàm `_select_key_double_random()` thấy tất cả các key đều có `active_requests > 0` nên cho rằng pool đang bận, kích hoạt vòng lặp chờ (`acquire loop` với backoff `asyncio.sleep`) tốn từ 1s–3s chỉ để chờ một key vốn dĩ đang hoàn toàn rảnh!
2. **Hiệu Ứng Domino Hạ Điểm Health (Cascading Health Penalty):**
   - Khi một key gặp lỗi cục bộ của chính key đó (như `invalid_key`, `permission_denied`), hàm xử lý lỗi cũ đã gọi `update_model_health(_alias, success=False)` làm tụt điểm tín nhiệm của toàn bộ model alias.
   - Model alias bị hạ điểm khiến router chèn thêm khoảng hoãn (penalty delay) và tìm cách swap pool, làm chậm toàn bộ các request sau đó.
3. **I/O Blocking Trong Hot Path:**
   - Các thao tác cập nhật DB trước đây thiếu tách bạch giữa dữ liệu runtime in-memory và audit log bền vững.

### 13.2. Kiến Trúc Mới: Idempotent Reservation Lifecycle
1. **Cấp phát định danh duy nhất (`reservation_id`):**
   - Khi `reserve_key()` được gọi, nó sinh ra một chuỗi `reservation_id` (UUID4) độc nhất cùng mốc thời gian `reserved_at`.
   - Đối tượng reservation được đăng ký vào dictionary `_reservations` của `APIRouter`.
2. **Thu hồi Idempotent an toàn tuyệt đối:**
   - Khi `release_key(key, reservation_id=...)` được gọi, hệ thống kiểm tra sự tồn tại của `reservation_id`.
   - Nếu đã release rồi, router từ chối xử lý tiếp và cảnh báo, ngăn chặn triệt để hiện tượng trừ âm `active_requests`.
   - Nếu hợp lệ, slot được hoàn trả ngay lập tức và bản ghi reservation được đánh dấu là đã giải phóng.
3. **Bộ dọn dẹp bộ nhớ tự động (`_cleanup_reservations_locked`):**
   - Tự động dọn dẹp các reservation đã release hoặc các reservation bị treo quá thời gian timeout (quá 1800s), đảm bảo RAM không bị rò rỉ kể cả khi hệ thống chạy hàng tháng với hàng triệu lượt gọi.
4. **Kết quả:**
   - Triệt tiêu 100% các vòng lặp chờ ảo. Thời gian phân giải và cấp phát key giảm từ hàng giây xuống **dưới 1ms**, đưa tốc độ phản hồi gateway về mức tức thì.

---

## 14. Cơ Chế Lan Truyền Request ID Xuyên Suốt & Nhật Ký Chuyển Đổi Trạng Thái (State Transitions)

### 14.1. Request Context Toàn Cục (`request_context.py`)
Để hỗ trợ việc điều tra sự cố (observability & tracing) ở môi trường production mà không cần truyền tham số thủ công qua hàng chục tầng hàm:
- Module `src/core/runtime/request_context.py` sử dụng `contextvars.ContextVar` lưu trữ `request_id` cho từng async task / coroutine.
- Middleware HTTP `request_context_middleware` tại `src/server/openai_server/routes/app_init.py` tự động bắt header `X-Request-ID` từ client (hoặc tự sinh chuỗi UUID hex) và gán vào `ContextVar` đồng thời trả ngược lại trong response header `X-Request-ID`.
- Mọi tầng bên dưới (`PoolManager`, `APIRouter`, `GeminiCaller`, `CustomEndpointManager`) khi ghi log hoặc thực hiện chuyển đổi trạng thái chỉ cần gọi `get_request_id()` để lấy đúng request ID mà không làm rối chữ ký hàm (clean interface).

### 14.2. Bảng Chuyển Đổi Trạng Thái Có Metadata (`key_transitions` & `endpoint_transitions`)
Mọi biến cố làm thay đổi trạng thái của Key hoặc Custom Endpoint đều được lưu vết dạng có cấu trúc vào SQLite:
1. **Bảng `key_transitions`:**
   - Lưu trữ: `transition_id`, `request_id`, `key_suffix` (chỉ lưu 8 ký tự cuối, che giấu bí mật), `model_id`, `from_state`, `to_state`, `reason`, `duration_ms`, `error_type`, `meta_json`, `created_at`.
   - Cho phép tra cứu lịch sử sự cố theo từng request ID hoặc theo từng key thông qua hàm `query_key_transitions_db()`.
2. **Bảng `endpoint_transitions`:**
   - Lưu trữ: `transition_id`, `request_id`, `endpoint`, `from_state`, `to_state`, `reason`, `failure_count`, `cooldown_seconds`, `meta_json`, `created_at`.
   - Cho phép tra cứu lịch sử nhảy Circuit Breaker của Custom Endpoint thông qua `query_endpoint_transitions_db()`.
3. **Cơ chế Tự động Nâng cấp Schema (`auto_migrate_schema` & `_BOOTSTRAP_DDL`):**
   - Toàn bộ các cột và bảng mới được đăng ký tại `_SCHEMA_COLUMN_REGISTRY` trong `src/backend/schema.py` để tự động chạy `ALTER TABLE ADD COLUMN` khi khởi động trên database cũ.
   - Đồng thời được khai báo đồng nhất tại `_BOOTSTRAP_DDL` trong `src/backend/_db.py` để đảm bảo máy mới khởi tạo DB rỗng không bao giờ bị lệch cấu trúc.

---

## 15. Phân Định RTK Theo Provider & Cô Lập Circuit Breaker Cho Custom Endpoint

### 15.1. RTK Theo Phân Loại Provider (`provider_kind`)
Thuật toán nén ngữ cảnh thời gian thực (RTK) tại `src/logical_HQ_translator/compression/rtk.py` nhận tham số `provider_kind`:
- **`provider_kind="internal"` (Gemini, Claude):** Nén cả nội dung văn bản dài của người dùng và các kết quả tool lớn khi cần hạ tải context để bảo vệ hạn mức 250k TPM.
- **`provider_kind="custom"` (9router, external OpenAI-compatible):** Tuyệt đối không can thiệp vào văn bản của người dùng hoặc các khối suy luận (reasoning/thinking) của mô hình. Chỉ cắt tỉa các khối `tool_result`/`tool_output` kỹ thuật quá khổ. Điều này ngăn chặn hiện tượng model ngoài bị cụt output hoặc trả về phản hồi rỗng do thiếu dữ liệu hội thoại.

### 15.2. Cô Lập Circuit Breaker Cho Custom Endpoint
- Custom Endpoint sở hữu cơ chế Circuit Breaker hoàn toàn độc lập nằm trong `CustomEndpointManager._circuit_breaker`.
- Trạng thái nhảy breaker của Custom Endpoint (ví dụ sau 3 lỗi liên tiếp) hoàn toàn **không chia sẻ và không ảnh hưởng** đến Circuit Breaker hay pool key của Gemini.
- Khi một Custom Endpoint bị ngắt (Tripped), các lượt gọi tiếp theo trong pool tự động chuyển hướng qua các member khác hoặc Gemini members mà không gây tắc nghẽn toàn hệ thống.

---

## 16. Quy Tắc Định Tuyến Gemma 4 Standalone & Cơ Chế Chống Cướp Model (Anti-Hijacking Policy)

### 16.1. Bản Chất Mô Hình Gemma 4 (26B/27B và 31B)
1. **Chế độ Vận Hành Độc Lập (Standalone Mode):**
   - Gemma 4 (`gemma-4-26b-a4b-it` và `gemma-4-31b-it`) chạy trực tiếp trên các API Key của Google AI Studio/Gemini, nhưng hoạt động ở chế độ Standalone (nằm ngoài `MODEL_POOLS` của Gemini Flash/Lite).
   - Key được cấp phát qua `router.reserve_key(model_alias, model_id)` và tuân thủ chặt chẽ vòng đời `reservation_id`.
2. **Chuẩn Hóa Đa Dạng Bí Danh (Canonical Alias Normalization):**
   - Phía client gửi rất nhiều biến thể tên gọi khác nhau do thói quen cấu hình:
     - `gemma-4-27b`, `gemma-27b`, `gemma-4-26b`, `gemma-4-26b-it`, `gemma-4-26b-a4b-it`, `gemma-4` $\rightarrow$ Tự động chuẩn hóa về alias chuẩn: **`gemma-4-26b`** (backing model: `gemma-4-26b-a4b-it`).
     - `gemma-4-31b`, `gemma-31b`, `gemma-4-31b-it` $\rightarrow$ Tự động chuẩn hóa về alias chuẩn: **`gemma-4-31b`** (backing model: `gemma-4-31b-it`).
   - Việc chuẩn hóa này diễn ra ngay tại `router.resolve_model_alias()`, giúp chia sẻ trọn vẹn thống kê rate limit, token bucket và key pool lành mạnh.
3. **Ngưỡng Ngữ Cảnh An Toàn (Safe Context Window):**
   - `GEMMA_CONTEXT_LENGTH = 14000` tokens (thay vì 185k của Gemini Flash) để bảo vệ model khỏi tình trạng quá tải context vật lý (262k) hoặc bị chậm đột ngột.
4. **Cơ Chế Suy Luận (Thinking Configuration):**
   - Google API của Gemma 4 hỗ trợ các mức `thinking_level`: `"low"`, `"medium"`, `"high"`, `"minimal"`.
   - **Tuyệt đối cấm** gửi tham số `thinking_budget` tới Google cho Gemma (Google API sẽ từ chối với HTTP 400 Bad Request). Mọi `thinking_budget` nếu client gửi tới sẽ tự động được sanitize sang `thinking_level`.

### 16.2. Cơ Chế Chống Cướp Model Native (Anti-Hijacking Policy)
**Lỗ Hổng Lịch Sử Cần Tránh:**
Trước đây, khi một tài khoản đăng ký custom endpoint mà không cấu hình `pool_assignments` (ví dụ endpoint `9router` với `pool_assignments = {}`), hệ thống tại:
- `src/logical_HQ_translator/model/resolver.py` (Step 1)
- `src/core/providers/custom_endpoint/passthrough.py` (Priority 4)
đã nhận định sai lầm rằng đây là một "100% override endpoint" của tài khoản, dẫn đến việc cướp sạch mọi yêu cầu gọi model (`gemma-4-26b`, `gemma-4-31b`, `gemini-flash`) và ép đổi sang `target_model = (enabled_models or all_models)[0]` (chính là `ag/gemini-3.1-pro-high` trên 9router). Khi 9router gặp sự cố hoặc ngắt circuit breaker, toàn bộ model Gemma và Gemini của client bị tê liệt hoàn toàn.

**Quy Tắc Định Tuyến Bất Biến (Strict Rule):**
1. **Ưu Tiên Tuyệt Đối Cho Model Native:**
   - Mọi model thuộc diện Native (`is_native_model`: chứa `gemma`, `gemini`, `flash`, hoặc nằm trong `AVAILABLE_MODELS` / `MODEL_POOLS`) **KHÔNG BAO GIỜ** bị rơi vào luồng override mặc định của Custom Endpoint ở cấp tài khoản.
   - `resolve_custom_endpoint_for_request()` sẽ lập tức trả về `None` khi nhận diện model là Native.
2. **Khớp Model Rõ Ràng (Explicit Model Matching):**
   - Step 1 của `_resolve_model()` chỉ được phép chuyển hướng request sang Custom Endpoint của tài khoản khi và chỉ khi:
     - Model yêu cầu là một model tùy chỉnh ngoài (không phải Native).
     - **VÀ** model đó thực sự khớp với danh sách model mà endpoint hỗ trợ (`_match_endpoint_model(model_alias, candidates)`).
   - Nghiêm cấm hành vi lấy phần tử đầu tiên `[0]` để ép model tùy tiện.

### 16.3. Minh Bạch Lỗi Khi Streaming (No Silent Empty Stream)
- Khi gặp sự cố kết nối hoặc lỗi upstream trong quá trình streaming tại `chat.py` hoặc `opencode_routes.py`, hệ thống không bao giờ được phát ra một chunk rỗng `{"delta": {}, "finish_reason": "stop"}` làm client tưởng lầm request thành công trong 1s nhưng nhận về output trống.
- Chế độ an toàn bắt buộc phải phát rõ nội dung lỗi trong delta:
  `{"choices": [{"index": 0, "delta": {"content": f"\n\n[Error: {e}]"}, "finish_reason": "stop"}]}`
  giúp người dùng và client nắm bắt chính xác nguyên nhân kỹ thuật để xử lý.


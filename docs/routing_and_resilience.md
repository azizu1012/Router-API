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
    Facade -->|Thành công| ClientSuccess[Trả về Client]
    Facade -->|Lỗi 429/503| PMRetry[PoolManager: Phạt Key & Backoff Retry]
```

1. **Proxy Layer (Tầng chuyển đổi):** [ClaudeProxy](file:///d:/AI_Projects/router_api/src/api/claude_proxy/handler/proxy.py) và [OpenCodeProxy](file:///d:/AI_Projects/router_api/src/api/opencode_proxy/handler/proxy.py) chỉ đóng vai trò chuyển đổi định dạng request/response từ client thành định dạng dùng chung của hệ thống. Tầng này không chứa logic chọn key hay retry.
2. **PoolManager (Tầng điều phối vòng lặp):** [PoolManager](file:///d:/AI_Projects/router_api/src/core/pool_manager.py) quản lý vòng lặp xoay vòng (rotation) qua các pool member và thực hiện retry khi gặp lỗi tạm thời.
3. **KeyResolver & Router (Tầng quyết định):** [KeyResolver](file:///d:/AI_Projects/router_api/src/core/router/core/key_resolver.py) thực hiện việc tính toán độ ưu tiên (Priority Score), kiểm tra giới hạn RPM/TPM thực tế của từng key, và chọn key.

---

## 2. Cơ Chế Xử Lý Lỗi 429 và 503 (Soft Handling vs Hard Freeze)

Để duy trì hiệu suất cao, hệ thống phân loại lỗi thành hai nhóm chính: **Lỗi tạm thời (Transient Errors)** và **Lỗi vĩnh viễn (Permanent Errors)**.

### Phân Loại Chi Tiết
- **Transient Errors:** Gồm có lỗi Rate Limit (429), Service Unavailable/Overloaded (503), Gateway Timeout (504), lỗi mạng tạm thời hoặc các lỗi không xác định từ máy chủ Google API.
- **Permanent Errors:** Gồm các lỗi như API Key không hợp lệ (`invalid_key`), tài khoản bị khóa/hết hạn thanh toán (`billing_error`), hoặc yêu cầu sai cấu trúc (`bad_request`).

### Sự khác biệt trong cách xử lý:

| Đặc tính | Lỗi Tạm Thời (429, 503, Timeout) | Lỗi Vĩnh Viễn (invalid_key, billing_error) |
| :--- | :--- | :--- |
| **Trạng thái key** | **Soft Handling**: Cooldown cực ngắn (`KEY_429_COOLDOWN_SECONDS` ≈ 8-15 giây). | **Hard Freeze**: Đóng băng dài hạn (`KEY_INVALID_COOLDOWN_SECONDS` = 3600 giây). |
| **Xếp hạng Priority** | Áp dụng hình phạt giảm điểm tạm thời (`apply_error_penalty` trừ điểm score) để tránh chọn lại ngay. | Đóng băng vĩnh viễn trên RAM & DB, không cho phép chọn. |
| **Xử lý Pool** | Không tăng chỉ số lỗi vĩnh viễn của thành viên pool (`pool.record_failure`), chỉ tích lũy bộ đếm tạm thời để chuẩn bị swap model khi vượt quá giới hạn. | Gọi `pool.record_failure` ngay lập tức để swap sang model thành viên khác hoặc custom endpoint khác. |
| **Độ trễ Retry** | Áp dụng thuật toán **Timing Jitter** rồi thử lại. | Chuyển đổi key hoặc model ngay lập tức. |

---

## 3. Thuật Toán Double Random (Tránh Thundering Herd & Key Collision)

Khi hệ thống xử lý đồng thời hàng chục request (high concurrency), nếu tất cả các luồng đều chọn key "tốt nhất" (Greedy Selection) hoặc thử lại cùng một lúc (Synchronized Retry), hệ thống sẽ ngay lập tức kích hoạt lỗi 429 hàng loạt trên key đó (Rate Limit Cascade). 

Để giải quyết triệt để vấn đề này, Router API v2 triển khai thuật toán **Double Random** gồm 2 lớp bảo vệ ngẫu nhiên độc lập:

### Lớp 1: Ngẫu nhiên hóa thời gian chờ (Timing Jitter)
Trong hàm `_retry_delay`, thay vì chờ một khoảng thời gian cố định dạng lũy thừa ($1s, 2s, 4s...$), hệ thống tính toán một độ lệch ngẫu nhiên (Jitter) khoảng $\pm 20\%$ dựa trên khoảng cách cơ sở:

```python
def _retry_delay(attempt: int) -> float:
    import random
    if attempt >= config.POOL_SWAP_FAILURES * 2:
        return random.uniform(0.3, 0.7)
    base = min(config.GEMINI_API_KEY_INTERVAL * (2 ** attempt), config.KEY_429_COOLDOWN_SECONDS * 2)
    jitter = random.uniform(-base * 0.2, base * 0.2)
    return max(config.GEMINI_API_KEY_INTERVAL, base + jitter)
```
*Tác dụng:* Phân tán thời điểm gửi lại request của các luồng đang chờ, tránh hiện tượng "Thundering Herd" (nhiều client cùng ùa vào gửi yêu cầu tại cùng một mili-giây).

### Lớp 2: Ngẫu nhiên hóa lựa chọn Key (Priority Selection Randomization)
Trong hàm `reserve_key`, thay vì luôn chọn key có điểm Priority Score cao nhất hoặc ít tải nhất, hệ thống thực hiện:
1. Lọc và sắp xếp toàn bộ danh sách key khả dụng theo thứ tự ưu tiên: ít `active_requests` nhất trước, sau đó là điểm Priority cao nhất, cuối cùng là ít lỗi liên tục nhất.
2. Cắt lấy **Top 50%** key tốt nhất trong danh sách ứng viên khỏe mạnh.
3. Chọn ngẫu nhiên một key từ nhóm 50% này (`random.choice`).

```python
# Lọc lấy top 50% key khỏe nhất để phân tán tải
top_50_percent = int(len(candidates_with_priority) * 0.5)
chosen_cand = random.choice(candidates_with_priority[:max(1, top_50_percent)])
selected_key = chosen_cand[1]
```
*Tác dụng:* Dưới áp lực tải cao, các request sẽ được phân phối đều trên nhiều key thuộc nhóm "tốt", giảm thiểu tối đa xác suất hai luồng xử lý song song chọn trúng cùng một API Key và gây ra xung đột hạn ngạch (quota collision).

---

## 4. Chế Độ Phòng Chống Nghẽn Tải Cực Cao (Extreme Checking Logic)

Khi một request bị lỗi liên tục và số lần thử lại đạt tới giới hạn nghiêm trọng (`attempt >= 10`), hệ thống sẽ kích hoạt bộ lọc bảo vệ cực đoan (**Extreme Checking**):

1. **Siết chặt hạn ngạch (Throttling down to 70%):** RPM và TPM tối đa cho phép của key bị ép giảm xuống còn 70% công suất thiết kế.
2. **Không chấp nhận chia sẻ tải:** Chỉ những key hoàn toàn rảnh rỗi (`active_requests == 0`) và không trong thời gian cooldown mới được phép sử dụng.
3. **Ưu tiên yêu cầu nhỏ:** Đối với các prompt lớn vượt giới hạn 70% TPM, yêu cầu sẽ bị chặn ngay lập tức nếu key có bất kỳ hoạt động nào trong vòng 60 giây qua, bảo vệ key khỏi bị quá tải hoàn toàn.

Cơ chế này hoạt động như một van an toàn (Safety Valve) giúp hạ nhiệt hệ thống khi đang xảy ra tình trạng bão request 429 trên diện rộng.

---

## 5. Cơ chế Lan Truyền Lỗi HTTP Chuẩn (Proper HTTP Error Propagation)

### Vấn đề trước refactor:
Trước đó, khi gặp lỗi Rate Limit (429) hoặc Overloaded (503), Claude Proxy luôn bắt ngoại lệ (exception) ở tầng handler bên trong và trả lời client bằng một chuỗi SSE text thông báo lỗi ở mã trạng thái **HTTP 200 OK**:
`⚠️ [Hệ thống quá tải tạm thời / System Overloaded] ⚠️ ...`

Đối với các ứng dụng chat thông thường, điều này giúp hiển thị giao diện thân thiện cho người dùng. Tuy nhiên, đối với **Agent tự trị (như Claude Code)**, đây là một **anti-pattern nghiêm trọng** vì:
- Agent coi thông báo lỗi này là *phản hồi văn bản hợp lệ* từ mô hình và cố gắng "trò chuyện" tiếp, dẫn đến việc nhồi nhét nội dung lỗi vào lịch sử chat (Context Bloat).
- Agent lặp lại yêu cầu liên tục trong một vòng lặp vô hạn (Infinite Loop), khiến dung lượng context tăng vọt nhưng chỉ số token metadata của API response giả lập lại rất nhỏ (làm thanh hiển thị context của người dùng báo sai chỉ số, ví dụ `0.1k/200k`).

### Giải pháp khắc phục:
- **Tách biệt luồng xử lý:** 
  - Với **Main Agent** (luồng chat chính): Loại bỏ try-except giả lập ở Proxy. Khi có lỗi rate limit / timeout, hệ thống sẽ lan truyền (propagate) lỗi lên FastAPI để trả về mã lỗi HTTP thật (`429 Rate Limit` hoặc `503 Overloaded`). Client (Claude Code CLI) khi thấy mã lỗi HTTP này sẽ tự động backoff retry ở tầng client một cách tự nhiên mà không làm bẩn context.
  - Với **Sub-Agent** (luồng chạy lệnh ngầm song song): Tiếp tục giữ cơ chế bắt lỗi và trả về text thông báo mô phỏng mã 200 OK ở hàm `handle_sub_agent_error` để tránh việc CLI của client bị crash đột ngột khi các sub-agent thăm dò tệp bị lỗi tạm thời.

---

## 6. Quyết Định Định Tuyến Sub-Agent Linh Hoạt (Dynamic Sub-Agent Model Selection)

### Vấn đề trước refactor:
Tầng xác thực `auth.py` nhận diện sub-agent và tính toán quota dựa trên cấu hình tùy chỉnh của tài khoản (`subagent_model`) hoặc biến môi trường (`SUB_AGENT_MODEL`). Tuy nhiên, tầng định tuyến tin nhắn `_intercept_sub_agent` trước đây lại **hardcode** việc ghi đè mô hình sub-agent về `"gemini-flash-lite"`.

Điều này dẫn đến việc:
- Nếu người dùng cố tình cấu hình một mô hình con cụ thể (ví dụ: mô hình tùy chỉnh `haku` hoặc `gemini-flash` thường) làm sub-agent, tầng định tuyến vẫn ghi đè cưỡng bức về `gemini-flash-lite`.
- Sự không đồng nhất giữa model dự kiến tính quota và model thực tế định tuyến gây ra xung đột cú pháp hoặc sai lệch hạn ngạch.

### Giải pháp khắc phục:
Hàm `_intercept_sub_agent` đã được cập nhật để nhận tham số `account` và tích hợp helper `_get_sub_agent_override_model(account)`. Luồng giải quyết mô hình sub-agent hiện tại diễn ra đồng bộ theo thứ tự ưu tiên:
1. Đọc trường `subagent_model` / `agent_model` / `sub_agent_model` từ thông tin tài khoản cấu hình trong database.
2. Đọc biến môi trường `OPENCODE_SUB_AGENT_MODEL` hoặc `SUB_AGENT_MODEL` từ `.env`.
3. Fallback mặc định về `"gemini-flash-lite"` nếu không tìm thấy cấu hình riêng.

Nhờ đó, Claude Code có thể gọi các sub-agent chạy trên bất kỳ model nào được cấu hình mà không sợ lệch hạn ngạch hoặc lỗi định dạng.

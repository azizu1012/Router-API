# Đánh Giá Thuật Toán — Toàn Hệ Thống

## Thang Điểm
- **1-4**: Yếu — cần refactor gấp
- **5-7**: Trung Bình — có thể cải thiện
- **8-9**: Tốt — robust, phù hợp
- **10**: Xuất Sắc — tối ưu lý thuyết

---

## 🟢 Pipeline Tìm Kiếm

### 1. Phân Loại Chủ Đề — `_classify_topic`
**Điểm: 7/10** | O(k×t)

**Cơ chế:** Đếm số keyword match trên 20 chủ đề, chọn chủ đề có điểm cao nhất.

**Ưu điểm:** Đơn giản, nhanh, dễ bảo trì. Keyword phủ 21 chủ đề, có cả tiếng Việt lẫn tiếng Anh.

**Nhược điểm:** Không dùng TF-IDF hoặc embedding — dễ false positive (ví dụ "current" match cả gaming lẫn finance). Không xử lý được ambiguous query. Bộ lọc generic_tokens còn thô sơ.

**Cải thiện:** Thêm weight theo độ hiếm của keyword. Dùng word-level scoring thay vì boolean.

---

### 2. Tính Điểm Phủ Truy Vấn — `_query_coverage_score`
**Điểm: 7/10** | O(q + n-grams)

**Cơ chế:** Tỉ lệ token (5 mức 0.2→1.45) + so khớp n-gram phrase (n=2..4, trọng số 0.45→1.25) + thưởng kết hợp.

**Ưu điểm:** Phát hiện overlap cả ở mức token lẫn phrase. Thưởng kết hợp khi cả token và phrase đều match. Ngưỡng hợp lý.

**Nhược điểm:** O(n²) với n-gram trên text lớn. Ngưỡng cứng (0.9, 0.75, v.v.) — không adaptive. So khớp phrase với padded text dễ sai với punctuation.

---

### 3. Tính Điểm Uy Tín Động — `_dynamic_reputation_score`
**Điểm: 8/10** | O(q + t + e)

**Cơ chế:** Điểm composite từ độ phủ truy vấn (title+snippet + evidence ×0.75) + overlap trực tiếp + thưởng độ dài snippet/evidence + thưởng domain .gov/.edu + khớp title.

**Ưu điểm:** Đa yếu tố, evidence scaling 0.75 (tránh double-count). Thưởng độ dài chi tiết. Thưởng domain authority đơn giản nhưng hiệu quả.

**Nhược điểm:** Evidence coverage luôn nhân 0.75 — nên là dynamic weight. Phần thưởng domain chỉ có 5 pattern — thiếu .org, .io.

---

### 4. Xếp Hạng Lại Nhẹ — `_lightweight_rerank`
**Điểm: 7/10** | O(n² + n log n)

**Cơ chế:** Ma trận consensus (overlap token giữa các cặp record) + thưởng freshness (năm/marker) + phạt thời gian + phạt trùng domain.

**Ưu điểm:** Consensus giúp giảm outlier. Freshness boost phân biệt time-sensitive vs không. Phạt thời gian dùng chung logic với `_calculate_time_decay_penalty`.

**Nhược điểm:** O(n²) consensus với n≤20 (~400 operations) — chấp nhận được nhưng hơi phí. Consensus boost min(1.5, overlap×0.1) — bão hòa quá nhanh. Freshness boost chỉ check chuỗi năm, không parse ngày thực.

---

### 5. Đa Dạng Hóa Domain — `_domain_diversify_records`
**Điểm: 8/10** | O(n)

**Cơ chế:** 2 lượt — lượt đầu chọn unique org domain, lượt sau lấp đầy chỗ trống.

**Ưu điểm:** Đơn giản, O(n), hành vi dễ đoán. Dùng `_organization_domain` thay raw domain — xử lý đúng com.vn, gov.vn.

**Nhược điểm:** Lượt 2 có thể chọn record chất lượng thấp.

---

### 6. Bộ Nhớ Đệm Pipeline — cache/cooldown/inflight
**Điểm: 8/10** | O(1) cho thao tác

**Cơ chế:** Cache 2 tầng TTL (general 6h, time-sensitive 30m), khử trùng lặp inflight task, cooldown truy vấn lỗi.

**Ưu điểm:** Inflight dedup tránh search trùng cho concurrent request. Cooldown tránh retry storm. Semantic cache giảm cache miss.

**Nhược điểm:** Cache eviction LRU theo timestamp — dùng `min()` O(n) trên toàn dict (n=1000). Cooldown check nằm trong lock.

---

### 7. Trích Xuất Ngày Tháng — `_extract_date`
**Điểm: 6/10** | O(n) regex

**Cơ chế:** 15 compiled regex patterns — phút/giờ/ngày/tuần/tháng/năm, ngày tuyệt đối (YYYY-MM-DD, DD-MM-YYYY, Tháng Ngày, Ngày Tháng), marker tương đối.

**Ưu điểm:** Phủ nhiều định dạng (cả tiếng Việt). Compile sẵn pattern (class attribute).

**Nhược điểm:** Regex thuần — dễ vỡ với định dạng lạ. Phút/giờ đều trả về `now` (không parse khoảng cách). Map tháng hardcode. Year-only trả về June 1 — tùy tiện. Không xử lý timezone. Bắt exception quá rộng.

---

### 8. Chuẩn Hóa Văn Bản — `_canonicalize_search_query`
**Điểm: 8/10** | O(n + t)

**Cơ chế:** Loại bỏ dấu → alias cụm từ → lọc ký tự đặc biệt → alias token → stopwords → sắp xếp token unique.

**Ưu điểm:** Chuẩn hóa mạnh — truy vấn "thời tiết hôm nay" và "weather today" map cùng cache key. Sắp xếp token unique giúp cache key ổn định. Tiếng Việt alias đầy đủ.

**Nhược điểm:** Thay thế alias dùng regex `\b...\b` — có thể miss nếu punctuation gần. Giới hạn 32 token.

---

### 9. Trích Xuất Nội Dung HTML — `_extract_main_text`
**Điểm: 6/10** | O(h)

**Cơ chế:** Xóa script/style/noscript/form/svg → trích article/main → xóa nav/header/footer/aside → xóa thẻ HTML → xóa cụm từ tiếp thị.

**Ưu điểm:** Article/main extraction giúp giữ nội dung chính. Xóa cụm từ tiếp thị thông minh.

**Nhược điểm:** Dùng regex để parse HTML — dễ vỡ. Không xử lý thẻ lồng nhau tốt. Chỉ match 1 article/main. Không dùng SEO meta description, JSON-LD.

---

## 🟡 Định Tuyến & Quản Lý Key

### 10. Chọn Key — `reserve_key`
**Điểm: 8/10** | O(k×m)

**Cơ chế:** Pool membership → duyệt key → lọc ứng viên (bật, tier, pool, frozen, RPM/TPM headroom) → tính điểm ưu tiên → random top-10 → reserve DB.

**Ưu điểm:** Lọc đa giai đoạn giảm dần tập ứng viên. Random selection từ top-10 — cân bằng tải + tránh pattern lỗi xác định. Extreme retry mode (≥10) tự động thắt chặt threshold. Tự động giải phóng request treo 120s.

**Nhược điểm:** Tính điểm ưu tiên chạy lại mỗi lần reserve. Sort danh sách O(k log k).

---

### 11. Cooldown Thích Ứng — `_freeze_key` + `_adaptive_cooldown`
**Điểm: 9/10** | O(1)

**Cơ chế:** Loại lỗi → tính thời gian → jitter → đặt frozen_until (per-model + global). Lỗi vĩnh viễn → tự động gỡ key + thêm vào danh sách cấm.

**Ưu điểm:** Exponential backoff (3^cf, giới hạn 600s). RPD freeze biết Pacific midnight. Xử lý lỗi vĩnh viễn — tự động gỡ key khỏi .env. Freeze đa lớp (model + global).

**Nhược điểm:** Không có.

---

### 12. Circuit Breaker — `_key_is_circuit_open` + `record_429`
**Điểm: 9/10** | O(1)

**Cơ chế:** Ngưỡng = 10 → freeze toàn bộ key. Phát hiện cascade 429 ở global: 15 lần liên tiếp → cooldown ngẫu nhiên 10-20s.

**Ưu điểm:** Bảo vệ kép (per-key + global). Global cooldown reset counter — không infinite cascade. Jitter tránh thaw storm.

---

### 13. Xoay Vòng Pool Model — `ModelPool.swap/record_failure`
**Điểm: 8/10** | O(m)

**Cơ chế:** Member vòng tròn → đếm lỗi → ngưỡng swap (tùy loại lỗi) → reset khi tất cả exhausted → phục hồi khi thành công.

**Ưu điểm:** Ngưỡng swap thay đổi theo loại lỗi — hard failure swap ngay (1 lần), transient cần 2 lần. Reset tất cả exhausted cho phép full cycle lại. Reset toàn bộ khi thành công.

**Nhược điểm:** Chỉ số circular index còn đơn giản.

---

### 14. Tính Điểm Ưu Tiên — `get_key_priority`
**Điểm: 8/10** | O(p) cleanup

**Cơ chế:** Tỉ lệ RPD còn lại (remaining/target) → giảm penalty → trả về -1 nếu cạn. Dọn dẹp penalty hết hạn mỗi 60s.

**Ưu điểm:** Priority dựa trên RPD phản ánh dung lượng còn lại. Dọn dẹp định kỳ không chặn hot path.

---

### 15. Hệ Thống Penalty
**Điểm: 8/10** | O(1)

**Cơ chế:** PENALTY_MAP (lý do → thời gian + giảm điểm) → lưu SQLite → xóa lười khi hết hạn.

**Ưu điểm:** Penalty theo từng model. Bền vững qua restart (SQLite). Đọc không cần lock (kiểm tra hết hạn khi truy cập).

---

## 🔵 Giới Hạn Tốc Độ

### 16. GeminiRateLimiter (gộp theo model)
**Điểm: 9/10** | O(1) amortized

**Cơ chế:** Sliding window deques (60s RPM/TPM) + đếm RPD (reset theo Pacific). Mở rộng limit theo số lượng key.

**Ưu điểm:** Deque cleanup O(1). Sliding window chính xác — không fixed clock artifact. Singleton registry. Mở rộng limit động theo key count.

---

### 17. RPM/TPM Theo (Key, Model)
**Điểm: 8/10** | O(q) cleanup

**Cơ chế:** Dict[api_key::model_id, deque] → xóa entry >60s → kiểm tra ngưỡng.

**Ưu điểm:** Chi tiết đến từng cặp (key, model). Deque hiệu quả.

**Nhược điểm:** Bộ nhớ O(k×m×60s). Không có dọn dẹp định kỳ — chỉ cleanup khi truy cập.

---

### 18. Giới Hạn Tốc Độ Cấp Tài Khoản
**Điểm: 8/10** | O(1) amortized

**Cơ chế:** Deques theo (tài khoản, pool) + RPD + khóa theo tài khoản.

**Ưu điểm:** Khóa chi tiết — các tài khoản độc lập. Dọn dẹp tài khoản không hoạt động sau 24h. Phục hồi RPD từ DB. Ước lượng token theo trọng số (ASCII vs non-ASCII).

**Nhược điểm:** Phục hồi RPD quét toàn bộ log mỗi lần khởi động.

---

### 19. Giới Hạn Công Bằng (Fair-Share)
**Điểm: 9/10** | O(1)

**Cơ chế:** Phân phối theo trọng số: free base share → premium 1.5× → admin nhận phần còn lại → giới hạn bởi config tài khoản.

**Ưu điểm:** Công bằng — không 1 tài khoản chiếm pool. Premium 1.5× multiplier. Giới hạn bởi config. Toán chặt chẽ.

---

## 🟣 Tìm Kiếm & Dịch Vụ Ngoài

### 20. Tìm Kiếm Lai — `execute_hybrid_search`
**Điểm: 8/10** | O(p×m)

**Cơ chế:** Truy vấn song song → Gemini Grounding (lite→flash fallback) → DDG fallback.

**Ưu điểm:** Chuỗi suy giảm nhẹ nhàng. Thực thi song song. Trích xuất citations từ grounding_metadata. Khử trùng lặp cấp domain.

---

### 21. Trích Xuất Truy Vấn Tìm Kiếm — `extract_search_queries`
**Điểm: 7/10** | O(1) I/O

**Cơ chế:** Gemini JSON mode → phát hiện intent tìm kiếm → chuỗi fallback.

**Ưu điểm:** Chuỗi fallback model (flash→lite→25-lite). Giới hạn 800 ký tự.

**Nhược điểm:** Gemini trả về [] → không fallback search (đã fix). Prompt cố định — không adaptive.

---

### 22. Khám Phá Endpoint Tùy Chỉnh — `_try_fetch_models`
**Điểm: 7/10** | O(1) I/O

**Cơ chế:** 3 URL patterns → aiohttp timeout 5s → so khớp response.

**Ưu điểm:** Dò URL thông minh. Timeout 5s mỗi request.

---

## 🟠 Tài Khoản & Bộ Nhớ Đệm

### 23. Bộ Nhớ Đệm Tài Khoản — `AccountManager`
**Điểm: 9/10** | O(1) hot path

**Cơ chế:** Double-checked locking → TTL 10s → dict lookup O(1) → fallback DB với so sánh chống timing attack.

**Ưu điểm:** Double-checked locking giảm tranh chấp. TTL 10s cân bằng freshness vs hiệu năng. So sánh chống timing attack. Invalidate khi mutation.

---

## 🟤 Hạ Tầng

### 24. Ghi Log Bất Đồng Bộ
**Điểm: 8/10** | O(b) flush

**Cơ chế:** asyncio.Queue → flush hàng loạt mỗi 5s → executemany → giữ 30 ngày.

**Ưu điểm:** Không chặn luồng chính. Re-queue khi lỗi. Ghi SQLite theo batch. Tự động xóa dữ liệu cũ.

---

### 25. Tính Toán Chi Phí
**Điểm: 6/10** | O(m)

**Cơ chế:** Tính chi phí giả định so sánh với giá Claude Sonnet.

**Nhược điểm:** Giá hardcode. Giả định 80% cache read tùy tiện. Chỉ so sánh 1 model.

---

## 📊 Tổng Hợp

| # | Thành Phần | Điểm | Độ Phức Tạp | Ưu Tiên |
|---|-----------|------|-------------|---------|
| 1 | Phân Loại Chủ Đề | 7 | O(k×t) | Trung bình |
| 2 | Điểm Phủ Truy Vấn | 7 | O(q + n-grams) | Trung bình |
| 3 | Uy Tín Động | 8 | O(q+t+e) | Thấp |
| 4 | Xếp Hạng Lại | 7 | O(n² + n log n) | Trung bình |
| 5 | Đa Dạng Hóa Domain | 8 | O(n) | Thấp |
| 6 | Bộ Nhớ Đệm Pipeline | 8 | O(1) | Thấp |
| 7 | Trích Xuất Ngày Tháng | 6 | O(n) regex | **Cao** |
| 8 | Chuẩn Hóa Văn Bản | 8 | O(n+t) | Thấp |
| 9 | Trích Xuất HTML | 6 | O(h) | **Cao** |
| 10 | Chọn Key | 8 | O(k×m) | Thấp |
| 11 | Cooldown Thích Ứng | 9 | O(1) | Thấp |
| 12 | Circuit Breaker | 9 | O(1) | Thấp |
| 13 | Xoay Vòng Pool | 8 | O(m) | Thấp |
| 14 | Điểm Ưu Tiên | 8 | O(p) cleanup | Thấp |
| 15 | Hệ Thống Penalty | 8 | O(1) | Thấp |
| 16 | Rate Limiter Gộp | 9 | O(1) | Thấp |
| 17 | RPM/TPM Theo Key | 8 | O(q) | Thấp |
| 18 | Rate Limiter Tài Khoản | 8 | O(1) | Thấp |
| 19 | Giới Hạn Công Bằng | 9 | O(1) | Thấp |
| 20 | Tìm Kiếm Lai | 8 | O(p×m) | Thấp |
| 21 | Trích Xuất Truy Vấn | 7 | O(1) I/O | Trung bình |
| 22 | Khám Phá Endpoint | 7 | O(1) I/O | Trung bình |
| 23 | Cache Tài Khoản | 9 | O(1) | Thấp |
| 24 | Ghi Log Bất Đồng Bộ | 8 | O(b) | Thấp |
| 25 | Tính Chi Phí | 6 | O(m) | **Cao** |

**Điểm trung bình:** 7.68/10

**Cần cải thiện (≤6):**
1. **Trích Xuất Ngày Tháng (6)** — chuyển sang thư viện `dateparser` thay regex thuần
2. **Trích Xuất HTML (6)** — dùng BeautifulSoup/lxml thay regex
3. **Tính Chi Phí (6)** — dynamic pricing từ config, không hardcode

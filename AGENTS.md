# Project Instructions

## 1. TODO bắt buộc cho task phức tạp

- Task từ 3 bước trở lên hoặc chạm nhiều file → tạo TODO list ngay trước khi làm.
- Ghi cụ thể từng bước, đúng thứ tự.
- Làm xong bước nào → tick ngay (dùng todowrite).
- Không tự ý làm bước sau khi bước trước chưa xong.
- Chỉ skip TODO nếu task đơn giản (1-2 bước).

## 2. Đọc file theo lô, song song

- Lập danh sách file trước khi gọi tool.
- Ưu tiên đọc nhiều file một lượt (gọi song song).
- Dùng grep/glob trước khi đọc sâu nếu chưa chắc vị trí.

## 3. Sub-agent / explore

- Chỉ dùng khi cần scan rộng hoặc tìm kiếm khó.
- Mỗi sub-agent chỉ làm 1 mục tiêu, chỉ RESEARCH, không ghi file.
- `description`: 3-5 từ, đúng mục tiêu.

## 4. Code rules

- Không commit `.env`, `usage.db`, `logs/`
- DB dùng SQLite qua `src/backend/_db.py`
- Dùng logger có sẵn: `from src.core.config import logger` hoặc `from src.core.logger import logger_system/logger_proxy/logger_keys/logger_api/logger_keepalive`

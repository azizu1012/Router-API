# Bugfix: Import Error - get_connection

## Issue
Server failed to start với lỗi:
```
ImportError: cannot import name 'get_connection' from 'src.backend._db'
```

## Root Cause
File `src/backend/model_aliases.py` import sai:
```python
from src.backend._db import get_connection  # ❌ Không tồn tại
```

`_db.py` chỉ export `conn()` function, không có `get_connection()`.

## Fix
Đổi import theo pattern của các file khác trong `src/backend/`:

```python
from src.backend._db import _LOCK, conn as _conn  # ✅ Đúng
```

Và replace tất cả `get_connection()` → `_conn()` trong file (7 chỗ).

## Files Changed
- `src/backend/model_aliases.py` (8 lines)

## Verification
```bash
python run.py
```

Server sẽ start bình thường.

## Lesson Learned
Khi tạo file mới trong `src/backend/`, luôn check pattern import của các file existing:
- ✅ `from src.backend._db import _LOCK, conn as _conn`
- ❌ `from src.backend._db import get_connection`

---

**Status**: ✅ FIXED
**Time**: 2 minutes
**Impact**: Server start blocking bug

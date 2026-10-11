# Custom Endpoint Cleanup Plan

## Context
Custom endpoint đã được refactor thành pure passthrough architecture:
- Không còn là pool member
- Không qua pool acquire/release loop
- Routing được handle bởi proxy handlers trước khi gọi pool_manager

## Files cần cleanup

### 1. `src/core/pool_manager.py` (HIGH PRIORITY)
**Current state:** 581 dòng, custom endpoint logic còn embedded sâu trong pool loop

**Locations to remove:**
- Line 16: `from src.core.providers import _custom_endpoint_manager as endpoint_manager`
- Line 17: `from src.core.providers.custom_endpoint_client import check_custom_pool_rate`
- Line 201-203: `is_custom` check và `endpoint_manager.mark_endpoint_success()`
- Line 220-225: Custom endpoint failure handling trong pool nonstream loop
- Line 367-383: Custom endpoint rate check trong pool stream loop  
- Line 404-407: Custom endpoint api_base injection
- Line 420-421: Custom endpoint success marking trong stream
- Line 435-440: Custom endpoint failure handling trong stream loop
- Line 488: `is_custom = False` initialization
- Line 497: `is_custom` assignment
- Line 506-508: Custom endpoint rate check trong standalone stream
- Line 537-538: Custom endpoint success marking trong standalone stream
- Line 553-555: Custom endpoint failure marking trong standalone stream
- Line 625: `is_custom` assignment trong `_resolve_and_call`
- Line 628: Skip thinking computation for custom endpoint
- Line 637-639: Custom endpoint rate check trong `_resolve_and_call`
- Line 660-663: Custom endpoint api_base injection trong `_resolve_and_call`

**Strategy:** 
- KHÔNG XÓA NGAY - vì logic còn nhiều nhánh
- Đợi verify passthrough hoạt động ổn
- Sau đó tạo một version mới của pool_manager chỉ handle Gemini

### 2. `src/logical_HQ_translator/model_resolver.py` (MEDIUM PRIORITY)
**Status:** Đã update - custom endpoint được detect và tagged nhưng vẫn return về pool_manager
**Action needed:** 
- Verify rằng proxy handlers đã intercept custom endpoint trước khi gọi pool_manager
- Có thể loại bỏ hoàn toàn custom endpoint logic khỏi `_resolve_model()`

### 3. `src/core/router/pool.py` (LOW PRIORITY)
**Check:** Xem có logic nào specific cho custom endpoint pool members không
**Action:** Review và cleanup nếu cần

### 4. `src/backend/pool_assignments.py` (HIGH PRIORITY - Task #8)
**Action:** 
- Deprecate hoặc loại bỏ table `pool_assignments` 
- Custom endpoint giờ được assign qua `custom_endpoints.account_id` hoặc `custom_endpoints.account_key_id`

## Migration Path

### Phase 1: Verify Passthrough (CURRENT)
- ✅ Tạo passthrough module
- ✅ Update proxy handlers để route qua passthrough
- ✅ Add key-level assignment API
- ⏳ Test manually để verify hoạt động

### Phase 2: Database Cleanup
- Migrate data từ `pool_assignments` sang `custom_endpoints.account_key_id`
- Drop hoặc deprecate `pool_assignments` table
- Update admin UI để dùng key-level assignment

### Phase 3: Code Cleanup
- Loại bỏ custom endpoint logic khỏi `pool_manager.py`
- Loại bỏ custom endpoint logic khỏi `model_resolver.py`
- Remove unused imports và functions

### Phase 4: Testing
- Integration tests cho passthrough architecture
- Verify fallback behavior (nếu custom endpoint fail)
- Load testing

## Rollback Plan
Nếu passthrough gây issues:
1. Revert proxy handlers (remove passthrough check)
2. Custom endpoint sẽ fallback về pool logic cũ
3. Không cần revert database changes (backward compatible)

## Notes
- Pool_manager cleanup là **safe to defer** vì proxy handlers đã intercept trước
- Custom endpoint requests sẽ không bao giờ reach pool_manager logic nữa
- Cleanup là optimization, không phải bugfix

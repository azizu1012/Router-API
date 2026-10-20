import time
import uuid
import pytest
from unittest.mock import patch

from src.core.router import router
from src.core.runtime.request_context import set_request_id, get_request_id
from src.backend.keys.status import (
    record_key_transition,
    query_key_transitions_db,
    query_endpoint_transitions_db,
    flush_pending_writes,
)
from src.core.providers import _custom_endpoint_manager as endpoint_manager


def test_request_context_propagation():
    """Verify request ID is properly set and retrieved from ContextVar."""
    custom_id = f"req-{uuid.uuid4().hex[:12]}"
    set_request_id(custom_id)
    assert get_request_id() == custom_id


def test_reservation_lifecycle_and_idempotent_release(temp_db):
    """Verify reservation lifecycle prevents double release and tracks active requests accurately."""
    test_key = "test_res_key_12345678"
    router._key_status[test_key] = {
        "enabled": 1,
        "active_requests": 0,
        "usage": 0,
        "frozen_until": 0.0,
        "consecutive_failures": 0,
        "per_model": {},
    }

    res_id = uuid.uuid4().hex
    reservation = {
        "key": test_key,
        "reservation_id": res_id,
        "reserved_at": time.time(),
        "model_alias": "gemini-flash-36",
        "model_id": "gemini-3.6-flash",
    }
    router.register_reservation(reservation)

    # Simulate acquisition
    with router._key_lock:
        router._key_status[test_key]["active_requests"] += 1

    assert router._key_status[test_key]["active_requests"] == 1

    # First release -> succeeds
    released = router.release_key(test_key, reservation_id=res_id)
    assert released is True
    assert router._key_status[test_key]["active_requests"] == 0

    # Second release with same reservation_id -> rejected, active_requests stays 0 (no underflow!)
    released_second = router.release_key(test_key, reservation_id=res_id)
    assert released_second is False
    assert router._key_status[test_key]["active_requests"] == 0


def test_key_transition_recording_and_query(temp_db):
    """Verify key transitions record metadata with suffix-only and are queryable."""
    req_id = f"req-test-{uuid.uuid4().hex[:8]}"
    trans_id = uuid.uuid4().hex
    full_key = "AIzaSySecretApiKeySuffix88"

    record_key_transition(
        transition_id=trans_id,
        request_id=req_id,
        key=full_key,
        model_id="gemini-3.6-flash",
        reason="rate_limit",
        metadata={"path": "test_path", "attempt": 1},
    )
    flush_pending_writes()

    rows = query_key_transitions_db(request_id=req_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["transition_id"] == trans_id
    assert row["key_suffix"] == full_key[-8:]
    assert "Secret" not in row["key_suffix"]  # Secret is not leaked!
    assert row["reason"] == "rate_limit"
    assert row["metadata"].get("path") == "test_path"


def test_endpoint_transition_recording_and_query(temp_db):
    """Verify custom endpoint transitions are recorded to SQLite and queryable."""
    ep_name = f"test-ep-{uuid.uuid4().hex[:6]}"
    req_id = f"req-ep-{uuid.uuid4().hex[:8]}"

    endpoint_manager.mark_endpoint_failure(
        ep_name,
        metadata={"request_id": req_id, "reason": "502 Bad Gateway", "attempt": 0},
    )

    rows = query_endpoint_transitions_db(request_id=req_id, endpoint=ep_name)
    assert len(rows) >= 1
    assert rows[0]["endpoint"] == ep_name
    assert rows[0]["state"] == "degraded"
    assert rows[0]["failures"] == 1

    # Success marks healthy
    endpoint_manager.mark_endpoint_success(ep_name, metadata={"request_id": req_id})
    rows_after = query_endpoint_transitions_db(endpoint=ep_name)
    assert len(rows_after) >= 2
    assert rows_after[0]["state"] == "healthy"


def test_key_local_error_does_not_degrade_model_health(temp_db):
    """Key-local errors (invalid_key, permission_denied) must not penalize model health."""
    test_alias = "gemini-flash-36"
    concrete_mid = "gemini-3.6-flash"
    router._model_health[test_alias] = {"score": 100.0, "updated": time.time()}

    test_key = "test_key_local_error_key"
    router._key_status[test_key] = {
        "enabled": 1,
        "active_requests": 0,
        "frozen_until": 0.0,
        "consecutive_failures": 0,
        "per_model": {},
    }

    # Freezing with invalid_key must not lower model health score
    router.freeze_key(test_key, duration=60, model_id=concrete_mid, reason="invalid_key")
    assert router._model_health[test_alias]["score"] == 100.0

    # Freezing with permission_denied must not lower model health score
    router.freeze_key(test_key, duration=60, model_id=concrete_mid, reason="permission_denied")
    assert router._model_health[test_alias]["score"] == 100.0

    # But provider/model-level error (e.g. unavailable, internal error) should penalize model health
    router.freeze_key(test_key, duration=60, model_id=concrete_mid, reason="unavailable")
    assert router._model_health[test_alias]["score"] < 100.0


def test_freeze_all_keys_requires_actor_and_reason(temp_db):
    """Emergency freeze requires actor and reason to prevent accidental global freezes."""
    test_key = "test_emergency_freeze_key"
    router._key_status[test_key] = {
        "enabled": 1,
        "frozen_until": 0.0,
        "active_requests": 0,
        "consecutive_failures": 0,
        "per_model": {},
    }

    # Calling without actor or reason -> refused
    router.freeze_all_keys(60)
    assert router._key_status[test_key]["frozen_until"] == 0.0

    # Calling with actor and reason -> accepted
    router.freeze_all_keys(60, actor="admin_alice", reason="data_center_outage")
    assert router._key_status[test_key]["frozen_until"] > time.time()

"""Tests for session authorization security fixes:
- Session tokens are invalidated when accounts are disabled (self_service & dashboard).
- Stale session tokens cannot bind to newly created accounts sharing the same name.
"""

import os
import tempfile
from pathlib import Path
import pytest
from fastapi import Request, HTTPException


@pytest.fixture(autouse=True)
def isolated_db():
    from src.backend import _db as db_mod
    from src.backend import schema as schema_mod

    fd, name = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    tmp = Path(name)

    orig_db = db_mod._DB
    db_mod._DB = str(tmp)
    db_mod._bootstrapped = False
    try:
        schema_mod.init_config_tables()
        yield
    finally:
        db_mod._DB = orig_db
        db_mod._bootstrapped = False
        for suffix in ("", "-wal", "-shm"):
            Path(str(tmp) + suffix).unlink(missing_ok=True)


def test_self_service_rejects_disabled_account():
    from src.backend.accounts.manager import create_account_db, update_account_db
    from src.server.openai_server.routes.auth_session import _make_session_token
    from src.server.openai_server.routes.self_service.auth import get_account

    acc = create_account_db(name="disabled_user", tier="free")
    token = _make_session_token(acc)

    # Valid when enabled
    req = Request({"type": "http", "headers": [(b"x-dashboard-token", token.encode())]})
    account = get_account(req)
    assert account["account_id"] == acc["account_id"]

    # Invalidate when disabled in DB
    update_account_db("disabled_user", enabled=False)
    with pytest.raises(HTTPException) as exc:
        get_account(req)
    assert exc.value.status_code == 401


def test_self_service_rejects_stale_token_with_reused_name():
    from src.backend.accounts.manager import create_account_db, delete_account_db
    from src.server.openai_server.routes.auth_session import _make_session_token
    from src.server.openai_server.routes.self_service.auth import get_account

    # Create account A and generate session token
    acc_a = create_account_db(name="reused_name", tier="free")
    token_a = _make_session_token(acc_a)

    # Delete account A
    delete_account_db("reused_name")

    # Create account B with the same name (gets new account_id)
    acc_b = create_account_db(name="reused_name", tier="admin")
    assert acc_b["account_id"] != acc_a["account_id"]

    # Old token A must not access account B
    req = Request({"type": "http", "headers": [(b"x-dashboard-token", token_a.encode())]})
    with pytest.raises(HTTPException) as exc:
        get_account(req)
    assert exc.value.status_code == 401


def test_require_dashboard_rejects_stale_token_with_reused_name():
    from src.backend.accounts.manager import create_account_db, delete_account_db
    from src.server.openai_server.routes.auth_session import _make_session_token, _require_dashboard

    # Create account A and token A
    acc_a = create_account_db(name="dash_reused", tier="free")
    token_a = _make_session_token(acc_a)

    # Delete account A and recreate account B
    delete_account_db("dash_reused")
    acc_b = create_account_db(name="dash_reused", tier="admin")

    req = Request({"type": "http", "headers": [(b"x-dashboard-token", token_a.encode())]})
    with pytest.raises(HTTPException) as exc:
        _require_dashboard(req)
    assert exc.value.status_code == 401


def test_require_dashboard_rejects_disabled_account():
    from src.backend.accounts.manager import create_account_db, update_account_db
    from src.server.openai_server.routes.auth_session import _make_session_token, _require_dashboard

    acc = create_account_db(name="dash_disabled", tier="free")
    token = _make_session_token(acc)

    update_account_db("dash_disabled", enabled=False)
    req = Request({"type": "http", "headers": [(b"x-dashboard-token", token.encode())]})
    with pytest.raises(HTTPException) as exc:
        _require_dashboard(req)
    assert exc.value.status_code == 401

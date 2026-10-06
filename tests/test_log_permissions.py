"""Tests for log streaming, channel normalization, and tier-based access control.

Covers:
1. normalize_channel normalization across formats (e.g. 'log:proxy', 'proxy.log', 'proxy').
2. LogWatcher tail preloading from disk and get_history buffer lookup.
3. Access control on /dashboard/logs/history:
   - Unauthenticated -> 401
   - Free/Premium user querying 'proxy' or 'api' -> 200
   - Free/Premium user querying 'keys', 'system', or 'web' -> 403
   - Admin user querying any channel -> 200
"""

import os
import sys
import tempfile
from collections import deque
from pathlib import Path
import pytest

sys.path.insert(0, ".")

from src.server.log_watcher import normalize_channel, LogWatcher


def test_normalize_channel_forms():
    assert normalize_channel("proxy") == "proxy"
    assert normalize_channel("log:proxy") == "proxy"
    assert normalize_channel("proxy.log") == "proxy"
    assert normalize_channel("log:keys:endpoint") == "keys"
    assert normalize_channel("system.log") == "system"
    assert normalize_channel("api") == "api"
    assert normalize_channel("log:web") == "web"
    assert normalize_channel("unknown_chan") == "unknown_chan"


def test_log_watcher_tail_and_history(tmp_path):
    log_file = tmp_path / "proxy.log"
    log_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n", encoding="utf-8")

    watcher = LogWatcher(log_dir=tmp_path)

    # Calling get_history with lines=3 should read from disk if buffer empty
    history = watcher.get_history("proxy", lines=3)
    assert len(history) == 3
    assert history == ["line 3", "line 4", "line 5"]

    # Pre-populating buffer with enough lines (>= requested lines)
    watcher._buffers["log:proxy"] = deque(["line 6 (buffered)", "line 7 (buffered)"])
    history_buffered = watcher.get_history("log:proxy", lines=2)
    assert history_buffered == ["line 6 (buffered)", "line 7 (buffered)"]


@pytest.fixture()
def client():
    """Real app with throwaway database."""
    from fastapi.testclient import TestClient
    from src.backend import _db as db_mod
    from src.backend import schema as schema_mod
    from src.server.openai_server.security import clear_dashboard_rate

    # Clear rate limit accumulator for the test client IP
    clear_dashboard_rate("testclient")

    fd, name = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    tmp = Path(name)

    original = db_mod._DB
    db_mod._DB = str(tmp)
    db_mod._bootstrapped = False
    try:
        schema_mod.init_config_tables()
        from src.server.openai_server.routes.app_init import app
        with TestClient(app) as c:
            yield c
    finally:
        clear_dashboard_rate("testclient")
        db_mod._DB = original
        db_mod._bootstrapped = False
        for suffix in ("", "-wal", "-shm"):
            Path(str(tmp) + suffix).unlink(missing_ok=True)


def _create_user(client, name="user1", tier="free"):
    from src.core.accounts import account_manager
    from src.backend.account_keys import set_password_db
    from src.server.openai_server.routes.auth_session import _make_session_token

    acc = account_manager.create_account(name=name, tier=tier)
    set_password_db(acc["account_id"], "pass1234")
    return acc, _make_session_token(acc)


def test_log_history_unauthenticated(client):
    res = client.get("/dashboard/logs/history?channel=proxy")
    assert res.status_code == 401


def test_log_history_regular_user_permissions(client):
    _, user_token = _create_user(client, name="regular_joe", tier="free")
    headers = {"X-Dashboard-Token": user_token}

    # Allowed: proxy
    res_proxy = client.get("/dashboard/logs/history?channel=proxy", headers=headers)
    assert res_proxy.status_code == 200
    assert "history" in res_proxy.json()
    assert "lines" in res_proxy.json()

    # Allowed: api
    res_api = client.get("/dashboard/logs/history?channel=api", headers=headers)
    assert res_api.status_code == 200

    # Forbidden: keys
    res_keys = client.get("/dashboard/logs/history?channel=keys", headers=headers)
    assert res_keys.status_code == 403
    assert "chỉ dành cho Quản trị viên" in res_keys.json().get("detail", "")

    # Forbidden: system
    res_sys = client.get("/dashboard/logs/history?channel=system", headers=headers)
    assert res_sys.status_code == 403

    # Forbidden: web
    res_web = client.get("/dashboard/logs/history?channel=web", headers=headers)
    assert res_web.status_code == 403


def test_log_history_admin_permissions(client):
    _, admin_token = _create_user(client, name="super_admin", tier="admin")
    headers = {"X-Dashboard-Token": admin_token}

    for ch in ("proxy", "api", "keys", "system", "web"):
        res = client.get(f"/dashboard/logs/history?channel={ch}", headers=headers)
        assert res.status_code == 200, f"Expected 200 for admin on channel {ch}, got {res.status_code}"
        data = res.json()
        assert "history" in data
        assert isinstance(data["history"], list)

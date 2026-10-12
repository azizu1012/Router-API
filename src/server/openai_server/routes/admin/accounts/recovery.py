"""Admin credential recovery and password-policy routes."""
from fastapi import Request, HTTPException

from ...app_init import app
from ...auth_session import _require_admin
from ...credential_reveal import reveals_credential

from src.backend.keys.accounts import get_credential_db, set_password_flag


@app.get("/dashboard/admin/accounts/recovery")
async def admin_account_recovery(request: Request, name: str):
    """Read a user's password back, for the operator.

    Separate from the account listing on purpose: a password must never ride
    along with a table of accounts, because that table is what an operator
    glances at and what a screenshot captures. This has to be asked for by name.

    Admin-only, and it reports honestly when it cannot answer. A NULL means one
    of three things — the account predates the feature, no key is configured, or
    the row does not decrypt — and none of them are worth guessing at.
    """
    _require_admin(request)
    clean = str(name or "").strip()
    if not clean:
        raise HTTPException(status_code=400, detail="name is required")

    import asyncio
    from src.backend.accounts.manager import find_account_by_name
    from src.backend.keys.accounts import get_recoverable_password_db
    from src.backend.accounts.recovery import is_enabled

    acct = await asyncio.to_thread(find_account_by_name, clean)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")

    if not is_enabled():
        return {
            "name": clean,
            "password": None,
            "available": False,
            "reason": "ROUTER_API_PASSWORD_KEY chưa được đặt — tính năng xem mật khẩu đang tắt",
        }

    plain = await asyncio.to_thread(get_recoverable_password_db, acct["account_id"])
    if plain is None:
        return {
            "name": clean,
            "password": None,
            "available": False,
            "reason": "Tài khoản này chưa có bản mật khẩu có thể đọc lại. "
                      "Đặt lại mật khẩu để tạo bản mới.",
        }
    return {"name": clean, "password": plain, "available": True}


@app.get("/dashboard/admin/accounts/master-key")
@reveals_credential
async def admin_account_master_key(request: Request, name: str):
    """Read one account's master key, for the operator.

    Separate from the account listing on purpose. The listing is polled every few
    seconds and held in browser state, so a credential in it means one screenshot
    of the accounts tab is a list of quota-exempt keys — which is exactly how
    ``/dashboard/accounts`` shipped raw ``auth_key`` for every account.

    Asking for it by name is the difference between an operator reading the key
    they were looking for and a whole table leaking to whoever sees the screen.
    Admin-only, and it says so plainly when the account has no key.
    """
    _require_admin(request)
    clean = str(name or "").strip()
    if not clean:
        raise HTTPException(status_code=400, detail="name is required")

    import asyncio
    from src.backend.accounts.manager import find_account_by_name

    acct = await asyncio.to_thread(find_account_by_name, clean)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")

    key = str(acct.get("auth_key") or "")
    if not key:
        return {"name": clean, "auth_key": None, "available": False,
                "reason": "Tài khoản này chưa có master key."}
    return {"name": clean, "auth_key": key, "available": True}


@app.post("/dashboard/admin/accounts/require-password-change")
async def admin_require_password_change(request: Request):
    """Mark an account as owing a password change, or clear the mark.

    This writes a flag; it does not touch the password. The distinction matters
    because the two things get confused: an admin asking for a password reset
    wants a new secret, while this wants the account told to choose one itself on
    next login. The dashboard reads the flag as a persistent banner, not as a lock.

    Refuses an account with no credential row rather than creating one. Writing a
    credential here would fabricate a password hash for an account that never had
    a web login, and the value chosen would have to be one nobody knows.
    """
    _require_admin(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        required = bool(body.get("required"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    import asyncio
    from src.backend.accounts.manager import find_account_by_name

    acct = await asyncio.to_thread(find_account_by_name, name)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    if not await asyncio.to_thread(get_credential_db, acct["account_id"]):
        raise HTTPException(
            status_code=400,
            detail="Tài khoản này chưa có mật khẩu web. Cấp mật khẩu trước rồi đánh dấu bắt đổi.",
        )

    try:
        await asyncio.to_thread(
            set_password_flag, acct["account_id"], 1 if required else 0
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "name": name, "must_change_password": required}



"""Admin key-management endpoints."""
import json
from fastapi import Request, HTTPException

from ..app_init import app
from ..auth_session import _require_admin
from .helpers import append_key_to_env, remove_key_from_env, normalize_key_name


@app.post("/dashboard/admin/keys/add")
async def admin_add_key(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        api_key = str(body.get("api_key", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key is required")

    append_key_to_env(api_key)

    from src.core.config_n_logg import reload_config
    new_keys = reload_config()
    from src.backend.key_status import register_keys_in_db
    register_keys_in_db(new_keys)
    from src.core.router import router
    router.refresh_keys()

    return {"status": "success"}


@app.post("/dashboard/admin/keys/delete")
async def admin_delete_key(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        target_key = str(body.get("key", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not target_key:
        raise HTTPException(status_code=400, detail="key is required")

    target_key = normalize_key_name(target_key)
    success = remove_key_from_env(target_key)

    if not success:
        raise HTTPException(status_code=404, detail="Key not found or could not delete")

    from src.core.config_n_logg import reload_config
    new_keys = reload_config()
    from src.backend.key_status import register_keys_in_db
    register_keys_in_db(new_keys)
    from src.core.router import router
    router.refresh_keys()

    return {"status": "success"}


@app.post("/dashboard/admin/keys/pool")
async def admin_pool_key(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        target_key = str(body.get("key", "")).strip()
        pool_name = str(body.get("pool", "all")).strip().lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not target_key:
        raise HTTPException(status_code=400, detail="key is required")

    target_key = normalize_key_name(target_key)

    from src.backend._db import _LOCK, conn as _conn
    with _LOCK:
        c = _conn()
        try:
            row = c.execute("SELECT data FROM key_status WHERE key = ?", (target_key,)).fetchone()
            current_data = {}
            if row and row["data"]:
                try:
                    current_data = json.loads(row["data"])
                except Exception:
                    pass
            if pool_name == "all":
                current_data.pop("allowed_pools", None)
            else:
                current_data["allowed_pools"] = [pool_name]
            c.execute("UPDATE key_status SET data = ? WHERE key = ?", (json.dumps(current_data), target_key))
            c.commit()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Database save failed: {e}")
        finally:
            c.close()

    from src.core.router import router
    router.refresh_keys()

    return {"status": "success"}

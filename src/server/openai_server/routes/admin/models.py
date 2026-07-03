from fastapi import Request, HTTPException

from ..app_init import app
from ..auth_session import _require_admin


@app.get("/dashboard/admin/models")
async def admin_get_models(request: Request):
    _require_admin(request)
    from src.core.api_config import AVAILABLE_MODELS, MODEL_POOLS
    from src.backend.model_config import list_model_configs
    from src.backend.endpoints import list_endpoints_db as list_endpoints
    from src.backend.accounts import list_accounts_db

    db_configs = {r["alias"]: r for r in list_model_configs()}
    all_accounts = {a["account_id"]: a.get("name", "") for a in list_accounts_db()}

    # Pool membership lookup from MODEL_POOLS: alias → pool_name
    alias_to_pool = {}
    for pn, pc in MODEL_POOLS.items():
        for m in pc["members"]:
            alias_to_pool[m] = pn

    result = []
    seen_aliases = set()

    # 1. Gemini models from AVAILABLE_MODELS
    for alias, cfg in AVAILABLE_MODELS.items():
        dbc = db_configs.get(alias)
        entry = {
            "alias": alias,
            "display": cfg.get("display", ""),
            "model_id": cfg.get("model_id", ""),
            "rpm": cfg.get("rpm", 10),
            "tpm": cfg.get("tpm", 1000000),
            "rpd": cfg.get("rpd", 1000),
            "hidden": cfg.get("hidden", False),
            "priority": cfg.get("priority", 1),
            "context_length": cfg.get("context_length", 220000),
            "pool_name": cfg.get("pool_name", ""),
            "in_db": dbc is not None,
            "source": "gemini",
            "endpoint": "",
        }
        if dbc:
            entry["db_rpm"] = dbc.get("rpm")
            entry["db_tpm"] = dbc.get("tpm")
            entry["db_rpd"] = dbc.get("rpd")
        # Inherit pool membership from MODEL_POOLS if pool_name not set
        if not entry["pool_name"] and entry["alias"] in alias_to_pool:
            entry["pool_name"] = alias_to_pool[entry["alias"]]
        result.append(entry)
        seen_aliases.add(alias)

    # 2. Custom endpoint models from enabled_models + pool_assignments
    for ep in list_endpoints():
        if not ep.get("enabled"):
            continue
        pool_assignments = ep.get("pool_assignments", {})
        aid = ep.get("account_id") or ""
        aname = all_accounts.get(aid, "") or aid
        for mid in (ep.get("enabled_models") or []):
            # Skip if this model_id is already known as a Gemini alias
            if mid in seen_aliases:
                continue
            # Find which pool this model is assigned to
            assigned_pool = ""
            for pn, pm in pool_assignments.items():
                if pm == mid:
                    assigned_pool = pn
                    break
            dbc = db_configs.get(mid)
            if dbc and dbc.get("account_id") != aid:
                dbc = None
            entry = {
                "alias": mid,
                "display": f"{ep.get('name','?')} / {mid}",
                "model_id": mid,
                "rpm": 0,
                "tpm": 0,
                "rpd": 0,
                "hidden": False,
                "priority": 0,
                "context_length": 0,
                "pool_name": assigned_pool,
                "in_db": dbc is not None,
                "source": "custom_endpoint",
                "endpoint": ep.get("name", ""),
                "account_id": aid,
                "account_name": aname,
            }
            if dbc:
                entry["db_rpm"] = dbc.get("rpm")
                entry["db_tpm"] = dbc.get("tpm")
                entry["db_rpd"] = dbc.get("rpd")
            result.append(entry)

    pools = [
        {
            "name": pn,
            "members": pc["members"],
        }
        for pn, pc in MODEL_POOLS.items()
    ]

    # Include enabled endpoints so the ModelsTab can pick from custom endpoint models
    safe_endpoints = []
    for ep in list_endpoints():
        if not ep.get("enabled"):
            continue
        safe_ep = {k: v for k, v in ep.items() if k != "auth_key"}
        safe_ep["account_name"] = all_accounts.get(safe_ep.get("account_id", ""), "")
        safe_endpoints.append(safe_ep)

    return {"models": result, "pools": pools, "endpoints": safe_endpoints}


@app.post("/dashboard/admin/models/save")
async def admin_save_model(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        alias = str(body.get("alias", "")).strip().lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not alias:
        raise HTTPException(status_code=400, detail="alias is required")

    from src.backend.model_config import save_model_config
    body.pop("alias", None)
    save_model_config(alias, **body)

    # If saving a custom endpoint model with account_id, also ensure the endpoint
    # has that model enabled in its enabled_models list
    account_id = body.get("account_id") or ""
    if account_id:
        from src.backend.model_config import list_model_configs
        from src.backend.endpoints import list_endpoints_db
        for ep in list_endpoints_db():
            if ep.get("account_id") == account_id and alias in (ep.get("models") or []):
                enabled = list(ep.get("enabled_models") or [])
                if alias not in enabled:
                    enabled.append(alias)
                    from src.backend.endpoints import update_endpoint_db
                    update_endpoint_db(ep["name"], enabled_models=enabled)
                break

    from src.core.api_config import reload_model_config
    reload_model_config()

    from .helpers import update_env_var
    prefix = alias.upper().replace("-", "_")
    for field in ("rpm", "tpm", "rpd"):
        if field in body:
            update_env_var(f"{prefix}_{field.upper()}", str(body[field]))
    if "model_id" in body:
        update_env_var(f"{prefix}_MODEL", str(body["model_id"]))

    # Reload env vars into os.environ so config picks them up
    from dotenv import load_dotenv
    from src.core.config_n_logg import ENV_PATH
    load_dotenv(ENV_PATH, override=True)

    return {"status": "success", "alias": alias}


@app.post("/dashboard/admin/models/delete")
async def admin_delete_model(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
        alias = str(body.get("alias", "")).strip().lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not alias:
        raise HTTPException(status_code=400, detail="alias is required")

    from src.backend.model_config import delete_model_config
    account_id = body.get("account_id") or ""
    result = delete_model_config(alias, account_id)

    from src.core.api_config import reload_model_config
    reload_model_config()

    from .helpers import remove_env_var
    prefix = alias.upper().replace("-", "_")
    for field in ("rpm", "tpm", "rpd"):
        remove_env_var(f"{prefix}_{field.upper()}")
    remove_env_var(f"{prefix}_MODEL")

    return {"status": "success", "alias": alias}

import json
from fastapi import WebSocket, WebSocketDisconnect

from .app_init import app
from .auth_session import _verify_session_token
from ...realtime.websocket import ws_manager
from src.core.config_n_logg.logger import logger_system as logger


@app.websocket("/dashboard/ws")
async def dashboard_websocket(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    if origin:
        import urllib.parse
        host = websocket.headers.get("host", "").split(":")[0]
        parsed_origin = urllib.parse.urlparse(origin).hostname or ""
        if parsed_origin != host and parsed_origin not in ("localhost", "127.0.0.1"):
            logger.warning("[WS] Rejected untrusted origin: %s (host: %s)", origin, host)
            await websocket.close(code=4003)
            return

    token = websocket.query_params.get("token", "")
    account = _verify_session_token(token)
    if not account:
        await websocket.close(code=4001)
        return

    from src.backend.accounts.manager import find_account_by_id, find_account_by_name
    from src.backend.keys.accounts import get_credential_db

    account_id = account.get("account_id")
    name = account.get("name")
    acc = find_account_by_id(account_id) if account_id else None
    if not acc and name:
        candidate = find_account_by_name(name)
        if candidate and (not account_id or candidate.get("account_id") == account_id):
            acc = candidate

    if not acc or not acc.get("enabled", 1):
        await websocket.close(code=4001)
        return

    if account_id and acc.get("account_id") and acc["account_id"] != account_id:
        await websocket.close(code=4001)
        return

    account["tier"] = acc.get("tier", "free")
    account["name"] = acc.get("name", account.get("name", ""))
    account_id = acc.get("account_id") or account_id or "unknown"

    if account_id and "iat" in account:
        cred = get_credential_db(account_id)
        if cred and cred.get("updated_at") and cred["updated_at"] > account["iat"]:
            await websocket.close(code=4001)
            return

    await ws_manager.connect(websocket, account_id, {
        "name": account.get("name", ""),
        "tier": account.get("tier", "free"),
    })

    logger.info("[WS] Client connected: %s (%s)", account.get("name"), account_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws_manager.send_to(websocket, {
                    "type": "error",
                    "channel": "system",
                    "message": "Invalid JSON"
                })
                continue

            msg_type = data.get("type", "")
            channels = data.get("channels", [])

            if msg_type == "subscribe":
                subscribed = []
                rejected = []
                user_tier = account.get("tier", "free")
                for ch in channels:
                    if ch.startswith("log:"):
                        logical = ch.split("log:", 1)[1].split(":", 1)[0]
                        if logical in ("keys", "system", "web") and user_tier != "admin":
                            rejected.append(ch)
                            continue
                    await ws_manager.subscribe(websocket, ch)
                    subscribed.append(ch)
                    logger.debug("[WS] %s subscribed to %s", account.get("name"), ch)

                if rejected:
                    await ws_manager.send_to(websocket, {
                        "type": "error",
                        "channel": "system",
                        "message": f"Permission denied for log channels: {', '.join(rejected)}"
                    })
                if subscribed:
                    await ws_manager.send_to(websocket, {
                        "type": "subscribed",
                        "channel": "system",
                        "channels": subscribed
                    })

            elif msg_type == "unsubscribe":
                for ch in channels:
                    await ws_manager.unsubscribe(websocket, ch)
                await ws_manager.send_to(websocket, {
                    "type": "unsubscribed",
                    "channel": "system",
                    "channels": channels
                })

            elif msg_type == "ping":
                await ws_manager.send_to(websocket, {"type": "pong", "channel": "system"})

    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected: %s", account.get("name"))
    except Exception as e:
        logger.warning("[WS] Error for %s: %s", account.get("name"), e)
    finally:
        await ws_manager.disconnect(websocket)


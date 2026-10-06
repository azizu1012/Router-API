import json
from fastapi import WebSocket, WebSocketDisconnect

from .app_init import app
from .auth_session import _verify_session_token
from ...websocket_manager import ws_manager
from src.core.config_n_logg.logger import logger_system as logger


@app.websocket("/dashboard/ws")
async def dashboard_websocket(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    account = _verify_session_token(token)
    if not account:
        await websocket.close(code=4001)
        return

    account_id = account.get("account_id", "unknown")
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

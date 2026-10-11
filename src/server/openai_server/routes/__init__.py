from pathlib import Path
from fastapi.staticfiles import StaticFiles

from .app_init import app
from . import standard_routes as standard_routes
from . import completions_routes as completions_routes
from . import dashboard_routes as dashboard_routes
from . import admin as admin  # admin/ package (keys, endpoints, accounts)
from . import opencode_routes as opencode_routes
from . import ws_routes as ws_routes
from . import self_service as self_service  # Self-service endpoints and aliases
from src.server.pass_through_server.routes import gemini_routes as gemini_routes
from src.server.openai_server import mcp_routes as mcp_routes

# MCP before the frontend mount, for the same reason the comment below gives:
# the static mount matches every path, so whichever is registered first wins.
# Registered after, /mcp would be answered by StaticFiles — which is exactly how
# POST /mcp came back 405 instead of reaching the MCP server.
mcp_routes.mount_mcp(app)

# Register self-service routes (member endpoints and aliases)
app.include_router(self_service.router)

# Mount static frontend LAST so API routes take priority
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

__all__ = ["app", "standard_routes", "completions_routes", "dashboard_routes", "admin", "opencode_routes", "ws_routes", "gemini_routes"]

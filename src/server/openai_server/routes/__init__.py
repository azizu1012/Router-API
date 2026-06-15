from pathlib import Path
from fastapi.staticfiles import StaticFiles

from .app_init import app
from . import standard_routes as standard_routes
from . import completions_routes as completions_routes
from . import dashboard_routes as dashboard_routes
from . import admin as admin  # admin/ package (keys, endpoints, accounts)
from . import opencode_routes as opencode_routes
from . import ws_routes as ws_routes
from src.server.pass_through_server.routes import gemini_routes as gemini_routes

# Mount static frontend LAST so API routes take priority
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

__all__ = ["app", "standard_routes", "completions_routes", "dashboard_routes", "admin", "opencode_routes", "ws_routes", "gemini_routes"]

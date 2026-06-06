from pathlib import Path
from fastapi.staticfiles import StaticFiles

from .app_init import app
from . import standard_routes
from . import completions_routes
from . import dashboard_routes
from . import admin_routes
from . import opencode_routes
from src.server.pass_through_server.routes import gemini_routes

# Mount static frontend LAST so API routes take priority
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

__all__ = ["app"]

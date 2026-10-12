"""Dashboard static page route."""

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from src.core.config_n_logg.logger import logger_web
from ..app_init import app


FRONTEND_DIR = Path(__file__).resolve().parents[4] / "frontend"


@app.get("/stats", response_class=HTMLResponse)
@app.get("/stats/{path:path}", response_class=HTMLResponse)
async def stats_page(path: str | None = None):
    html_path = FRONTEND_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard index.html not found")
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger_web.error("[Stats] Failed to read index.html: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error reading dashboard")


__all__ = ["stats_page"]


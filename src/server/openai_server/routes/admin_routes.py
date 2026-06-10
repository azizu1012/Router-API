"""Thin backward-compatible facade — delegates to ``routes.admin`` package."""
from .admin import *  # noqa: F401, F403 — triggers route registration via @app.post decorators

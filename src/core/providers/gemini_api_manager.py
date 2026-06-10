"""Thin backward-compatible facade — delegates to the new ``gemini`` package."""
from src.core.providers.gemini import GeminiAPIManager, api_manager  # noqa: F401

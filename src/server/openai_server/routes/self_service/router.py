"""Shared router object for member self-service route groups."""

from fastapi import APIRouter

from src.core.config_n_logg.logger import logger_api as logger

router = APIRouter(prefix="/api/me", tags=["self-service"])

__all__ = ["router", "logger"]


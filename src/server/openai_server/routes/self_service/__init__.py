"""Registration module for member self-service route groups."""

from .router import router
from . import endpoints
from . import aliases
from . import alias_templates

__all__ = ["router", "endpoints", "aliases", "alias_templates"]


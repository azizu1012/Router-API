"""Dashboard route registration split by account and system concerns."""

from . import account
from . import system

__all__ = ["account", "system"]


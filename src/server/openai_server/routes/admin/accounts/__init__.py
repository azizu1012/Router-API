"""Admin account routes grouped by lifecycle, keys, and recovery."""

from . import keys
from . import lifecycle
from . import recovery

__all__ = ["keys", "lifecycle", "recovery"]


"""Claude Proxy — pure format converter.

Delegates all key management, pool logic, retry, and quota to PoolManager.
"""

from typing import Any, Dict, List, Optional
import re

from src.core.config_n_logg import config
from .proxy_nonstream import ClaudeProxyNonstreamMixin
from .proxy_stream import ClaudeProxyStreamMixin


def _model_supports_thinking(model_id: str) -> bool:
    m = model_id.lower()
    if "lite" in m:
        return False
    return any(x in m for x in ["gemini-2", "gemini-2.5", "gemini-3", "gemini-3.5"])


class ClaudeProxy(ClaudeProxyNonstreamMixin, ClaudeProxyStreamMixin):
    """Orchestrates Claude chat completion requests."""
    pass


claude_proxy = ClaudeProxy()

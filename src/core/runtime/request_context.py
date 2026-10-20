"""Request-scoped tracing context shared by proxy and provider layers."""
from contextvars import ContextVar
from typing import Optional
import uuid

_request_id: ContextVar[Optional[str]] = ContextVar("router_request_id", default=None)


def get_request_id() -> str:
    return _request_id.get() or ""


def set_request_id(request_id: Optional[str] = None) -> str:
    value = (request_id or "").strip()[:128] or uuid.uuid4().hex
    _request_id.set(value)
    return value

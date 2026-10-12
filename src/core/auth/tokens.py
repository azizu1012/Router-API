"""Transport-neutral authentication primitives.

The server layer turns these domain errors into HTTP responses. Keeping token
parsing and account lookup here prevents each protocol route from growing its
own slightly different authentication rules.
"""

from typing import Any, Dict, Optional

from src.core.accounts import account_manager


class AuthenticationFailure(Exception):
    """Raised when a request does not resolve to an enabled account."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def bearer_token(authorization: Optional[str]) -> str:
    raw = str(authorization or "").strip()
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def resolve_auth_header(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    if x_api_key and x_api_key.strip():
        value = x_api_key.strip()
        return value if value.lower().startswith("bearer ") else f"Bearer {value}"
    return authorization if authorization and authorization.strip() else None


def authenticate(authorization: Optional[str]) -> Dict[str, Any]:
    token = bearer_token(authorization)
    if not token:
        raise AuthenticationFailure("Missing API Key")

    account = account_manager.find_by_key(token)
    if not account and token.lower().startswith("sk-ant-"):
        account = account_manager.find_by_key(token[7:])
    if not account:
        raise AuthenticationFailure("Unauthorized API Key")
    return account


def auth_key_prefix(account: Dict[str, Any]) -> str:
    value = account.get("auth_key") or ""
    return value[-8:] if len(value) >= 8 else value


def count_openai_images(messages: list) -> int:
    return sum(
        1
        for message in messages or []
        for item in (message.get("content") or [])
        if isinstance(message.get("content"), list)
        and isinstance(item, dict)
        and item.get("type") in ("image_url", "image")
    )



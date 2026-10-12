"""Authentication domain services shared by all protocol adapters."""

from .tokens import (
    AuthenticationFailure,
    authenticate,
    auth_key_prefix,
    bearer_token,
    count_openai_images,
    resolve_auth_header,
)
from .limits import (
    AccountLimitFailure,
    TokenLimitFailure,
    enforce_account_limit,
    enforce_token_limits,
    token_limit_message,
)

__all__ = [
    "AuthenticationFailure",
    "authenticate",
    "auth_key_prefix",
    "bearer_token",
    "count_openai_images",
    "resolve_auth_header",
    "enforce_account_limit",
    "enforce_token_limits",
    "token_limit_message",
    "AccountLimitFailure",
    "TokenLimitFailure",
]


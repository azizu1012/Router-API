from typing import Any, Dict
from fastapi import HTTPException

from src.core.auth import (
    AuthenticationFailure,
    authenticate,
    auth_key_prefix,
    bearer_token,
    count_openai_images,
    resolve_auth_header,
    enforce_account_limit,
    enforce_token_limits,
    token_limit_message,
    AccountLimitFailure,
    TokenLimitFailure,
)


_bearer_token = bearer_token


_resolve_auth = resolve_auth_header


def _check_auth(authorization: str | None) -> Dict[str, Any]:
    try:
        return authenticate(authorization)
    except AuthenticationFailure as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": exc.message, "type": "authentication_error"}},
        ) from exc


_token_limit_message = token_limit_message


async def _enforce_token_limits(account: Dict[str, Any], estimated_tokens: int = 0) -> Any:
    try:
        return await enforce_token_limits(account, estimated_tokens)
    except TokenLimitFailure as exc:
        raise HTTPException(
            status_code=429,
            detail={"error": {"message": exc.message, "type": "rate_limit_error"}},
        ) from exc


_count_openai_images = count_openai_images


async def _apply_account_limit(account: Dict[str, Any], body: Dict[str, Any], is_opencode: bool = False) -> None:
    try:
        await enforce_account_limit(account, body, is_opencode=is_opencode)
    except AccountLimitFailure as exc:
        raise HTTPException(
            status_code=429,
            detail={"error": {"message": exc.message, "type": "rate_limit_error"}},
        ) from exc


_auth_key_prefix = auth_key_prefix

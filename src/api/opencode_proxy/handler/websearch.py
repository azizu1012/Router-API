"""Web search configuration for OpenCode proxy."""

from typing import Any, Dict, Optional

from src.core.config_n_logg import config


def get_auth_key_prefix(account: Optional[Dict[str, Any]]) -> str:
    if not account:
        return ""
    ak = account.get("auth_key") or ""
    return ak[-8:] if len(ak) >= 8 else ak


VALID_ENGINES = {"duckduckgo", "disabled"}

def resolve_search_engine(body: Dict[str, Any], account: Optional[Dict[str, Any]]) -> str:
    """Resolve which engine serves a search, in precedence order.

    1. An explicit `search_engine` in the request body. The client asked for a
       specific engine; honour it.
    Explicitly disabled requests stay disabled. Every enabled request uses the
    Router's own citation search implementation.
    """
    body_engine = (body.get("search_engine") or "").strip().lower()
    return body_engine if body_engine in VALID_ENGINES else "duckduckgo"

def should_enable_web_search(body: Dict[str, Any], account: Optional[Dict[str, Any]]) -> bool:
    """Check if web search should be enabled for this request.

    Respects explicit client-level disable flags.
    """
    engine = resolve_search_engine(body, account)
    if engine == "disabled":
        return False
    for flag in ["web_search", "search"]:
        if flag in body and body[flag] is False:
            return False
    return bool(
        body.get("web_search") is True
        or body.get("search") is True
        or body.get("_hosted_search") is True
    )



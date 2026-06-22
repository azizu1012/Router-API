"""Web search configuration for OpenCode proxy."""

from typing import Any, Dict, Optional

from src.core.config_n_logg import config


def get_auth_key_prefix(account: Optional[Dict[str, Any]]) -> str:
    if not account:
        return ""
    ak = account.get("auth_key") or ""
    return ak[-8:] if len(ak) >= 8 else ak


VALID_ENGINES = {"auto", "google_grounding", "duckduckgo", "disabled"}

def resolve_search_engine(body: Dict[str, Any], account: Optional[Dict[str, Any]]) -> str:
    """Resolve effective search engine from request body override or account config.
    
    Body override takes precedence. Falls back to account setting, then 'auto'.
    """
    body_engine = (body.get("search_engine") or "").strip().lower()
    if body_engine in VALID_ENGINES:
        return body_engine
    if account:
        acct_engine = (account.get("search_engine") or "").strip().lower()
        if acct_engine in VALID_ENGINES:
            return acct_engine
    return "auto"

def should_enable_web_search(body: Dict[str, Any], account: Optional[Dict[str, Any]]) -> bool:
    """Check if web search should be enabled for this request.

    Respects explicit client-level disable flags.
    """
    engine = resolve_search_engine(body, account)
    if engine == "disabled":
        return False
    for flag in ["web_search", "search", "google_search", "grounding"]:
        if flag in body and body[flag] is False:
            return False
    return bool(
        body.get("web_search") is True
        or body.get("search") is True
        or body.get("google_search") is True
        or body.get("grounding") is True
    )


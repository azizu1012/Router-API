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
    """Resolve which engine serves a search, in precedence order.

    1. An explicit `search_engine` in the request body. The client asked for a
       specific engine; honour it.
    2. 'auto' — grounding first, DuckDuckGo behind it — when the caller already
       decided the request is a hosted-search request. The Responses route sets
       this, because a Responses client that passes tools:[{"type":"web_search"}]
       is asking for the hosted capability.
    3. 'duckduckgo' otherwise.

    The default used to be 'auto' with an account-level override. That meant the
    common case — a chat request that happens to enable search — spent a Gemini
    call on grounding before falling back, and the only way to avoid it was a
    dashboard toggle. DuckDuckGo answers the same questions and touches no Gemini
    key, so it is now the floor and the Responses route opts up to grounding
    explicitly.

    The account setting is still read so an existing row keeps its meaning, but
    nothing in the dashboard writes it any more.
    """
    body_engine = (body.get("search_engine") or "").strip().lower()
    if body_engine in VALID_ENGINES:
        return body_engine
    if body.get("web_search") is True and body.get("_hosted_search"):
        return "auto"
    if account:
        acct_engine = (account.get("search_engine") or "").strip().lower()
        if acct_engine in VALID_ENGINES:
            return acct_engine
    return "duckduckgo"

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


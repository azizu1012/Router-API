from typing import Dict, List, Tuple, Optional, Any

from src.tools.search.orchestration.manager import execute_hybrid_search

# Same presentation budget the Claude proxy applies. Held here rather than at the
# call site so the two dialects cannot drift apart again — the Claude arm was
# bounded while this one was left unbounded, which is the exact bug this fixes.
HOSTED_SEARCH_MAX_RESULTS = 5
HOSTED_SEARCH_MAX_EVIDENCE_CHARS = 900


async def execute_opencode_search(
    queries: List[str],
    model_alias_or_name: str = "",
    search_engine: str = "duckduckgo",
    auth_key_prefix: str = "",
    account: Optional[Dict[str, Any]] = None,
    result_limit: Optional[int] = None,
    evidence_chars: Optional[int] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Execute specialized web search for OpenCode proxy.

    Uses Router-owned DuckDuckGo citation search directly without spending model quota.

    `result_limit` and `evidence_chars` bound what is handed back, and they matter
    more here than on the Claude path: this text becomes a tool result the model
    reads and then re-sends on every later turn of the session, so an unbounded
    payload is paid for once here and again in each subsequent request. The engine
    still ranks its full internal result set; only the presentation is trimmed.
    """
    if not queries:
        return "", []

    return await execute_hybrid_search(
        queries,
        # The caller's engine is honoured. Hardcoding it ignored the argument
        # every caller above had already resolved.
        search_engine=search_engine or "duckduckgo",
        auth_key_prefix=auth_key_prefix,
        account=account,
        result_limit=result_limit,
        evidence_chars=evidence_chars,
    )


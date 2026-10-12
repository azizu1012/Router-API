from typing import Dict, List, Tuple, Optional, Any

from src.tools.search.orchestration.manager import execute_hybrid_search


async def execute_opencode_search(
    queries: List[str],
    model_alias_or_name: str = "",
    search_engine: str = "duckduckgo",
    auth_key_prefix: str = "",
    account: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Execute specialized web search for OpenCode proxy.

    Uses Router-owned DuckDuckGo citation search directly without spending model quota.
    """
    if not queries:
        return "", []

    return await execute_hybrid_search(
        queries,
        search_engine="duckduckgo",
        auth_key_prefix=auth_key_prefix,
        account=account,
    )


"""Anthropic Messages protocol helpers grouped by concern.

The package namespace is the shared API consumed by both Claude proxy paths;
implementation lives in request, response, usage, and search modules.
"""

from .request import (
    apply_tool_choice_to_tools,
    extract_sampling_params,
    extract_tool_choice,
)
from .response import (
    build_error_event,
    log_stream_failure,
    map_stop_reason,
)
from .search import (
    _MAX_SMART_BUDGET_CAP,
    _fetch_query_from_url,
    _search_budget_left,
    _search_citations,
    _search_failed,
    _server_tool_record,
    _server_tool_use_block,
    _web_search_result_block,
    filter_citations,
    is_redundant_query,
    run_server_tool,
    server_search_limits,
)
from .usage import (
    MIN_CACHEABLE_TOKENS,
    compute_usage,
    count_cache_breakpoints,
    estimate_input_tokens,
    estimate_tokens,
    thinking_signature,
)

__all__ = [
    "apply_tool_choice_to_tools",
    "extract_sampling_params",
    "extract_tool_choice",
    "build_error_event",
    "log_stream_failure",
    "map_stop_reason",
    "filter_citations",
    "is_redundant_query",
    "run_server_tool",
    "server_search_limits",
    "MIN_CACHEABLE_TOKENS",
    "compute_usage",
    "count_cache_breakpoints",
    "estimate_input_tokens",
    "estimate_tokens",
    "thinking_signature",
    "_MAX_SMART_BUDGET_CAP",
    "_fetch_query_from_url",
]


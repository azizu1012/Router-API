"""Search orchestration engine: data, text normalization, ranking, and manager."""

from .manager import AdvancedSearchManager, search_manager, search_with_citations, web_search

__all__ = [
    "AdvancedSearchManager",
    "search_manager",
    "search_with_citations",
    "web_search",
]


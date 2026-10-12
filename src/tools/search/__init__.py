"""Modular Router-owned web search package."""

from .engine.manager import AdvancedSearchManager, search_manager, search_with_citations, web_search

__all__ = ["AdvancedSearchManager", "search_manager", "search_with_citations", "web_search"]


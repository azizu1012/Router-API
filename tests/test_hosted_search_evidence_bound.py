import asyncio

import pytest


@pytest.mark.anyio
async def test_hosted_search_page_reads_use_evidence_semaphore(monkeypatch):
    from src.tools.search.engine.manager import AdvancedSearchManager

    manager = AdvancedSearchManager()
    manager.search_web_mode = "grounded"
    manager.search_grounded_top_links = 4
    manager.search_timeout_seconds = 2
    manager.evidence_semaphore = asyncio.Semaphore(2)

    records = [
        {
            "title": f"Source {idx}",
            "url": f"https://example{idx}.com/article",
            "snippet": "Useful source content for the query.",
            "domain": f"example{idx}.com",
            "provider": "duckduckgo",
        }
        for idx in range(4)
    ]
    monkeypatch.setattr(
        manager,
        "_search_duckduckgo_records",
        lambda *args, **kwargs: asyncio.sleep(0, result=list(records)),
    )
    monkeypatch.setattr(manager, "_is_search_result_sufficient", lambda *args: True)

    active = 0
    peak = 0

    async def fake_fetch(url):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "Evidence text", {}

    monkeypatch.setattr(manager, "_fetch_page_data", fake_fetch)

    text, citations = await manager.search_with_citations("latest technology news")

    assert text
    assert citations
    assert peak == 2


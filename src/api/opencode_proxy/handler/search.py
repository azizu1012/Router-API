from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any

from src.core.providers.genai_types import types as gt
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.providers.gemini_api_manager import api_manager
from src.core.providers.search_manager import execute_hybrid_search


async def execute_opencode_search(
    queries: List[str],
    model_alias_or_name: str,
    search_engine: str = "auto",
    auth_key_prefix: str = "",
    account: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Execute specialized web search for OpenCode proxy.
    
    If the requested model is a 'lite' model, it falls back to the standard search to save tokens.
    Otherwise (for 'flash' or larger models), it calls a 'lite' model as a sub-agent to perform 
    a detailed search, read results, and write a thorough report with cited sources and links.
    """
    if not queries:
        return "", []

    is_lite = "lite" in str(model_alias_or_name).lower()
    if is_lite:
        logger.info("[OpenCode Search] Model %s is lite, using standard search", model_alias_or_name)
        return await execute_hybrid_search(queries, search_engine=search_engine, auth_key_prefix=auth_key_prefix, account=account)

    # Resolve the customized sub-agent model (if any)
    target_model = None
    if account:
        target_model = account.get("subagent_model") or account.get("agent_model") or account.get("sub_agent_model")
    if not target_model:
        import os
        target_model = os.getenv("OPENCODE_SUB_AGENT_MODEL") or os.getenv("SUB_AGENT_MODEL")

    models_to_try = [target_model] if target_model else ["gemini-flash-lite", "gemini-flash-25-lite"]

    logger.info("[OpenCode Search] Executing sub-agent search via models %s", models_to_try)

    async def run_subagent_query(query: str) -> Dict[str, Any]:
        # ── DuckDuckGo Only ──
        if search_engine == "duckduckgo":
            try:
                logger.info("[OpenCode Search Sub-agent] Searching DuckDuckGo for query: %s", query)
                from src.tools.duckduckgo import search_with_citations
                text, cits = await search_with_citations(query)
                if text:
                    logger.info("[OpenCode Search Sub-agent] DuckDuckGo search successful with %d citations.", len(cits))
                    return {"query": query, "snippet": text, "citations": cits}
            except Exception as ddg_err:
                logger.warning("[OpenCode Search Sub-agent] DuckDuckGo search failed for query '%s': %s", query, ddg_err)
            return {"query": query, "snippet": "", "citations": []}

        # ── Google Grounding via Gemini sub-agent (for auto/google_grounding) ──
        if search_engine in ("auto", "google_grounding"):
            for model in models_to_try:
                try:
                    logger.info("[OpenCode Search Sub-agent] Running Google Grounding with model %s for query: %s", model, query)
                    current_time_str = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p")
                    
                    system_instruction = (
                        "You are a professional, thorough research assistant. Your task is to search the web and compile a detailed, factually accurate report on the given topic.\n"
                        "Rules:\n"
                        "1. Provide a comprehensive summary with detailed context, dates, facts, and names. Do NOT limit your answer to a few sentences.\n"
                        "2. For every key piece of information, include the article title and FULL article URL exactly as provided. NEVER output just a homepage domain like `example.com`.\n"
                        "3. Only cite information that comes from the search results grounding metadata. Do not fabricate any links or sources.\n"
                        "4. If different sources have conflicting information, present both views clearly.\n"
                        "5. Keep the report clear, structured, and easy to read."
                    )
                    
                    contents = [gt.Content(role="user", parts=[gt.Part.from_text(
                        text=(
                            f"Today is {current_time_str}.\n"
                            f"Search and write a detailed report on: {query} current as of {datetime.now().strftime('%B %Y')}\n"
                            "Include all relevant details, numbers, dates. For each source, provide the FULL article URL (not just the domain)."
                        )
                    )])]

                    if account:
                        try:
                            from src.core.limits.account_limiter import get_effective_limits_by_pool
                            from src.core.limits import account_limiter
                            estimated_tokens = len(query) // 4 + 1200
                            is_sub_lite = "lite" in str(model).lower() or "flash-lite" in str(model).lower()
                            sub_pool = "lite" if is_sub_lite else "flash"
                            eff_rpm, eff_tpm, eff_rpd = await get_effective_limits_by_pool(account, sub_pool)
                            effective = {**account, "rpm": eff_rpm, "tpm": eff_tpm, "rpd": eff_rpd}
                            allowed, reason = await account_limiter.acquire(effective, estimated_tokens, sub_pool)
                            if not allowed:
                                logger.warning("[OpenCode Search Sub-agent] Rate limit exceeded for %s pool: %s", sub_pool, reason)
                                raise RuntimeError(f"quota_exhausted: Account rate limit exceeded for {sub_pool} pool: {reason}")
                        except Exception as s_err:
                            if "quota_exhausted" in str(s_err):
                                raise
                            logger.warning("[OpenCode Search Sub-agent] Failed to check rate limit: %s", s_err)

                    gresult = await api_manager.call_gemini(
                        model_alias=model,
                        system_instruction=system_instruction,
                        contents=contents,
                        max_tokens=1024,
                        temperature=0.2,
                        web_search=True,
                        account=account
                    )

                    response = gresult.get("response")
                    snippet = (getattr(response, "text", "") or "").strip()

                    used_key = gresult.get("api_key", "")
                    kp = used_key[-8:] if used_key else "unknown"
                    from src.core.usage_logger import log_usage
                    await log_usage(
                        model_alias=gresult.get("model_id", "gemini-3.1-flash-lite"),
                        key_prefix=kp,
                        prompt_tokens=gresult.get("input_tokens", 0),
                        completion_tokens=gresult.get("output_tokens", 0),
                        auth_key_prefix=auth_key_prefix,
                    )

                    citations: List[Dict[str, Any]] = []
                    candidates = getattr(response, "candidates", None) or []
                    if candidates:
                        grounding = getattr(candidates[0], "grounding_metadata", None)
                        if grounding:
                            chunks = getattr(grounding, "grounding_chunks", []) or []
                            seen_links: set = set()
                            for chunk in chunks:
                                web = getattr(chunk, "web", None)
                                if web:
                                    title = getattr(web, "title", "") or "Source"
                                    uri = getattr(web, "uri", "")
                                    if uri and uri not in seen_links:
                                        seen_links.add(uri)
                                        citations.append({"title": title, "url": uri})

                    logger.info("[OpenCode Search Sub-agent] Google Grounding finished with model %s: %s with %d citations", model, query, len(citations))
                    return {"query": query, "snippet": snippet, "citations": citations}

                except Exception as e:
                    err_str = str(e)
                    if "quota_exhausted" in err_str:
                        logger.warning("[OpenCode Search Sub-agent] Quota exhausted for model %s, query '%s'.", model, query)
                        break
                    logger.warning("[OpenCode Search Sub-agent] Google Grounding failed with model %s for query '%s': %s", model, query, e)

        # ── DuckDuckGo fallback (only for "auto" mode) ──
        if search_engine == "auto":
            try:
                logger.info("[OpenCode Search Sub-agent] Google Grounding failed for '%s'. Falling back to DuckDuckGo...", query)
                from src.tools.duckduckgo import search_with_citations
                text, cits = await search_with_citations(query)
                if text:
                    logger.info("[OpenCode Search Sub-agent] DuckDuckGo fallback successful with %d citations.", len(cits))
                    return {"query": query, "snippet": text, "citations": cits}
            except Exception as ddg_err:
                logger.warning("[OpenCode Search Sub-agent] DuckDuckGo fallback failed for query '%s': %s", query, ddg_err)

        return {"query": query, "snippet": "", "citations": []}

    # Run queries sequentially (1 per turn to avoid hammering keys)
    logger.info("[OpenCode Search] Executing queries sequentially")
    results = []
    for q in queries[:1]:
        result = await run_subagent_query(q)
        results.append(result)
    valid_results = [r for r in results if r.get("snippet")]
    if not valid_results:
        return "", []

    # Collect all unique citations
    all_citations: List[Dict[str, Any]] = []
    seen_urls: set = set()
    for r in valid_results:
        for cit in r.get("citations", []):
            url = cit.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_citations.append(cit)

    # Format the results into a detailed context block
    current_time_str = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p")
    lines = [
        f"[Web Search Results — {current_time_str}]",
        "─" * 50,
    ]
    for i, result in enumerate(valid_results, 1):
        query = result.get("query", "")
        snippet = result.get("snippet", "").strip()
        lines.append(f"\n### Kết quả {i} — Tìm kiếm cho query: \"{query}\"\n")
        lines.append(snippet)
        lines.append("")
    lines.append("─" * 50)
    lines.append("[End of Web Search Results]")

    search_context = "\n".join(lines)
    return search_context, all_citations

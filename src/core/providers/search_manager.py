import json
import asyncio
from typing import Dict, List, Tuple, Optional, Any
from google.genai import types as gt
from src.core.config_n_logg.logger import logger_system as logger
from src.core.providers.gemini_api_manager import api_manager


from datetime import datetime


async def extract_search_queries(prompt_text: str, messages: list, auth_key_prefix: str = "", account: Optional[Dict[str, Any]] = None) -> List[str]:
    """Use Gemini to detect search intent and extract 1 clean search query.
    
    Returns an empty list if no search is required.
    """
    current_time_str = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p")
    system_instruction = (
        "You are an AI search query generator. Analyze the user's prompt and conversation context.\n"
        "Determine if the user is asking about current events, real-time prices, recent releases, news, "
        "or facts requiring search. If a search is needed, extract exactly 1 concise keyword query to "
        "search the web.\n"
        "If no search is needed (e.g. conversational greetings, general coding questions, math, "
        "creative writing, or general historical facts), return an empty list: [].\n"
        "You MUST respond ONLY with a JSON array of strings. Do not add markdown backticks or explanations.\n"
        f"[Current Time Context: {current_time_str}]"
    )

    if account:
        try:
            from src.core.limits.account_limiter import get_effective_limits_by_pool
            from src.core.limits import account_limiter
            estimated_tokens = (len(prompt_text[:800]) + len(str(messages[-1:]))) // 4 + 256
            eff_rpm, eff_tpm, eff_rpd = await get_effective_limits_by_pool(account, "lite")
            effective = {**account, "rpm": eff_rpm, "tpm": eff_tpm, "rpd": eff_rpd}
            allowed, reason = await account_limiter.acquire(effective, estimated_tokens, "lite")
            if not allowed:
                logger.warning("Account rate limit exceeded for lite pool in query extraction: %s", reason)
                raise RuntimeError(f"quota_exhausted: Account rate limit exceeded for lite pool in query extraction: {reason}")
        except Exception as q_err:
            if "quota_exhausted" in str(q_err):
                raise
            logger.warning("Failed to check rate limit for query extraction: %s", q_err)

    try:
        # Use only the last user message and a short snippet of prompt_text
        # to minimize TPM consumption on the lite pool.
        short_prompt = prompt_text[:800] if len(prompt_text) > 800 else prompt_text
        last_msg = messages[-1:] if messages else []
        
        res_dict = None
        for alias in ["gemini-flash", "gemini-flash-lite", "gemini-flash-25-lite"]:
            try:
                res_dict = await api_manager.call_gemini_json(
                    model_alias=alias,
                    system_instruction=system_instruction,
                    prompt_text=f"User prompt: {short_prompt}\nLast message: {str(last_msg)}",
                    account=account,
                )
                break
            except Exception as qe:
                err_str = str(qe).lower()
                if any(x in err_str for x in ["quota", "exhausted", "limit", "rate", "no_available_key", "frozen"]):
                    logger.warning("Failed to extract search queries using %s: %s. Trying next...", alias, qe)
                    continue
                logger.error("Error extracting search queries with %s: %s", alias, qe)
                raise
                
        if not res_dict:
            raise RuntimeError("All models in query extraction failed")
        
        json_output = res_dict.get("text", "")
        cleaned = json_output.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if len(lines) > 2:
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()

        # Log usage to SQLite
        used_key = res_dict.get("api_key", "")
        kp = used_key[-8:] if used_key else "unknown"
        from src.core.usage_logger import log_usage
        await log_usage(
            model_alias=res_dict.get("model_id", "gemini-3.1-flash-lite"),
            key_prefix=kp,
            prompt_tokens=res_dict.get("input_tokens", 0),
            completion_tokens=res_dict.get("output_tokens", 0),
            auth_key_prefix=auth_key_prefix,
        )

        queries = json.loads(cleaned)
        if isinstance(queries, list):
            return [str(q).strip() for q in queries if str(q).strip()][:3]
    except Exception as e:
        if "quota_exhausted" in str(e):
            raise
        logger.warning("Failed to extract search queries using Gemini: %s. Fallback to no queries.", e)
    
    return []


# ── Search result structure ─────────────────────────────────────
# Each result: {"query": str, "snippet": str, "citations": [{"title": str, "url": str}]}

def _format_search_context(results: List[Dict[str, Any]]) -> str:
    """Format structured search results into a context block for the main model.
    
    Produces a clearly-attributed snippet format:
    
    [Web Search Results]
    ─────────────────────
    **Query: yaoguang hsr playable**
    > Yaoguang is a 5-star Physical character from the Elation path...
    Sources: [Honkai Wiki](https://...) · [Gamerant](https://...)
    
    [End of Web Search Results]
    """
    if not results:
        return ""

    current_time_str = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p")
    lines = [
        f"[Web Search Results — {current_time_str}]",
        "─" * 50,
    ]

    all_citations: List[Dict[str, Any]] = []
    seen_urls: set = set()

    for i, result in enumerate(results, 1):
        query = result.get("query", "")
        snippet = result.get("snippet", "").strip()
        citations = result.get("citations", [])

        if not snippet:
            continue

        lines.append(f"\n**Kết quả {i} — Query: {query}**")
        # Indent snippet as blockquote-style
        for line in snippet.splitlines():
            lines.append(f"  {line}" if line.strip() else "")

        # Inline source attribution for this snippet
        cite_parts = []
        for cit in citations:
            url = cit.get("url", "")
            title = cit.get("title", "Source") or "Source"
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_citations.append(cit)
                cite_parts.append(f"[{title}]({url})")
        if cite_parts:
            lines.append(f"  📎 Nguồn: {' · '.join(cite_parts)}")

    lines.append("\n" + "─" * 50)
    lines.append("[End of Web Search Results]")

    return "\n".join(lines)


def _format_citations_footer(citations: List[Dict[str, Any]]) -> str:
    """Format a deduplicated citations list as a Sources section for the final response.
    Deduplicates by domain name to ensure clean and non-repetitive sources list.
    """
    if not citations:
        return ""
    
    from urllib.parse import urlparse
    def get_domain(url: str, title: str = "") -> str:
        try:
            host = (urlparse(url).netloc or "").lower().strip()
            if host.startswith("www."):
                host = host[4:]
            if host == "vertexaisearch.cloud.google.com" and title:
                # Vertex redirect URL; use the source title (like 'laodong.vn') as the deduplication domain key
                return title.lower().strip()
            return host
        except Exception:
            return ""

    seen_domains = set()
    unique = []
    for c in citations:
        url = c.get("url", "") or c.get("uri", "")
        if not url:
            continue
        title = c.get("title", "") or "Source"
        domain = get_domain(url, title) or url
        if domain not in seen_domains:
            seen_domains.add(domain)
            unique.append((domain, url, title))
            
    if not unique:
        return ""
        
    lines = ["\n\n---\n**🔗 Nguồn thông tin:**"]
    for domain, url, title in unique:
        display_title = title if title and title.lower() != domain.lower() else domain
        lines.append(f"- [{display_title}]({url})")
    return "\n".join(lines)


async def execute_hybrid_search(
    queries: List[str],
    auth_key_prefix: str = "",
    account: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Execute Gemini grounding search in parallel and return (formatted_context, all_citations).
    
    Each search result is a structured snippet with inline source attribution.
    The returned context string is ready to inject into the system instruction.
    The citations list contains all unique sources for footer rendering.
    """
    if not queries:
        return "", []

    logger.info("Executing Google Grounding Search for queries: %s", queries)

    async def run_single_query(query: str) -> Dict[str, Any]:
        """Run one grounded search query. Returns {"query", "snippet", "citations"}."""
        if account:
            try:
                from src.core.limits.account_limiter import get_effective_limits_by_pool
                from src.core.limits import account_limiter
                estimated_tokens = len(query) // 4 + 300
                eff_rpm, eff_tpm, eff_rpd = await get_effective_limits_by_pool(account, "lite")
                effective = {**account, "rpm": eff_rpm, "tpm": eff_tpm, "rpd": eff_rpd}
                allowed, reason = await account_limiter.acquire(effective, estimated_tokens, "lite")
                if not allowed:
                    logger.warning("Account rate limit exceeded for lite pool in hybrid search: %s", reason)
                    raise RuntimeError(f"quota_exhausted: Account rate limit exceeded for lite pool in hybrid search: {reason}")
            except Exception as s_err:
                if "quota_exhausted" in str(s_err):
                    raise
                logger.warning("Failed to check rate limit for hybrid search: %s", s_err)

        for model in ["gemini-flash-lite", "gemini-flash-25-lite", "gemini-flash"]:
            try:
                logger.info("run_single_query starting with model %s for query: %s", model, query)
                current_time_str = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p")
                contents = [gt.Content(role="user", parts=[gt.Part.from_text(
                    text=(
                        f"Today is {current_time_str}. Search Google for: {query}\n\n"
                        "Return a concise factual summary in 2-4 sentences. "
                        "Include specific facts, dates, numbers, or names relevant to the query."
                    )
                )])]

                gresult = await api_manager.call_gemini(
                    model_alias=model,
                    system_instruction=(
                        "You are a factual search assistant. Provide a concise, accurate summary "
                        "of search results. Include specific facts and be direct. 2-4 sentences max."
                    ),
                    contents=contents,
                    max_tokens=400,
                    temperature=0.1,
                    web_search=True,
                    account=account
                )
                logger.info("run_single_query call_gemini returned with model %s: %s", model, query)

                response = gresult.get("response")
                snippet = (getattr(response, "text", "") or "").strip()

                # Log usage
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

                # Extract grounding citations
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

                logger.info("run_single_query finished with model %s: %s with %d citations", model, query, len(citations))
                return {"query": query, "snippet": snippet, "citations": citations}

            except Exception as e:
                err_str = str(e)
                if "quota_exhausted" in err_str:
                    logger.warning("Gemini grounding quota_exhausted for model %s, query '%s'. Falling back to DuckDuckGo.", model, query)
                    break
                logger.warning("Google Grounding search failed with model %s for query '%s': %s", model, query, e)

        # Fallback: DuckDuckGo
        try:
            logger.info("Gemini Lite grounding unavailable for query '%s'. Falling back to DuckDuckGo...", query)
            from src.tools.duckduckgo import search_with_citations
            text, cits = await search_with_citations(query)
            if text:
                logger.info("DuckDuckGo fallback successful with %d citations.", len(cits))
                return {"query": query, "snippet": text, "citations": cits}
        except Exception as ddg_err:
            logger.warning("DuckDuckGo fallback search failed for query '%s': %s", query, ddg_err)

        return {"query": query, "snippet": "", "citations": []}

    # Run up to 3 queries in parallel
    logger.info("Scheduling parallel tasks in execute_hybrid_search")
    tasks = [run_single_query(q) for q in queries[:3]]
    try:
        results = await asyncio.gather(*tasks)
    except Exception as gather_err:
        logger.warning("execute_hybrid_search gather error (degrading gracefully): %s", gather_err)
        return "", []
    logger.info("Parallel tasks finished in execute_hybrid_search")

    # Filter empty results
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

    # Format as structured context block
    search_context = _format_search_context(valid_results)
    return search_context, all_citations

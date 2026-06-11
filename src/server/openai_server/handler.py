import json
import time
import uuid
from collections import defaultdict
from typing import Any, AsyncIterator, Dict, List, Optional


from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_system as logger
from src.core.router import router
from src.core.providers import _custom_endpoint_manager, api_manager

_CUSTOM_POOL_RPM = 10
_custom_pool_usage: Dict[str, List[float]] = defaultdict(list)


async def _check_custom_pool_rate(model_id: str) -> bool:
    now = time.time()
    window = now - 60
    _custom_pool_usage[model_id] = [t for t in _custom_pool_usage[model_id] if t > window]
    if len(_custom_pool_usage[model_id]) >= _CUSTOM_POOL_RPM:
        return False
    _custom_pool_usage[model_id].append(now)
    return True


def _extract_openai_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text") or ""))
        return "\n".join(chunks)
    return str(content or "")


async def _openai_chat_completion(body: Dict[str, Any], account: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from src.core.providers.genai_types import types as gt
    import asyncio
    from src.api.claude_proxy.utils import _emergency_truncate_to_limit

    model_alias = router.resolve_model_alias(body.get("model"))
    messages = body.get("messages") or []

    # Respect explicit client-level disable override
    explicit_disable = False
    for flag in ["web_search", "search", "google_search", "grounding"]:
        if flag in body and body[flag] is False:
            explicit_disable = True
            break

    if explicit_disable:
        web_search = False
    else:
        web_search = bool(
            body.get("web_search") or 
            body.get("search") or 
            body.get("grounding") or 
            body.get("google_search") or
            config.GEMINI_AUTO_GROUNDING or
            (account and account.get("web_search_enabled"))
        )

    # Failsafe emergency truncation for OpenAI completion
    is_lite = "lite" in str(model_alias).lower() or "lite" in str(body.get("model", "")).lower()
    limit = config.LITE_EMERGENCY_MAX_INPUT_TOKENS if is_lite else config.EMERGENCY_MAX_INPUT_TOKENS
    messages = _emergency_truncate_to_limit(messages, limit)
    body["messages"] = messages

    system_chunks = []
    gemini_contents = []
    image_count = 0
    prompt_chunks = []

    for msg in messages:
        role = str(msg.get("role") or "user").lower()
        content = msg.get("content", "")

        text = _extract_openai_text(content)
        if text:
            prompt_chunks.append(text)

        if role in ("system", "developer"):
            if text:
                system_chunks.append(text)
            continue

        gemini_role = "model" if role == "assistant" else "user"
        parts = []

        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    parts.append(gt.Part.from_text(text=str(item.get("text") or "")))
                elif item.get("type") == "image_url":
                    url = item.get("image_url", {}).get("url", "")
                    import base64
                    import re
                    match = re.match(r"^data:([^;]+);base64,(.+)$", str(url), re.DOTALL)
                    if match:
                        data = base64.b64decode(match.group(2))
                        parts.append(gt.Part.from_bytes(data=data, mime_type=match.group(1)))
                        image_count += 1
                    elif str(url).startswith(("http://", "https://")):
                        try:
                            import requests
                            import mimetypes
                            response = await asyncio.to_thread(requests.get, url, timeout=12)
                            if response.status_code == 200:
                                data = response.content
                                mime_type, _ = mimetypes.guess_type(url)
                                if not mime_type or not mime_type.startswith("image/"):
                                    mime_type = "image/jpeg"
                                parts.append(gt.Part.from_bytes(data=data, mime_type=mime_type))
                                image_count += 1
                            else:
                                logger.error("Failed to fetch image from URL %s: HTTP status %d", url, response.status_code)
                        except Exception as e:
                            logger.error("Failed to fetch image from URL %s: %s", url, e)
        elif text:
            parts.append(gt.Part.from_text(text=text))

        if not parts:
            parts.append(gt.Part.from_text(text=""))
        gemini_contents.append(gt.Content(role=gemini_role, parts=parts))

    if not gemini_contents:
        gemini_contents.append(gt.Content(role="user", parts=[gt.Part.from_text(text="Tiếp tục.")]))

    system_instruction = "\n\n".join(system_chunks).strip()
    prompt_text = "\n".join(prompt_chunks)

    model_alias = router.resolve_model_alias(body.get("model"))
    model_id = router.get_model_id(model_alias)

    # ── Grounding / Web Search ──────────────────────────────────
    from src.core.providers.gemini_api_helpers import GeminiAPIHelpersMixin
    has_media = image_count > 0 or GeminiAPIHelpersMixin._has_media_or_files(gemini_contents)
    
    is_lite = "lite" in model_alias.lower() or "lite" in model_id.lower()
    can_native_ground = (
        web_search
        and not has_media
        and GeminiAPIHelpersMixin.model_supports_grounding(model_id)
    )
    native_grounding_active = can_native_ground
    
    # Hybrid search fallback: extract queries + search (only for models that don't support native grounding)
    hybrid_citations = []
    custom_messages = messages
    if web_search and not can_native_ground:
        from src.core.providers.search_manager import extract_search_queries, execute_hybrid_search
        auth_key_prefix = ""
        if account:
            ak = account.get("auth_key") or ""
            auth_key_prefix = ak[-8:] if len(ak) >= 8 else ak
        try:
            queries = await extract_search_queries(prompt_text, messages, auth_key_prefix, account=account)
        except Exception as qerr:
            logger.warning("[Grounding/OpenAI] extract_search_queries failed (%s), skipping grounding.", qerr)
            queries = []
        if queries:
            try:
                search_results, hybrid_citations = await execute_hybrid_search(queries, auth_key_prefix, account=account)
            except Exception as serr:
                logger.warning("[Grounding/OpenAI] execute_hybrid_search failed (%s), skipping grounding.", serr)
                search_results, hybrid_citations = "", []
            if search_results:
                from datetime import datetime
                current_time_str = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p")
                context_block = (
                    "\n\n[Search Context from Google Search Grounding]\n"
                    f"Current Time: {current_time_str}\n"
                    "Use the following real-time web search results to answer the user request:\n"
                    f"{search_results}\n"
                    "[End of Search Context]"
                )
                system_instruction = (system_instruction + context_block) if system_instruction else context_block.strip()

                custom_messages = []
                system_msg_found = False
                for m in messages:
                    role = m.get("role")
                    content = m.get("content")
                    if role in ("system", "developer") and not system_msg_found:
                        system_msg_found = True
                        if isinstance(content, str):
                            new_content = (content + context_block) if content else context_block.strip()
                        elif isinstance(content, list):
                            new_content = list(content) + [{"type": "text", "text": context_block}]
                        else:
                            new_content = context_block
                        custom_messages.append({**m, "content": new_content})
                    else:
                        custom_messages.append(m)
                if not system_msg_found:
                    custom_messages.insert(0, {"role": "system", "content": context_block.strip()})

    max_tokens = max(1, min(int(body.get("max_tokens") or body.get("max_completion_tokens") or 4096), config.MAX_OUTPUT_TOKENS))
    temperature = float(body.get("temperature", 0.7))
    top_p = float(body.get("top_p", 0.95))

    # ── Thinking params (raw — config built per-model inside pool) ─
    thinking_level = body.get("thinking_level")
    thinking_budget = body.get("thinking_budget")
    include_thoughts = body.get("include_thoughts")

    # ── Extra body (forward unknown params to custom endpoints) ─
    _consumed_keys = {
        "model", "messages", "stream", "max_tokens", "max_completion_tokens",
        "temperature", "top_p", "web_search", "search", "grounding", "google_search",
        "thinking_level", "thinking_budget", "include_thoughts",
    }
    extra_body = {k: v for k, v in body.items() if k not in _consumed_keys}

    # ── 1. Account-dedicated endpoint: 100% priority ──
    ep = _custom_endpoint_manager.get_endpoint_for_account(account)
    if ep and ep.get("enabled", True):
        model_to_use = body.get("model", model_alias)
        enabled_models = ep.get("enabled_models", [])
        if model_to_use not in enabled_models:
            logger.warning("[AccountEndpoint] Model %s not enabled on custom endpoint %s, trying pool fallback", model_to_use, ep["name"])
        else:
            try:
                result = await _custom_endpoint_manager._call_chat(
                    base_url=ep["base_url"],
                    auth_key=ep["auth_key"],
                    model=model_to_use,
                    messages=custom_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    stream=body.get("stream", False),
                    timeout=config.REQUEST_TIMEOUT_SECONDS,
                    extra_body=extra_body,
                )
                return {"text": result["text"], "model_alias": model_alias,
                        "finish_reason": result.get("finish_reason", "stop"),
                        "input_tokens": result.get("input_tokens", 0),
                        "output_tokens": result.get("output_tokens", 0),
                        "key_prefix": "custom"}
            except Exception as e:
                logger.warning("[AccountEndpoint] %s failed (%s), trying pool fallback", model_alias, e)

    # ── 2. Pool-assigned custom endpoints (from pool_assignments) ──
    pool_models = router.get_pool_custom_models(model_alias)

    async def _try_custom_pool() -> Optional[Dict[str, Any]]:
        if not pool_models:
            return None
        for pm in pool_models:
            ep = pm["endpoint"]
            if not ep.get("enabled", True):
                continue
            model_to_call = pm["model_id"]
            if not await _check_custom_pool_rate(model_to_call):
                logger.warning("Custom pool model %s rate limited (RPM=%d)", model_to_call, _CUSTOM_POOL_RPM)
                continue
            try:
                result = await _custom_endpoint_manager._call_chat(
                    base_url=ep["base_url"],
                    auth_key=ep["auth_key"],
                    model=model_to_call,
                    messages=custom_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    stream=body.get("stream", False),
                    timeout=config.REQUEST_TIMEOUT_SECONDS,
                    extra_body=extra_body,
                )
                return {"text": result["text"], "model_alias": model_alias,
                        "finish_reason": result.get("finish_reason", "stop"),
                        "input_tokens": result.get("input_tokens", 0),
                        "output_tokens": result.get("output_tokens", 0),
                        "key_prefix": "custom"}
            except Exception as pe:
                logger.warning("Pool custom model %s failed: %s", model_to_call, pe)
        return None

    # ── 3. Fallback: if no custom pool models, fall back to gemini-flash-lite ──
    fallback_to_lite = not pool_models
    if fallback_to_lite:
        model_alias = "gemini-flash-lite"
        model_id = router.get_model_id(model_alias)

    async def _call_gemini_pool() -> Optional[Dict[str, Any]]:
        try:
            gresult = await api_manager.call_gemini(
                model_alias=model_alias,
                system_instruction=system_instruction,
                contents=gemini_contents,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                image_count=image_count,
                account=account,
                web_search=native_grounding_active,
                thinking_level=thinking_level,
                thinking_budget=thinking_budget,
                include_thoughts=include_thoughts,
            )
            response = gresult["response"]
            text = getattr(response, "text", "") or ""
            
            # Extract thought text from candidates
            thought_text = ""
            try:
                for c in getattr(response, "candidates", None) or []:
                    for p in getattr(getattr(c, "content", None), "parts", []) or []:
                        if getattr(p, "thought", False):
                            pt = getattr(p, "text", "") or ""
                            if pt:
                                thought_text += pt
            except Exception:
                pass

            # Extract and format grounding citations if available
            try:
                candidates = getattr(response, "candidates", None) or []
                has_native_citations = False
                if candidates:
                    grounding = getattr(candidates[0], "grounding_metadata", None)
                    if grounding:
                        chunks = getattr(grounding, "grounding_chunks", []) or []
                        native_citations = []
                        for chunk in chunks:
                            web = getattr(chunk, "web", None)
                            if web:
                                title = getattr(web, "title", "") or "Source"
                                uri = getattr(web, "uri", "")
                                if uri:
                                    native_citations.append({"title": title, "url": uri})
                        if native_citations:
                            from src.core.providers.search_manager import _format_citations_footer
                            footer = _format_citations_footer(native_citations)
                            if footer:
                                text += footer
                                has_native_citations = True

                if not has_native_citations and hybrid_citations:
                    from src.core.providers.search_manager import _format_citations_footer
                    footer = _format_citations_footer(hybrid_citations)
                    if footer:
                        text += footer
            except Exception as ge:
                logger.error("Failed to parse grounding metadata: %s", ge)

            candidates = getattr(response, "candidates", None) or []
            finish_reason = "stop"
            if candidates:
                raw = str(getattr(candidates[0], "finish_reason", "") or "").lower()
                if "max" in raw:
                    finish_reason = "length"
            if not text and not candidates:
                raise RuntimeError("empty_candidate")
            used_key = gresult.get("api_key", "")
            kp = used_key[-8:] if used_key else ""
            return {
                "text": text,
                "thought": thought_text or None,
                "model_alias": model_alias,
                "finish_reason": finish_reason,
                "input_tokens": gresult.get("input_tokens", 0),
                "output_tokens": gresult.get("output_tokens", 0),
                "key_prefix": kp,
            }
        except RuntimeError as e:
            msg = str(e)
            if "no_available_key" in msg or "quota_exhausted" in msg:
                return None
            raise

    has_gemini = bool(config.GEMINI_API_KEYS)
    result = None

    # Try custom pool first (pool-assigned endpoints have priority)
    if pool_models:
        result = await _try_custom_pool()
        if not result and has_gemini:
            result = await _call_gemini_pool()
    elif has_gemini:
        result = await _call_gemini_pool()

    # Final fallback: if everything failed and we haven't already switched to lite
    if not result and not fallback_to_lite:
        logger.warning("All pool options failed, falling back to gemini-flash-lite")
        model_alias = "gemini-flash-lite"
        model_id = router.get_model_id(model_alias)
        result = await _call_gemini_pool()

    if not result:
        raise RuntimeError("pool_fallback_exhausted: all pool options failed")

    return result


def _completion_response(body: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    now = int(time.time())
    text = result["text"]
    model = body.get("model") or result["model_alias"]
    thought = result.get("thought")
    if thought:
        text = f"<think>\n{thought}\n</think>\n\n{text}"
    msg: Dict[str, Any] = {"role": "assistant", "content": text}
    if thought:
        msg["reasoning_content"] = thought
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": now,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": msg,
                "finish_reason": result.get("finish_reason") or "stop",
            }
        ],
        "usage": {
            "prompt_tokens": result.get("input_tokens", 0),
            "completion_tokens": result.get("output_tokens", 0),
            "total_tokens": (result.get("input_tokens", 0) or 0) + (result.get("output_tokens", 0) or 0),
        },
    }


async def _stream_response(body: Dict[str, Any], result: Dict[str, Any]) -> AsyncIterator[bytes]:
    cid = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    model = body.get("model") or result["model_alias"]

    # Emit thought first if present wrapped in <think> tags
    thought = result.get("thought")
    if thought:
        think_start = {
            "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"content": "<think>\n"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(think_start, ensure_ascii=False)}\n\n".encode("utf-8")

        for offset in range(0, len(thought), 900):
            chunk_text = thought[offset:offset + 900]
            thought_chunk = {
                "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": {"content": chunk_text, "reasoning_content": chunk_text}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(thought_chunk, ensure_ascii=False)}\n\n".encode("utf-8")

        think_end = {
            "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"content": "\n</think>\n\n"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(think_end, ensure_ascii=False)}\n\n".encode("utf-8")

    first = {
        "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n".encode("utf-8")

    text = result["text"]
    for offset in range(0, len(text), 900):
        chunk = {
            "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"content": text[offset:offset + 900]}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")

    done = {
        "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": result.get("finish_reason") or "stop"}],
    }
    yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"

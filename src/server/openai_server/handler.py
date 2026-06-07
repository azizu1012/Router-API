import json
import random
import time
import uuid
from collections import defaultdict
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import HTTPException

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
    import litellm
    from google.genai import types as gt
    import asyncio
    from src.api.claude_proxy.utils import _compact_conversation, should_compact, _emergency_truncate_to_limit

    model_alias = router.resolve_model_alias(body.get("model"))
    messages = body.get("messages") or []

    web_search = bool(
        body.get("web_search") or 
        body.get("search") or 
        body.get("grounding") or 
        body.get("google_search") or
        config.GEMINI_AUTO_GROUNDING or
        (account and account.get("web_search_enabled"))
    )

    # Estimate input tokens
    try:
        model_id = router.get_model_id(model_alias)
        litellm_model = f"gemini/{model_id}"
        input_tokens = await asyncio.to_thread(litellm.token_counter, model=litellm_model, messages=messages)
    except Exception:
        input_tokens = max(1, len(str(messages)) // 2)

    has_huge_msg = any(isinstance(m.get("content"), str) and len(m["content"]) > 250000 for m in messages)

    if should_compact(messages, input_tokens) or has_huge_msg:
        openai_tools = body.get("tools") or []
        compacted_messages = await _compact_conversation(body, messages, openai_tools, input_tokens)
        body["messages"] = compacted_messages
        messages = compacted_messages

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
                    import base64, re
                    match = re.match(r"^data:([^;]+);base64,(.+)$", str(url), re.DOTALL)
                    if match:
                        data = base64.b64decode(match.group(2))
                        parts.append(gt.Part.from_bytes(data=data, mime_type=match.group(1)))
                        image_count += 1
                    elif str(url).startswith(("http://", "https://")):
                        try:
                            import requests, mimetypes
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
        and api_manager.model_supports_grounding(model_id)
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

    ep = _custom_endpoint_manager.get_endpoint_for_model(model_id) if model_id != model_alias else None
    if not ep:
        ep = _custom_endpoint_manager.get_endpoint_for_model(model_alias)
    if ep and ep.get("enabled", True):
        try:
            litellm_model = f"openai/{model_id}" if model_id != model_alias else f"openai/{model_alias}"
            resp = await litellm.acompletion(
                model=litellm_model,
                messages=custom_messages,
                api_key=ep["auth_key"],
                api_base=ep["base_url"],
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stream=False,
                request_timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
            resp_usage = getattr(resp, "usage", None)
            input_tokens = getattr(resp_usage, "prompt_tokens", 0) or 0
            output_tokens = getattr(resp_usage, "completion_tokens", 0) or 0
            text = resp.choices[0].message.content if resp.choices else ""
            return {"text": text, "model_alias": model_alias, "finish_reason": "stop", "input_tokens": input_tokens, "output_tokens": output_tokens}
        except Exception as e:
            logger.error("[CustomEndpoint] %s error: %s", model_alias, e)
            raise

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
                resp = await litellm.acompletion(
                    model=f"openai/{model_to_call}",
                    messages=custom_messages,
                    api_key=ep["auth_key"],
                    api_base=ep["base_url"],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    stream=False,
                    request_timeout=config.REQUEST_TIMEOUT_SECONDS,
                )
                resp_usage = getattr(resp, "usage", None)
                input_tokens = getattr(resp_usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(resp_usage, "completion_tokens", 0) or 0
                text = resp.choices[0].message.content if resp.choices else ""
                return {"text": text, "model_alias": model_alias, "finish_reason": "stop", "input_tokens": input_tokens, "output_tokens": output_tokens}
            except Exception as pe:
                logger.warning("Pool custom model %s failed: %s", model_to_call, pe)
        return None

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
            )
            response = gresult["response"]
            text = getattr(response, "text", "") or ""
            

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

    if pool_models:
        has_gemini = bool(config.GEMINI_API_KEYS)
        try_gemini_first = has_gemini and (random.random() < 0.5 if pool_models else True)

        if try_gemini_first:
            result = await _call_gemini_pool()
            if not result:
                result = await _try_custom_pool()
        else:
            result = await _try_custom_pool()
            if not result and has_gemini:
                result = await _call_gemini_pool()
    else:
        result = await _call_gemini_pool()

    if not result:
        raise RuntimeError("pool_fallback_exhausted: all pool options failed")

    return result


def _completion_response(body: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    now = int(time.time())
    text = result["text"]
    model = body.get("model") or result["model_alias"]
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": now,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
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

import asyncio
import random
import time
from typing import Any, Dict, List, Optional

from src.core.providers.genai_types import types

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_keys as logger
from src.core.router import router
from src.core.limits import apply_error_penalty

from . import caller
from . import error as gerror
from .pool import ClientPool


def get_excluded_models(model_failures: Dict[str, int], model_alias: str) -> List[str]:
    return [mid for mid, count in model_failures.items() if count >= config.POOL_SWAP_FAILURES]


def all_models_excluded(model_failures: Dict[str, int], model_alias: str) -> bool:
    from src.core.api_config import MODEL_POOLS, is_sunset_25
    excluded = get_excluded_models(model_failures, model_alias)
    pool_cfg = MODEL_POOLS.get(model_alias)
    if pool_cfg:
        members = [m for m in pool_cfg["members"]
                   if not (is_sunset_25() and m in ("gemini-flash-25", "gemini-flash-25-lite"))]
        return all(router.get_model_id(m) in excluded or m in excluded for m in members)
    concrete = router.get_model_id(model_alias)
    return model_alias in excluded or concrete in excluded


def prepare_tools(
    tools: Optional[List[types.Tool]], model_id: str,
    image_count: int, contents: List[types.Content], web_search: bool,
) -> tuple:
    request_tools = list(tools) if tools else []
    has_files = image_count > 0 or caller.has_media_or_files(contents)
    use_grounding = web_search and ("gemini" in str(model_id).lower()) and not has_files
    if use_grounding:
        injected = caller.inject_grounding_tool(request_tools, model_id, has_files, web_search)
        if injected:
            request_tools = list(injected)
    return use_grounding, request_tools


async def wait_global_cooldown(attempt: int) -> None:
    now = time.time()
    if now < router.global_cooldown_until:
        wait = router.global_cooldown_until - now + 1.0
        logger.info("Global cooldown, waiting %.1fs (attempt %d/%d)", wait, attempt, config.MAX_RETRIES)
        await asyncio.sleep(wait)
    if attempt > 1:
        await asyncio.sleep(1.0 + random.uniform(0, 0.5))


async def backoff(attempt: int, model_failures: Dict[str, int], model_alias: str) -> None:
    backoff_sec = 2.0 ** attempt
    jitter = random.uniform(-backoff_sec * 0.3, backoff_sec * 0.3)
    backoff_sec = max(0.5, backoff_sec + jitter)
    logger.info("Backoff %.1fs (attempt %d/%d)", backoff_sec, attempt, config.MAX_RETRIES)
    await asyncio.sleep(backoff_sec)


def accumulate_parts(chunk: Any, parts: List[str]) -> None:
    if not chunk.candidates:
        return
    for candidate in chunk.candidates:
        if candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if part.text:
                    parts.append(part.text)
                elif part.function_call:
                    parts.append(f"function_call:{part.function_call.name}")


def extract_stream_usage(last_chunk: Any, full_parts: List[str]) -> tuple:
    usage = getattr(last_chunk, "usage_metadata", None)
    input_tokens = getattr(usage, "prompt_token_count", 0) or 0
    output_tokens = getattr(usage, "candidates_token_count", 0) or 0
    if output_tokens == 0 and full_parts:
        output_tokens = len("".join(full_parts)) // 4
    return input_tokens, output_tokens


async def handle_error(
    e: Exception, api_key: str, model_id: str,
    model_failures: Dict[str, int], attempt: int,
    pool: ClientPool,
) -> None:
    error_text = str(e)
    reason = gerror.classify(error_text)
    if reason == "bad_request":
        raise RuntimeError(f"bad_request: {error_text[:500]}")
    if reason == "project_denied":
        raise RuntimeError(f"project_denied: {error_text[:300]}")
    if reason == "unavailable":
        wait = config.GEMINI_UNAVAILABLE_DELAY_SEC * (attempt + 1)
        logger.warning("Key ...%s unavailable (attempt %d), wait %.1fs", api_key[-4:], attempt, wait)
        await asyncio.sleep(wait)
        return
    if reason == "permission_denied":
        router.freeze_key(api_key, config.KEY_INVALID_COOLDOWN_SECONDS, model_id, "permission_denied")
        apply_error_penalty(api_key, "permission_denied", model_id)
        logger.warning("Key ...%s PERMISSION_DENIED, frozen + penalty", api_key[-4:])
        await asyncio.sleep(0.5)
        return
    model_failures[model_id] = model_failures.get(model_id, 0) + 1
    logger.warning("Model %s failed on key ...%s (failures=%d): %s",
                   model_id, api_key[-4:], model_failures[model_id], error_text)
    if reason == "project_quota_429":
        project_id = gerror.parse_project(str(e))
        if project_id:
            pool.set_key_project(api_key, project_id)
            pool.freeze_project(project_id, model_id, float(config.GEMINI_PROJECT_FREEZE_SEC))
        else:
            from src.core.limits.gemini_rate_limiter import get_key_rpd_status
            today_count, target_rpd, is_exhausted = get_key_rpd_status(api_key, model_id)
            if is_exhausted:
                router.freeze_key(api_key, config.GEMINI_PROJECT_FREEZE_SEC, model_id, "rate_limit_rpd")
                apply_error_penalty(api_key, "rate_limit_rpd", model_id)
                router.global_cooldown_until = time.time() + config.GEMINI_GLOBAL_COOLDOWN_SEC
            else:
                router.freeze_key(api_key, config.KEY_429_COOLDOWN_SECONDS, model_id, "rate_limit")
                apply_error_penalty(api_key, "rate_limit", model_id)
        return
    router.freeze_key(api_key, config.KEY_429_COOLDOWN_SECONDS, model_id, "rate_limit")
    apply_error_penalty(api_key, "rate_limit", model_id)
    if router.record_429():
        logger.warning("[Cascade] 15 consecutive 429s detected")

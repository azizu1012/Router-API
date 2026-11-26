import asyncio
import random
from typing import Any, AsyncIterator, Dict, List, Optional

from src.core.providers.genai_types import types

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_keys as logger
from src.core.router import router
from src.core.limits import get_rate_limiter

from .pool import ClientPool
from . import caller
from . import error as gerror
from src.core.providers.gemini.thinking import resolve_thinking_config
from .utils import (
    get_excluded_models, all_models_excluded, prepare_tools,
    wait_global_cooldown, backoff, accumulate_parts, extract_stream_usage, handle_error,
)


class GeminiBadRequestError(RuntimeError):
    """Non-retryable client 400 error (must not freeze API keys)."""


_EMBED_CONFIG_KEYS = {
    "task_type": ("task_type", "taskType"),
    "title": ("title",),
    "output_dimensionality": ("output_dimensionality", "outputDimensionality"),
    "mime_type": ("mime_type", "mimeType"),
    "auto_truncate": ("auto_truncate", "autoTruncate"),
    "document_ocr": ("document_ocr", "documentOcr"),
    "audio_track_extraction": ("audio_track_extraction", "audioTrackExtraction"),
}


def _build_embed_config(raw: Optional[Dict[str, Any]]) -> Optional[types.EmbedContentConfig]:
    if not raw:
        return None
    kwargs: Dict[str, Any] = {}
    for target_key, aliases in _EMBED_CONFIG_KEYS.items():
        for src_key in aliases:
            if src_key in raw and raw[src_key] is not None:
                val = raw[src_key]
                if target_key == "output_dimensionality":
                    val = int(val)
                kwargs[target_key] = val
                break
    return types.EmbedContentConfig(**kwargs) if kwargs else None


class GeminiAPIManager:
    """Orchestrates Gemini API calls with key rotation and error handling.

    Delegates infrastructure to ``ClientPool``, SDK wrapping to
    ``caller``, and error classification to ``error``.
    """

    def __init__(self) -> None:
        self.pool = ClientPool()

    async def close_all(self) -> None:
        await self.pool.close_all()

    def refresh_pool_size(self) -> None:
        self.pool.refresh_pool_size()

    @staticmethod
    def _flatten_contents_text(contents: List[types.Content]) -> str:
        return caller.flatten_contents_text(contents)

    @staticmethod
    def _has_media_or_files(contents: List[types.Content]) -> bool:
        return caller.has_media_or_files(contents)

    # ── Public entry points ─────────────────────────────────────

    async def call_gemini(
        self,
        model_alias: str,
        system_instruction: str,
        contents: List[types.Content],
        max_tokens: int,
        temperature: float = 0.7,
        top_p: float = 0.95,
        tools: Optional[List[types.Tool]] = None,
        image_count: int = 0,
        account: Optional[Dict[str, Any]] = None,
        web_search: bool = False,
        thinking_level: Optional[str] = None,
        thinking_budget: Optional[int] = None,
        include_thoughts: Optional[bool] = None,
    ) -> Dict[str, Any]:
        prompt_text = caller.flatten_contents_text(contents)
        has_quota = await self._acquire_quota(prompt_text, model_alias, image_count)
        if not has_quota:
            raise RuntimeError("quota_exhausted")

        model_failures: Dict[str, int] = {}
        last_error: Optional[Exception] = None
        total_keys = self.pool.pool_size
        tier = self.pool.resolve_tier(account)

        for attempt in range(1, config.MAX_RETRIES + 1):
            await wait_global_cooldown(attempt)
            estimated_total = self._estimate_tokens(prompt_text, max_tokens, image_count)

            tried_keys: set = set()
            key_scan_errors: List[str] = []

            for _ in range(total_keys):
                if _ > 0:
                    await asyncio.sleep(0.3 + random.uniform(0, 0.3))

                if all_models_excluded(model_failures, model_alias):
                    logger.warning(
                        "[Quota] all_models_excluded for alias %s. failures: %s, excluded: %s",
                        model_alias, model_failures, get_excluded_models(model_failures, model_alias)
                    )
                    raise RuntimeError("quota_exhausted")

                api_key, model_id, _, reservation = await self._get_best_key(
                    model_alias, account, estimated_total,
                    get_excluded_models(model_failures, model_alias),
                )
                if not api_key:
                    break
                if api_key in tried_keys:
                    router.release_key(api_key)
                    continue
                tried_keys.add(api_key)

                if self.pool.is_key_frozen_by_project(api_key, model_id):
                    key_scan_errors.append(f"key ...{api_key[-4:]} skipped (project frozen)")
                    router.release_key(api_key)
                    continue

                try:
                    await self.pool.throttle(api_key, self.pool.get_key_last_used(api_key))
                    self.pool.record_key_usage(api_key)

                    _, request_tools = prepare_tools(tools, model_id, image_count, contents, web_search)
                    tc = resolve_thinking_config(model_id, thinking_level, thinking_budget, include_thoughts)
                    tc_obj = types.ThinkingConfig(**tc) if tc else None
                    response = await caller.generate_content(
                        self.pool, api_key, model_id, system_instruction, contents,
                        max_tokens, temperature, top_p, tools=request_tools or None,
                        tier=tier, thinking_config=tc_obj,
                    )

                    usage = getattr(response, "usage_metadata", None)
                    input_tokens = getattr(usage, "prompt_token_count", 0) or 0
                    output_tokens = getattr(usage, "candidates_token_count", 0) or 0

                    self._commit_key(api_key, model_id)
                    router.reset_429_counter()
                    router.record_success(api_key, model_id, input_tokens, output_tokens)
                    return {
                        "response": response,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "model_alias": model_alias,
                        "model_id": model_id,
                        "api_key": api_key,
                    }

                except Exception as e:
                    last_error = e
                    await handle_error(
                        e, api_key, model_id, model_failures, attempt, self.pool,
                    )
                finally:
                    router.release_key(api_key)

            if key_scan_errors:
                for msg in key_scan_errors[:3]:
                    logger.info("Key scan: %s", msg)

            if attempt < config.MAX_RETRIES:
                await backoff(attempt, model_failures, model_alias)

        raise RuntimeError(f"call_gemini_failed: {last_error or 'no keys available'}")

    async def call_gemini_json(
        self,
        model_alias: str,
        system_instruction: str,
        prompt_text: str,
        account: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        last_error = None
        model_failures: Dict[str, int] = {}
        tier = self.pool.resolve_tier(account)

        for attempt in range(1, config.MAX_RETRIES + 1):
            exclude_models = get_excluded_models(model_failures, model_alias)
            if all_models_excluded(model_failures, model_alias):
                break

            api_key, model_id, _, reservation = await self._get_best_key(
                model_alias, account, exclude_models=exclude_models,
            )
            if not api_key:
                break

            try:
                await self.pool.throttle(api_key, self.pool.get_key_last_used(api_key))
                self.pool.record_key_usage(api_key)

                response = await caller.generate_content_json(
                    self.pool, api_key, model_id, system_instruction,
                    prompt_text, tier=tier,
                )
                usage = getattr(response, "usage_metadata", None)
                self._commit_key(api_key, model_id)
                return {
                    "text": getattr(response, "text", "") or "",
                    "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
                    "output_tokens": getattr(usage, "candidates_token_count", 0) or 0,
                    "api_key": api_key,
                    "model_id": model_id,
                }
            except Exception as e:
                last_error = e
                reason = gerror.classify(str(e))
                if reason == "invalid_key":
                    router.freeze_key(api_key, config.KEY_INVALID_COOLDOWN_SECONDS, model_id, reason)
                else:
                    router.freeze_key(api_key, config.KEY_429_COOLDOWN_SECONDS, model_id, "rate_limit")
                    model_failures[model_id] = model_failures.get(model_id, 0) + 1
            finally:
                router.release_key(api_key)

        raise RuntimeError(f"call_gemini_json_failed: {last_error or 'no keys available'}")

    async def call_gemini_stream(
        self,
        model_alias: str,
        system_instruction: str,
        contents: List[types.Content],
        max_tokens: int,
        temperature: float = 0.7,
        top_p: float = 0.95,
        tools: Optional[List[types.Tool]] = None,
        image_count: int = 0,
        account: Optional[Dict[str, Any]] = None,
        web_search: bool = False,
        thinking_level: Optional[str] = None,
        thinking_budget: Optional[int] = None,
        include_thoughts: Optional[bool] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        prompt_text = caller.flatten_contents_text(contents)
        has_quota = await self._acquire_quota(prompt_text, model_alias, image_count)
        if not has_quota:
            raise RuntimeError("quota_exhausted")

        model_failures: Dict[str, int] = {}
        last_error: Optional[Exception] = None
        total_keys = self.pool.pool_size
        tier = self.pool.resolve_tier(account)

        for attempt in range(1, config.MAX_RETRIES + 1):
            await wait_global_cooldown(attempt)
            estimated_total = self._estimate_tokens(prompt_text, max_tokens, image_count)

            tried_keys: set = set()
            key_scan_errors: List[str] = []

            for _ in range(total_keys):
                if _ > 0:
                    await asyncio.sleep(0.3 + random.uniform(0, 0.3))

                if all_models_excluded(model_failures, model_alias):
                    raise RuntimeError("quota_exhausted")

                api_key, model_id, _, reservation = await self._get_best_key(
                    model_alias, account, estimated_total,
                    get_excluded_models(model_failures, model_alias),
                )
                if not api_key:
                    break
                if api_key in tried_keys:
                    router.release_key(api_key)
                    continue
                tried_keys.add(api_key)

                if self.pool.is_key_frozen_by_project(api_key, model_id):
                    key_scan_errors.append(f"key ...{api_key[-4:]} skipped (project frozen)")
                    router.release_key(api_key)
                    continue

                try:
                    await self.pool.throttle(api_key, self.pool.get_key_last_used(api_key))
                    self.pool.record_key_usage(api_key)

                    _, request_tools = prepare_tools(tools, model_id, image_count, contents, web_search)
                    tc = resolve_thinking_config(model_id, thinking_level, thinking_budget, include_thoughts)
                    tc_obj = types.ThinkingConfig(**tc) if tc else None
                    stream_gen = caller.generate_content_stream(
                        self.pool, api_key, model_id, system_instruction, contents,
                        max_tokens, temperature, top_p, tools=request_tools or None,
                        tier=tier, thinking_config=tc_obj,
                    )

                    full_parts: List[str] = []
                    chunk = None
                    saw_chunk = False
                    async for chunk in stream_gen:
                        saw_chunk = True
                        from .response_dump import dump_generation_response
                        chunk_dict = dump_generation_response(chunk)
                        yield {
                            "response_chunk": chunk_dict,
                            "model_alias": model_alias,
                            "model_id": model_id,
                            "api_key": api_key,
                        }
                        accumulate_parts(chunk, full_parts)

                    if not saw_chunk:
                        # Do not record a provider success when the SDK closes
                        # without yielding anything. The caller can then rotate
                        # to another key/model instead of returning silence.
                        raise RuntimeError("empty_stream_response")

                    input_tokens, output_tokens = extract_stream_usage(
                        chunk, full_parts,
                    )
                    self._commit_key(api_key, model_id)
                    router.reset_429_counter()
                    router.record_success(api_key, model_id, input_tokens, output_tokens)
                    return

                except Exception as e:
                    last_error = e
                    await handle_error(
                        e, api_key, model_id, model_failures, attempt, self.pool,
                    )
                finally:
                    router.release_key(api_key)

            if key_scan_errors:
                for msg in key_scan_errors[:3]:
                    logger.info("Key scan: %s", msg)

            if attempt < config.MAX_RETRIES:
                await backoff(attempt, model_failures, model_alias)

        raise RuntimeError(f"call_gemini_stream_failed: {last_error or 'no keys available'}")

    async def call_embed(
        self,
        model_alias: str,
        contents: Any,
        embed_config: Optional[Dict[str, Any]] = None,
        account: Optional[Dict[str, Any]] = None,
        estimated_tokens: int = 1,
    ) -> Dict[str, Any]:
        est = max(1, int(estimated_tokens or 1))
        has_quota = await get_rate_limiter(model_alias).acquire_quota(est)
        if not has_quota:
            raise RuntimeError("quota_exhausted")

        model_failures: Dict[str, int] = {}
        last_error: Optional[Exception] = None
        total_keys = self.pool.pool_size
        tier = self.pool.resolve_tier(account)
        cfg_obj = _build_embed_config(embed_config)

        for attempt in range(1, config.MAX_RETRIES + 1):
            await wait_global_cooldown(attempt)
            tried_keys: set = set()

            for idx in range(total_keys):
                if idx > 0:
                    await asyncio.sleep(0.3 + random.uniform(0, 0.3))

                if all_models_excluded(model_failures, model_alias):
                    raise RuntimeError("quota_exhausted")

                api_key, model_id, _, _ = await self._get_best_key(
                    model_alias, account, est,
                    get_excluded_models(model_failures, model_alias),
                )
                if not api_key:
                    break
                if api_key in tried_keys:
                    router.release_key(api_key)
                    continue
                tried_keys.add(api_key)

                if self.pool.is_key_frozen_by_project(api_key, model_id):
                    router.release_key(api_key)
                    continue

                try:
                    await self.pool.throttle(api_key, self.pool.get_key_last_used(api_key))
                    self.pool.record_key_usage(api_key)

                    response = await caller.embed_content(
                        self.pool, api_key, model_id, contents,
                        embed_config=cfg_obj, tier=tier,
                    )

                    embeddings = list(getattr(response, "embeddings", None) or [])
                    token_sum = sum(
                        int(getattr(getattr(emb, "statistics", None), "token_count", 0) or 0)
                        for emb in embeddings
                    )
                    input_tokens = token_sum if token_sum > 0 else est

                    self._commit_key(api_key, model_id)
                    router.reset_429_counter()
                    router.record_success(api_key, model_id, input_tokens, 0)
                    return {
                        "response": response,
                        "embeddings": embeddings,
                        "input_tokens": input_tokens,
                        "model_alias": model_alias,
                        "model_id": model_id,
                        "api_key": api_key,
                    }
                except Exception as e:
                    if gerror.classify(e) == "bad_request":
                        raise GeminiBadRequestError(str(e)) from e
                    last_error = e
                    await handle_error(
                        e, api_key, model_id, model_failures, attempt, self.pool,
                    )
                finally:
                    router.release_key(api_key)

            if attempt < config.MAX_RETRIES:
                await backoff(attempt, model_failures, model_alias)

        raise RuntimeError(f"call_embed_failed: {last_error or 'no keys available'}")

    async def call_decisions(
        self,
        model_alias: str,
        payload: Dict[str, Any],
        api_version: str = "v1beta",
        account: Optional[Dict[str, Any]] = None,
        estimated_tokens: int = 1,
    ) -> Dict[str, Any]:
        est = max(1, int(estimated_tokens or 1))
        has_quota = await get_rate_limiter(model_alias).acquire_quota(est)
        if not has_quota:
            raise RuntimeError("quota_exhausted")

        model_failures: Dict[str, int] = {}
        last_error: Optional[Exception] = None
        total_keys = self.pool.pool_size
        tier = self.pool.resolve_tier(account)

        for attempt in range(1, config.MAX_RETRIES + 1):
            await wait_global_cooldown(attempt)
            tried_keys: set = set()

            for idx in range(total_keys):
                if idx > 0:
                    await asyncio.sleep(0.3 + random.uniform(0, 0.3))

                if all_models_excluded(model_failures, model_alias):
                    raise RuntimeError("quota_exhausted")

                api_key, model_id, _, _ = await self._get_best_key(
                    model_alias, account, est,
                    get_excluded_models(model_failures, model_alias),
                )
                if not api_key:
                    break
                if api_key in tried_keys:
                    router.release_key(api_key)
                    continue
                tried_keys.add(api_key)

                if self.pool.is_key_frozen_by_project(api_key, model_id):
                    router.release_key(api_key)
                    continue

                req_payload = dict(payload)
                raw_model = req_payload.get("model")
                if isinstance(raw_model, str) and raw_model.startswith("models/"):
                    req_payload["model"] = f"models/{model_id}"
                elif raw_model is not None or "model" not in req_payload:
                    req_payload["model"] = model_id

                try:
                    await self.pool.throttle(api_key, self.pool.get_key_last_used(api_key))
                    self.pool.record_key_usage(api_key)

                    resp_dict = await caller.call_decisions_http(
                        api_key, req_payload, api_version=api_version, tier=tier,
                    )

                    usage_meta = resp_dict.get("usageMetadata") or resp_dict.get("usage") or {}
                    input_tokens = int(
                        usage_meta.get("promptTokenCount")
                        or usage_meta.get("prompt_tokens")
                        or est
                    )
                    output_tokens = int(
                        usage_meta.get("candidatesTokenCount")
                        or usage_meta.get("completion_tokens")
                        or 0
                    )

                    self._commit_key(api_key, model_id)
                    router.reset_429_counter()
                    router.record_success(api_key, model_id, input_tokens, output_tokens)
                    return {
                        "response": resp_dict,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "model_alias": model_alias,
                        "model_id": model_id,
                        "api_key": api_key,
                    }
                except Exception as e:
                    if gerror.classify(e) == "bad_request":
                        raise GeminiBadRequestError(str(e)) from e
                    last_error = e
                    await handle_error(
                        e, api_key, model_id, model_failures, attempt, self.pool,
                    )
                finally:
                    router.release_key(api_key)

            if attempt < config.MAX_RETRIES:
                await backoff(attempt, model_failures, model_alias)

        raise RuntimeError(f"call_decisions_failed: {last_error or 'no keys available'}")

    # ── Shared helpers ──────────────────────────────────────────

    async def _acquire_quota(self, prompt_text: str, model_alias: str, image_count: int) -> bool:
        image_tokens = image_count * 258
        reserved = max(1, len(prompt_text) // 4) + image_tokens
        return await get_rate_limiter(model_alias).acquire_quota(reserved)

    def _estimate_tokens(self, prompt_text: str, max_tokens: int, image_count: int) -> int:
        return max(1, len(prompt_text) // 4) + (image_count * 258) + max_tokens

    async def _get_best_key(self, model_alias: str, account: Optional[Dict] = None,
                            estimated_tokens: int = 0,
                            exclude_models: Optional[list] = None) -> tuple:
        reservation = router.reserve_key(model_alias, account=account,
                                          estimated_tokens=estimated_tokens,
                                          exclude_models=exclude_models)
        if not reservation:
            return None, None, model_alias, None
        return reservation["key"], reservation["model_id"], reservation["model_alias"], reservation

    def _commit_key(self, api_key: str, model_id: str) -> None:
        router.record_success(api_key, model_id)

api_manager = GeminiAPIManager()



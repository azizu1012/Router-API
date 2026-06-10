import asyncio
import random
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from google.genai import types

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_keys as logger
from src.core.router import router
from src.core.limits import apply_error_penalty, get_rate_limiter

from .pool import ClientPool
from . import caller
from . import error as gerror


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
            await self._wait_global_cooldown(attempt)
            estimated_total = self._estimate_tokens(prompt_text, max_tokens, image_count)

            tried_keys: set = set()
            key_scan_errors: List[str] = []

            for _ in range(total_keys):
                if _ > 0:
                    await asyncio.sleep(0.3 + random.uniform(0, 0.3))

                if self._all_models_excluded(model_failures, model_alias):
                    raise RuntimeError("quota_exhausted")

                api_key, model_id, _, reservation = await self._get_best_key(
                    model_alias, account, estimated_total,
                    self._get_excluded_models(model_failures, model_alias),
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

                    use_grounding, request_tools = self._prepare_tools(
                        tools, model_id, image_count, contents, web_search,
                    )
                    response = await self._call_with_grounding_fallback(
                        api_key, model_id, system_instruction, contents,
                        max_tokens, temperature, top_p, request_tools,
                        tier, use_grounding,
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
                    await self._handle_error(
                        e, api_key, model_id, model_failures, attempt,
                    )
                finally:
                    router.release_key(api_key)

            if key_scan_errors:
                for msg in key_scan_errors[:3]:
                    logger.info("Key scan: %s", msg)

            if attempt < config.MAX_RETRIES:
                await self._backoff(attempt, model_failures, model_alias)

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

        for attempt in range(1, 4):
            exclude_models = self._get_excluded_models(model_failures, model_alias)
            if self._all_models_excluded(model_failures, model_alias):
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
            await self._wait_global_cooldown(attempt)
            estimated_total = self._estimate_tokens(prompt_text, max_tokens, image_count)

            tried_keys: set = set()
            key_scan_errors: List[str] = []

            for _ in range(total_keys):
                if _ > 0:
                    await asyncio.sleep(0.3 + random.uniform(0, 0.3))

                if self._all_models_excluded(model_failures, model_alias):
                    raise RuntimeError("quota_exhausted")

                api_key, model_id, _, reservation = await self._get_best_key(
                    model_alias, account, estimated_total,
                    self._get_excluded_models(model_failures, model_alias),
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

                    use_grounding, request_tools = self._prepare_tools(
                        tools, model_id, image_count, contents, web_search,
                    )

                    try:
                        stream_gen = caller.generate_content_stream(
                            self.pool, api_key, model_id,
                            system_instruction, contents,
                            max_tokens, temperature, top_p,
                            tools=request_tools or None, tier=tier,
                        )
                    except Exception as ge_err:
                        if use_grounding and gerror.is_grounding_suppression(str(ge_err)):
                            logger.warning("Grounding stream failed on ...%s, retrying without.", api_key[-4:])
                            non_grounding = [t for t in request_tools
                                             if getattr(t, "google_search", None) is None]
                            stream_gen = caller.generate_content_stream(
                                self.pool, api_key, model_id,
                                system_instruction, contents,
                                max_tokens, temperature, top_p,
                                tools=non_grounding or None, tier=tier,
                            )
                        else:
                            raise ge_err

                    full_parts: List[str] = []
                    async for chunk in stream_gen:
                        chunk_dict = chunk.model_dump(by_alias=True, exclude_none=True)
                        yield {
                            "response_chunk": chunk_dict,
                            "model_alias": model_alias,
                            "model_id": model_id,
                            "api_key": api_key,
                        }
                        self._accumulate_parts(chunk, full_parts)

                    input_tokens, output_tokens = self._extract_stream_usage(
                        chunk, full_parts,
                    )
                    self._commit_key(api_key, model_id)
                    router.reset_429_counter()
                    router.record_success(api_key, model_id, input_tokens, output_tokens)
                    return

                except Exception as e:
                    last_error = e
                    await self._handle_error(
                        e, api_key, model_id, model_failures, attempt,
                    )
                finally:
                    router.release_key(api_key)

            if key_scan_errors:
                for msg in key_scan_errors[:3]:
                    logger.info("Key scan: %s", msg)

            if attempt < config.MAX_RETRIES:
                await self._backoff(attempt, model_failures, model_alias)

        raise RuntimeError(f"call_gemini_stream_failed: {last_error or 'no keys available'}")

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

    def _get_excluded_models(self, model_failures: Dict[str, int], model_alias: str) -> List[str]:
        return [mid for mid, count in model_failures.items() if count >= config.POOL_SWAP_FAILURES]

    def _all_models_excluded(self, model_failures: Dict[str, int], model_alias: str) -> bool:
        from src.core.api_config import MODEL_POOLS, is_sunset_25
        excluded = self._get_excluded_models(model_failures, model_alias)
        pool_cfg = MODEL_POOLS.get(model_alias)
        if pool_cfg:
            members = [m for m in pool_cfg["members"]
                       if not (is_sunset_25() and m in ("gemini-flash-25", "gemini-flash-25-lite"))]
            return all(router.get_model_id(m) in excluded or m in excluded for m in members)
        concrete = router.get_model_id(model_alias)
        return model_alias in excluded or concrete in excluded

    def _prepare_tools(
        self, tools: Optional[List[types.Tool]], model_id: str,
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

    async def _call_with_grounding_fallback(
        self, api_key, model_id, system_instruction, contents,
        max_tokens, temperature, top_p, request_tools, tier, use_grounding,
    ):
        try:
            return await caller.generate_content(
                self.pool, api_key, model_id,
                system_instruction, contents,
                max_tokens, temperature, top_p,
                tools=request_tools or None, tier=tier,
            )
        except Exception as ge_err:
            if use_grounding and gerror.is_grounding_suppression(str(ge_err)):
                logger.warning("Grounding failed on ...%s, retrying without.", api_key[-4:])
                non_grounding = [t for t in request_tools
                                 if getattr(t, "google_search", None) is None]
                return await caller.generate_content(
                    self.pool, api_key, model_id,
                    system_instruction, contents,
                    max_tokens, temperature, top_p,
                    tools=non_grounding or None, tier=tier,
                )
            raise ge_err

    async def _handle_error(
        self, e: Exception, api_key: str, model_id: str,
        model_failures: Dict[str, int], attempt: int,
    ) -> None:
        """Classify the error and apply penalty/freeze.

        Raises ``RuntimeError`` for fatal errors (bad_request, project_denied).
        Freezes keys and records penalties for transient errors, then returns.
        """
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
        logger.warning("Model %s failed on key ...%s (failures=%d)",
                       model_id, api_key[-4:], model_failures[model_id])

        if reason == "project_quota_429":
            project_id = gerror.parse_project(str(e))
            if project_id:
                self.pool.set_key_project(api_key, project_id)
                self.pool.freeze_project(project_id, model_id, float(config.GEMINI_PROJECT_FREEZE_SEC))
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

    async def _wait_global_cooldown(self, attempt: int) -> None:
        now = time.time()
        if now < router.global_cooldown_until:
            wait = router.global_cooldown_until - now + 1.0
            logger.info("Global cooldown, waiting %.1fs (attempt %d/%d)", wait, attempt, config.MAX_RETRIES)
            await asyncio.sleep(wait)
        if attempt > 1:
            await asyncio.sleep(1.0 + random.uniform(0, 0.5))

    async def _backoff(self, attempt: int, model_failures: Dict[str, int], model_alias: str) -> None:
        backoff = 2.0 ** attempt
        jitter = random.uniform(-backoff * 0.3, backoff * 0.3)
        backoff = max(0.5, backoff + jitter)
        logger.info("Backoff %.1fs (attempt %d/%d)", backoff, attempt, config.MAX_RETRIES)
        await asyncio.sleep(backoff)

    @staticmethod
    def _accumulate_parts(chunk: Any, parts: List[str]) -> None:
        if not chunk.candidates:
            return
        for candidate in chunk.candidates:
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if part.text:
                        parts.append(part.text)
                    elif part.function_call:
                        parts.append(f"function_call:{part.function_call.name}")

    @staticmethod
    def _extract_stream_usage(last_chunk: Any, full_parts: List[str]) -> tuple:
        usage = getattr(last_chunk, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        if output_tokens == 0 and full_parts:
            output_tokens = len("".join(full_parts)) // 4
        return input_tokens, output_tokens


api_manager = GeminiAPIManager()

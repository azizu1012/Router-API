import asyncio
import random
import re
import time
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_keys as logger
from src.core.router import router
from src.core.limits import get_rate_limiter, apply_error_penalty

from .gemini_api_helpers import GeminiAPIHelpersMixin


class GeminiAPIManager(GeminiAPIHelpersMixin):
    _semaphore = None

    @classmethod
    def _get_semaphore(cls) -> asyncio.Semaphore:
        if cls._semaphore is None:
            max_concurrent = config.GEMINI_API_MAX_CONCURRENT
            if max_concurrent <= 0:
                max_concurrent = max(10, len(config.GEMINI_API_KEYS) if config.GEMINI_API_KEYS else 10)
            cls._semaphore = asyncio.Semaphore(max_concurrent)
        return cls._semaphore

    def __init__(self):
        self._clients: Dict[str, Any] = {}
        self._client_lock = None
        self._max_clients = max(100, len(config.GEMINI_API_KEYS) * 2)
        self._last_api_time: float = 0.0
        self._key_last_used: Dict[str, float] = {}

        self._project_id_of_key: Dict[str, str] = {}
        self._project_frozen_until: Dict[str, float] = {}
        self._pool_size: int = len(config.GEMINI_API_KEYS) if config.GEMINI_API_KEYS else 101

    # ── Client helpers ──────────────────────────────────────────

    async def _get_client(self, api_key: str):
        if self._client_lock is None:
            self._client_lock = asyncio.Lock()
        async with self._client_lock:
            if api_key not in self._clients:
                if len(self._clients) >= self._max_clients:
                    self._clients.pop(next(iter(self._clients)))

                client = genai.Client(api_key=api_key)

                self._clients[api_key] = client
            return self._clients[api_key]

    async def close_all(self):
        async with self._client_lock:
            self._clients.clear()

    def refresh_pool_size(self):
        self._pool_size = len(config.GEMINI_API_KEYS) if config.GEMINI_API_KEYS else 10
        GeminiAPIManager._semaphore = None
        logger.info("GeminiAPIManager pool size and semaphore refreshed. Pool size: %d", self._pool_size)



    async def _generate_gemini_content(
        self, api_key: str, model_id: str, system_instruction: str,
        contents: List[types.Content], max_tokens: int,
        temperature: float, top_p: float, tools: Optional[List[types.Tool]] = None,
    ) -> Any:
        client = await self._get_client(api_key)
        async with self._get_semaphore():
            gen_config = types.GenerateContentConfig(
                system_instruction=system_instruction or None,
                temperature=temperature,
                top_p=top_p,
                max_output_tokens=max_tokens,
                safety_settings=[types.SafetySetting(**s) for s in config.SAFETY_SETTINGS] if config.SAFETY_SETTINGS else None,
                tools=tools or None,
            )
            return await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model=model_id,
                    contents=contents,
                    config=gen_config,
                ),
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
    # ── Main entry point ────────────────────────────────────────

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
        prompt_text = self._flatten_contents_text(contents)

        image_tokens = image_count * 258
        logger.info("call_gemini checking quota for model %s", model_alias)
        has_quota = await self._acquire_gemini_quota(prompt_text, model_alias, image_tokens=image_tokens)
        logger.info("call_gemini quota check result: %s", has_quota)
        if not has_quota:
            raise RuntimeError("quota_exhausted")

        model_failures: Dict[str, int] = {}
        last_error: Optional[Exception] = None
        total_keys = self._pool_size

        for attempt in range(1, config.MAX_RETRIES + 1):
            logger.info("call_gemini attempt %d/%d starting", attempt, config.MAX_RETRIES)
            now = time.time()
            if now < router.global_cooldown_until:
                wait = router.global_cooldown_until - now + 1.0
                logger.info("Global cooldown active, waiting %.1fs (attempt %d/%d)", wait, attempt, config.MAX_RETRIES)
                await asyncio.sleep(wait)

                # Pacing delay between retry attempts
                if attempt > 1:
                    pace_delay = 1.0 + random.uniform(0, 0.5)
                await asyncio.sleep(pace_delay)

            tried_keys: set = set()
            key_scan_errors: List[str] = []

            # Estimate tokens for this request to check key RPM/TPM limits (factor in 258 per image)
            reserved_tokens = max(1, len(prompt_text) // 4) + (image_count * 258)
            estimated_total_tokens = reserved_tokens + max_tokens

            logger.info("call_gemini starting key scanning for total_keys=%d", total_keys)
            for _ in range(total_keys):
                # Pacing delay between key swaps to prevent cascading 429s
                if _ > 0:
                    await asyncio.sleep(0.3 + random.uniform(0, 0.3))

                exclude_models = [mid for mid, count in model_failures.items() if count >= config.POOL_SWAP_FAILURES]
                
                from src.core.api_config import MODEL_POOLS, is_sunset_25
                pool_cfg = MODEL_POOLS.get(model_alias)
                if pool_cfg:
                    members = [m for m in pool_cfg["members"] if not (is_sunset_25() and m in ("gemini-flash-25", "gemini-flash-25-lite"))]
                    all_excluded = all(router.get_model_id(m) in exclude_models or m in exclude_models for m in members)
                else:
                    concrete_mid = router.get_model_id(model_alias)
                    all_excluded = (model_alias in exclude_models or concrete_mid in exclude_models)
                
                if all_excluded:
                    logger.warning("All members for pool/model %s are excluded. Failing early with quota_exhausted.", model_alias)
                    raise RuntimeError("quota_exhausted")

                logger.info("call_gemini calling _get_best_api_key with exclude_models=%s", exclude_models)
                api_key, model_id, _, reservation = await self._get_best_api_key(model_alias, account=account, estimated_tokens=estimated_total_tokens, exclude_models=exclude_models)
                logger.info("call_gemini _get_best_api_key returned api_key=%s", api_key[-4:] if api_key else None)
                if not api_key:
                    break

                if api_key in tried_keys:
                    router.release_key(api_key)
                    continue
                tried_keys.add(api_key)

                if self._is_key_frozen_by_project(api_key, model_id):
                    key_scan_errors.append(f"key ...{api_key[-4:]} skipped (project frozen)")
                    router.release_key(api_key)
                    continue

                last_used = self._key_last_used.get(api_key, 0.0)
                try:
                    await self._throttle_api_request(api_key, last_used)
                    self._key_last_used[api_key] = time.time()

                    client = await self._get_client(api_key)
                    logger.info("Gemini call attempt %d/%d model=%s key=...%s", attempt, config.MAX_RETRIES, model_alias, api_key[-4:])

                    request_tools = list(tools) if tools else []
                    has_files = image_count > 0 or self._has_media_or_files(contents)
                    # Check grounding against concrete model_id used for this call
                    use_grounding = web_search and self.model_supports_grounding(model_id) and not has_files

                    if use_grounding:
                        has_search = any(getattr(t, "google_search", None) is not None for t in request_tools)
                        if not has_search:
                            request_tools.append(types.Tool(google_search=types.GoogleSearch()))

                    try:
                        response = await self._generate_gemini_content(
                            api_key=api_key, model_id=model_id,
                            system_instruction=system_instruction, contents=contents,
                            max_tokens=max_tokens, temperature=temperature,
                            top_p=top_p, tools=request_tools or None,
                        )
                    except Exception as ge_err:
                        error_text = str(ge_err).lower()
                        is_grounding_error = any(
                            kw in error_text 
                            for kw in ["grounding", "google_search", "google-search", "search tool", "tool is not allowed", "tool not supported"]
                        ) or (
                            "403" in error_text and "permission" in error_text and use_grounding
                        ) or (
                            "400" in error_text and "invalid" in error_text and use_grounding
                        )
                        
                        if use_grounding and is_grounding_error:
                            logger.warning(
                                "Grounding search failed on key ...%s model=%s (Error: %s). Retrying WITHOUT grounding.",
                                api_key[-4:], model_id, ge_err
                            )
                            # Remove the google search tool and retry on the same key/model
                            non_grounding_tools = [t for t in request_tools if getattr(t, "google_search", None) is None]
                            response = await self._generate_gemini_content(
                                api_key=api_key, model_id=model_id,
                                system_instruction=system_instruction, contents=contents,
                                max_tokens=max_tokens, temperature=temperature,
                                top_p=top_p, tools=non_grounding_tools or None,
                            )
                        else:
                            raise ge_err

                    usage = getattr(response, "usage_metadata", None)
                    self._commit_selected_key(api_key, model_id)
                    router.reset_429_counter()
                    return {
                        "response": response,
                        "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
                        "output_tokens": getattr(usage, "candidates_token_count", 0) or 0,
                        "model_alias": model_alias,
                        "model_id": model_id,
                        "api_key": api_key,
                    }

                except Exception as e:
                    last_error = e
                    error_text = str(e)

                    if self._is_bad_request(error_text):
                        raise RuntimeError(f"bad_request: {error_text[:500]}")

                    if self._is_permission_denied(error_text):
                        if "denied access" in error_text.lower():
                            raise RuntimeError(f"project_denied: {error_text[:300]}")
                        router.freeze_key(api_key, config.KEY_INVALID_COOLDOWN_SECONDS, model_id, "permission_denied")
                        apply_error_penalty(api_key, "permission_denied", model_id)
                        logger.warning("Key ...%s PERMISSION_DENIED, frozen + penalty", api_key[-4:])
                        await asyncio.sleep(0.5)
                        continue

                    if self._is_invalid_key(error_text):
                        router.freeze_key(api_key, config.KEY_INVALID_COOLDOWN_SECONDS, model_id, "invalid_key")
                        logger.warning("Key ...%s INVALID, frozen", api_key[-4:])
                        await asyncio.sleep(0.5)
                        continue

                    if self._is_retryable(error_text):
                        # Track model failures for fallback swapping
                        model_failures[model_id] = model_failures.get(model_id, 0) + 1
                        logger.warning("Model %s failed on key ...%s (failures=%d)", model_id, api_key[-4:], model_failures[model_id])
                        if model_failures[model_id] >= config.POOL_SWAP_FAILURES:
                            logger.warning("Model %s hit failure limit (%d), swapping pool members", model_id, config.POOL_SWAP_FAILURES)

                        if self._is_unavailable(error_text):
                            apply_error_penalty(api_key, "unavailable", model_id)
                            logger.warning("Key ...%s unavailable (503), penalty + short delay %.1fs, rotating", api_key[-4:], config.GEMINI_UNAVAILABLE_DELAY_SEC)
                            await asyncio.sleep(config.GEMINI_UNAVAILABLE_DELAY_SEC)
                            continue

                        if self._is_project_quota_429(error_text):
                            project_id = self._parse_project(error_text)
                            if project_id:
                                self._project_id_of_key[api_key] = project_id
                                self._freeze_project(project_id, model_id, float(config.GEMINI_PROJECT_FREEZE_SEC))
                            else:
                                # We got a project quota 429 without a project ID.
                                # Let's check if the key's daily limit (RPD) is actually exhausted locally!
                                from src.core.limits.gemini_rate_limiter import get_key_rpd_status
                                today_count, target_rpd, is_exhausted = get_key_rpd_status(api_key, model_id)

                                if is_exhausted:
                                    router.freeze_key(api_key, config.GEMINI_PROJECT_FREEZE_SEC, model_id, "rate_limit_rpd")
                                    apply_error_penalty(api_key, "rate_limit_rpd", model_id)
                                    router.global_cooldown_until = time.time() + config.GEMINI_GLOBAL_COOLDOWN_SEC
                                    logger.warning("Project daily quota 429 EXHAUSTED (local %d >= RPD %d) key=...%s, frozen + penalty", today_count, target_rpd, api_key[-4:])
                                else:
                                    # Treat as temporary RPM/TPM rate limit because local RPD limit is not reached!
                                    router.freeze_key(api_key, config.KEY_429_COOLDOWN_SECONDS, model_id, "rate_limit")
                                    apply_error_penalty(api_key, "rate_limit", model_id)
                                    logger.warning("Project quota 429 TEMPORARY RPM/TPM (local %d < RPD %d) key=...%s, swapped with temporary penalty", today_count, target_rpd, api_key[-4:])
                            continue

                        router.freeze_key(api_key, config.KEY_429_COOLDOWN_SECONDS, model_id, "rate_limit")
                        apply_error_penalty(api_key, "rate_limit", model_id)
                        if router.record_429():
                            logger.warning("[Cascade] 3 consecutive 429s detected. IP Block confirmed. Global cooldown 45-90s")
                        else:
                            logger.info("[Swap] Key ...%s rate-limited. Swapping.", api_key[-4:])
                        continue

                    router.freeze_key(api_key, config.KEY_UNKNOWN_ERROR_COOLDOWN_SECONDS, model_id, "unknown_error")
                    apply_error_penalty(api_key, "unknown_error", model_id)
                    router.record_failure("error")
                    logger.warning("Key ...%s unknown error, frozen + penalty: %s", api_key[-4:], error_text[:200])
                finally:
                    router.release_key(api_key)

            if key_scan_errors:
                for msg in key_scan_errors[:3]:
                    logger.info("Key scan: %s", msg)

            if attempt < config.MAX_RETRIES:
                backoff = 2.0 ** attempt
                jitter = random.uniform(-backoff * 0.3, backoff * 0.3)
                backoff = max(0.5, backoff + jitter)
                logger.info("Retry backoff %.1fs (attempt %d/%d)", backoff, attempt, config.MAX_RETRIES)
                await asyncio.sleep(backoff)

    async def call_gemini_json(
        self,
        model_alias: str,
        system_instruction: str,
        prompt_text: str,
        account: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        last_error = None
        model_failures: Dict[str, int] = {}
        for attempt in range(1, 4):
            exclude_models = [mid for mid, count in model_failures.items() if count >= config.POOL_SWAP_FAILURES]
            
            from src.core.api_config import MODEL_POOLS, is_sunset_25
            pool_cfg = MODEL_POOLS.get(model_alias)
            if pool_cfg:
                members = [m for m in pool_cfg["members"] if not (is_sunset_25() and m in ("gemini-flash-25", "gemini-flash-25-lite"))]
                all_excluded = all(router.get_model_id(m) in exclude_models or m in exclude_models for m in members)
            else:
                concrete_mid = router.get_model_id(model_alias)
                all_excluded = (model_alias in exclude_models or concrete_mid in exclude_models)
            
            if all_excluded:
                logger.warning("All members for pool/model %s are excluded in json call. Failing early.", model_alias)
                break

            api_key, model_id, _, reservation = await self._get_best_api_key(model_alias, account=account, exclude_models=exclude_models)
            if not api_key:
                break

            try:
                last_used = self._key_last_used.get(api_key, 0.0)
                await self._throttle_api_request(api_key, last_used)
                self._key_last_used[api_key] = time.time()

                client = await self._get_client(api_key)
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.models.generate_content,
                        model=model_id,
                        contents=prompt_text,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction or None,
                            response_mime_type="application/json",
                            temperature=0.1,
                            max_output_tokens=512,
                        ),
                    ),
                    timeout=15.0,
                )
                usage = getattr(response, "usage_metadata", None)
                self._commit_selected_key(api_key, model_id)
                return {
                    "text": getattr(response, "text", "") or "",
                    "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
                    "output_tokens": getattr(usage, "candidates_token_count", 0) or 0,
                    "api_key": api_key,
                    "model_id": model_id,
                }
            except Exception as e:
                last_error = e
                error_text = str(e)
                if self._is_invalid_key(error_text):
                    router.freeze_key(api_key, config.KEY_INVALID_COOLDOWN_SECONDS, model_id, "invalid_key")
                else:
                    router.freeze_key(api_key, config.KEY_429_COOLDOWN_SECONDS, model_id, "rate_limit")
                    model_failures[model_id] = model_failures.get(model_id, 0) + 1
                    logger.warning("Model %s failed in json call on key ...%s (failures=%d)", model_id, api_key[-4:], model_failures[model_id])
            finally:
                router.release_key(api_key)

        raise RuntimeError(f"call_gemini_json_failed: {last_error or 'no keys available'}")


api_manager = GeminiAPIManager()


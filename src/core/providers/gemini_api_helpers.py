import asyncio
import random
import re
import time
from typing import Any, Dict, List, Optional
from google.genai import types

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_keys as logger
from src.core.router import router
from src.core.limits import get_rate_limiter


class GeminiAPIHelpersMixin:
    @staticmethod
    def _parse_project(text: str) -> str | None:
        m = re.search(r"project_number[=_:]\s*(\d+)", text, re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r"projects[=/:]\s*(\d+)", text, re.IGNORECASE)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _is_not_found(text: str) -> bool:
        lowered = (text or "").lower()
        return "404" in lowered or "not_found" in lowered or "not found" in lowered

    @staticmethod
    def _is_retryable(text: str) -> bool:
        lowered = (text or "").lower()
        # Không retry nếu là 404 (model không tồn tại)
        if "404" in lowered or "not_found" in lowered or "not found" in lowered:
            return False
        return any(t in lowered for t in [
            "429", "quota", "resource exhausted", "503",
            "unavailable", "overloaded", "deadline exceeded", "timeout",
        ])

    @staticmethod
    def _is_unavailable(text: str) -> bool:
        lowered = (text or "").lower()
        return any(t in lowered for t in ["503", "unavailable", "overloaded"])

    @staticmethod
    def _is_invalid_key(text: str) -> bool:
        lowered = (text or "").lower()
        return any(t in lowered for t in [
            "api key invalid", "api_key_invalid", "invalid api key",
            "401", "unauthorized", "permission denied", "api key not found",
        ])

    @staticmethod
    def _is_bad_request(text: str) -> bool:
        lowered = (text or "").lower()
        return (
            "400" in text
            or "invalid_argument" in lowered
            or "parameter" in lowered
            or "schema" in lowered
            or "tool" in lowered
        )

    @staticmethod
    def _is_permission_denied(text: str) -> bool:
        return "403" in text and "permission_denied" in text.lower()

    @staticmethod
    def _is_project_quota_429(text: str) -> bool:
        lowered = text.lower()
        return "rate_limit_exceeded" in lowered or ("quota exceeded" in lowered and ("day" in lowered or "daily" in lowered))

    def _is_invalid_key_error(self, text: str) -> bool:
        return self._is_invalid_key(text)

    def _is_rate_limit_error(self, text: str) -> bool:
        lowered = (text or "").lower()
        return any(t in lowered for t in ["429", "quota", "resource exhausted"])

    def _is_unavailable_error(self, text: str) -> bool:
        return self._is_unavailable(text)

    @staticmethod
    def _flatten_contents_text(contents: List[types.Content]) -> str:
        chunks = []
        for c in contents:
            for p in getattr(c, "parts", []) or []:
                t = getattr(p, "text", None)
                if t:
                    chunks.append(t)
        return "\n".join(chunks)

    @staticmethod
    def model_supports_grounding(model_id: str) -> bool:
        model_lower = model_id.lower()
        if "gemini" in model_lower:
            return True
        return False

    @staticmethod
    def _has_media_or_files(contents: List[types.Content]) -> bool:
        for c in contents:
            for p in getattr(c, "parts", []) or []:
                if getattr(p, "inline_data", None) is not None:
                    return True
                if getattr(p, "file_data", None) is not None:
                    return True
        return False

    def _mark_key_as_failed(self, api_key: str, model_alias: str, duration: int, reason: str, **kwargs):
        model_id = router.get_model_id(model_alias) if model_alias else None
        router.freeze_key(api_key, duration, model_id, reason)
        router.record_failure(reason)

    def _is_key_frozen_by_project(self, api_key: str, model_id: str) -> bool:
        proj = self._project_id_of_key.get(api_key)
        if not proj:
            return False
        until = self._project_frozen_until.get((proj, model_id), 0.0)
        return time.time() < until

    def _freeze_project(self, project_id: str, model_id: str, duration: float) -> None:
        until = time.time() + duration
        key = (project_id, model_id)
        old = self._project_frozen_until.get(key, 0.0)
        if until > old:
            self._project_frozen_until[key] = until
        frozen_count = sum(
            1 for k in self._project_id_of_key
            if self._project_id_of_key[k] == project_id
        )
        logger.warning(
            "Project %s quota 429 for model %s, frozen %ds (%d known keys in project)",
            project_id, model_id, duration, frozen_count,
        )

    def _commit_selected_key(self, api_key: str, model_id: str):
        router.record_success(api_key, model_id)

    async def _acquire_gemini_quota(self, prompt_text: str, model_alias: str, image_tokens: int = 0) -> bool:
        reserved_tokens = max(1, len(prompt_text) // 4) + image_tokens
        return await get_rate_limiter(model_alias).acquire_quota(reserved_tokens)

    async def _get_best_api_key(self, model_alias: str, account: Optional[Dict[str, Any]] = None, estimated_tokens: int = 0, exclude_models: Optional[list] = None):
        reservation = router.reserve_key(model_alias, account=account, estimated_tokens=estimated_tokens, exclude_models=exclude_models)
        if not reservation:
            return None, None, model_alias, None
        return reservation["key"], reservation["model_id"], reservation["model_alias"], reservation

    async def _throttle_api_request(self, api_key: str, last_used: float):
        now = time.time()

        # 1. Global throttling with jitter — spread all scanners apart
        since_last_global = now - self._last_api_time
        global_interval = config.GEMINI_API_GLOBAL_INTERVAL
        if global_interval <= 0:
            global_interval = 0.5

        jitter = random.uniform(-global_interval * 0.4, global_interval * 0.4)
        target_global_interval = max(0.2, global_interval + jitter)

        if since_last_global < target_global_interval:
            await asyncio.sleep(target_global_interval - since_last_global)
        self._last_api_time = time.time()

        # 2. Key-specific throttling — avoid hammering the same key
        now = time.time()
        key_interval = config.GEMINI_API_KEY_INTERVAL
        if key_interval <= 0:
            key_interval = 1.0

        key_jitter = random.uniform(-key_interval * 0.3, key_interval * 0.3)
        target_key_interval = max(0.3, key_interval + key_jitter)

        if now - last_used < target_key_interval:
            await asyncio.sleep(target_key_interval - (now - last_used))



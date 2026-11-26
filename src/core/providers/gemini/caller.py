import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from src.core.providers.genai_types import types

from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_system as logger
from .pool import ClientPool


class UpstreamStatusError(RuntimeError):
    """HTTP error raised by direct REST calls (e.g. Decisions API) for error classification."""

    def __init__(self, status_code: int, message: str, body: Any = None) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.body = body


def _build_safety_settings() -> Optional[List[types.SafetySetting]]:
    if not config.SAFETY_SETTINGS:
        return None
    return [types.SafetySetting(**s) for s in config.SAFETY_SETTINGS]


def _build_config(
    system_instruction: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    tools: Optional[List[types.Tool]],
    thinking_config: Optional[types.ThinkingConfig] = None,
) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=system_instruction or None,
        temperature=temperature,
        top_p=top_p,
        max_output_tokens=max_tokens,
        safety_settings=_build_safety_settings(),
        tools=tools or None,
        thinking_config=thinking_config,
    )


async def generate_content(
    pool: ClientPool,
    api_key: str,
    model_id: str,
    system_instruction: str,
    contents: List[types.Content],
    max_tokens: int,
    temperature: float,
    top_p: float,
    tools: Optional[List[types.Tool]] = None,
    tier: str = "free",
    thinking_config: Optional[types.ThinkingConfig] = None,
) -> Any:
    """Make a synchronous-style Gemini API call (non-streaming)."""
    client = await pool.get_client(api_key)
    async with ClientPool.get_semaphore(tier):
        gen_config = _build_config(system_instruction, temperature, top_p, max_tokens, tools, thinking_config)
        return await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=model_id,
                contents=contents,
                config=gen_config,
            ),
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )


async def generate_content_json(
    pool: ClientPool,
    api_key: str,
    model_id: str,
    system_instruction: str,
    prompt_text: str,
    tier: str = "free",
    timeout: float = 15.0,
) -> Any:
    """Make a JSON-mode Gemini API call (for structured output)."""
    client = await pool.get_client(api_key)
    async with ClientPool.get_semaphore(tier):
        gen_config = types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            response_mime_type="application/json",
            temperature=0.1,
            max_output_tokens=512,
        )
        return await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=model_id,
                contents=prompt_text,
                config=gen_config,
            ),
            timeout=timeout,
        )


async def generate_content_stream(
    pool: ClientPool,
    api_key: str,
    model_id: str,
    system_instruction: str,
    contents: List[types.Content],
    max_tokens: int,
    temperature: float,
    top_p: float,
    tools: Optional[List[types.Tool]] = None,
    tier: str = "free",
    thinking_config: Optional[types.ThinkingConfig] = None,
) -> AsyncIterator[Any]:
    """Make a streaming Gemini API call — yields chunks."""
    client = await pool.get_client(api_key)
    semaphore = ClientPool.get_semaphore(tier)
    gen_config = _build_config(system_instruction, temperature, top_p, max_tokens, tools, thinking_config)
    stream = None
    await semaphore.acquire()
    try:
        # Limit concurrent upstream stream creation, but never hold the shared
        # tier slot for the entire lifetime of an async stream. Holding it
        # across ``async for`` makes one slow Gemma thinking stream block every
        # other free-tier model and can make the whole server appear frozen.
        stream = await asyncio.wait_for(
            client.aio.models.generate_content_stream(
                model=model_id,
                contents=contents,
                config=gen_config,
            ),
            timeout=min(
                config.STREAM_OPEN_TIMEOUT_SECONDS,
                config.REQUEST_TIMEOUT_SECONDS,
            ),
        )
    finally:
        semaphore.release()

    try:
        async for chunk in stream:
            yield chunk
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception as e:
                logger.warning("[GeminiCaller] Error closing stream: %s", e)


async def embed_content(
    pool: ClientPool,
    api_key: str,
    model_id: str,
    contents: Any,
    embed_config: Optional[types.EmbedContentConfig] = None,
    tier: str = "free",
) -> Any:
    """Call Google GenAI embed_content via threadpool under tier semaphore."""
    client = await pool.get_client(api_key)
    async with ClientPool.get_semaphore(tier):
        return await asyncio.wait_for(
            asyncio.to_thread(
                client.models.embed_content,
                model=model_id,
                contents=contents,
                config=embed_config,
            ),
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )


async def call_decisions_http(
    api_key: str,
    payload: Dict[str, Any],
    api_version: str = "v1beta",
    tier: str = "free",
    base_url: str = "https://generativelanguage.googleapis.com",
) -> Dict[str, Any]:
    """Call Google Gemini Decisions REST API asynchronously."""
    version = api_version if api_version in ("v1beta", "v1alpha", "v1") else "v1beta"
    url = f"{base_url.rstrip('/')}/{version}/decisions"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    timeout = aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT_SECONDS)
    async with ClientPool.get_semaphore(tier):
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                raw_text = await resp.text()
                if resp.status >= 400:
                    msg = raw_text
                    parsed = None
                    try:
                        parsed = json.loads(raw_text)
                        err = parsed.get("error") if isinstance(parsed, dict) else None
                        if isinstance(err, dict) and err.get("message"):
                            msg = f"{err.get('status', '')} {err.get('message', '')}".strip()
                    except Exception:
                        pass
                    raise UpstreamStatusError(resp.status, msg or f"HTTP {resp.status}", body=parsed)
                if not raw_text:
                    return {}
                return json.loads(raw_text)


def has_media_or_files(contents: List[types.Content]) -> bool:
    for c in contents:
        for p in getattr(c, "parts", []) or []:
            if getattr(p, "inline_data", None) or getattr(p, "file_data", None):
                return True
    return False


def flatten_contents_text(contents: List[types.Content]) -> str:
    chunks: List[str] = []
    for c in contents:
        for p in getattr(c, "parts", []) or []:
            t = getattr(p, "text", None)
            if t:
                chunks.append(t)
    return "\n".join(chunks)


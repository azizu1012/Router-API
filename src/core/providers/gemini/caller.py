import asyncio
from typing import Any, AsyncIterator, List, Optional

from src.core.providers.genai_types import types

from src.core.config_n_logg import config
from .pool import ClientPool


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
    async with ClientPool.get_semaphore(tier):
        gen_config = _build_config(system_instruction, temperature, top_p, max_tokens, tools, thinking_config)
        stream = await asyncio.wait_for(
            client.aio.models.generate_content_stream(
                model=model_id,
                contents=contents,
                config=gen_config,
            ),
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        async for chunk in stream:
            yield chunk


def inject_grounding_tool(
    tools: Optional[List[types.Tool]],
    model_id: str,
    has_files: bool,
    web_search: bool,
) -> Optional[List[types.Tool]]:
    """Append a GoogleSearch tool if the model supports grounding."""
    if not web_search:
        return tools
    request_tools = list(tools) if tools else []
    has_search = any(getattr(t, "google_search", None) is not None for t in request_tools)
    if not has_search and _model_supports_grounding(model_id) and not has_files:
        request_tools.append(types.Tool(google_search=types.GoogleSearch()))
    return request_tools or None


def _model_supports_grounding(model_id: str) -> bool:
    return "gemini" in model_id.lower()


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

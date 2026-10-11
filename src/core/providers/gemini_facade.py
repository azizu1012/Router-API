"""Gemini API facade — routes via GenAI SDK (key pool) or custom endpoint (OpenAI SDK).

PATH 1 (key pool → GenAI SDK):
    acompletion(api_key, model, messages, ..., stream=False/True)
        → gemini_format.build_gemini_body()     # OpenAI → Gemini native
        → client.models.generate_content()      # GenAI SDK non-stream
        → client.aio.models.generate_content_stream()  # GenAI SDK stream
        → gemini_response.parse_gemini_*()      # Gemini → OpenAI-compatible output

PATH 2 (custom endpoint → OpenAI SDK):
    acompletion(api_key, model, messages, ..., api_base=..., stream=False/True)
        → custom_endpoint_client.call_custom_nonstream()   # OpenAI HTTP non-stream
        → custom_endpoint_client.CustomEndpointStreamGen() # OpenAI HTTP stream
"""

import asyncio
import types
from typing import Any, AsyncIterator, Dict, List, Optional

from src.core.config_n_logg.logger import logger_system as logger

_SAFETY_SETTINGS_OFF = [
    {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "OFF"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT",  "threshold": "OFF"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",  "threshold": "OFF"},
    {"category": "HARM_CATEGORY_HARASSMENT",         "threshold": "OFF"},
]


# ── GenAI SDK helpers ────────────────────────────────────────────────────────

def _extract_system_text(system_instruction: Optional[Dict]) -> Optional[str]:
    """Extract plain text from Gemini systemInstruction dict."""
    if not system_instruction:
        return None
    parts = system_instruction.get("parts", [])
    texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
    return "\n".join(texts) or None


def _gemini_tool_config(tool_choice: Any) -> Optional[Dict[str, Any]]:
    """Build Gemini's FunctionCallingConfig from the canonical OpenAI choice.

    Gemini has no "call this exact function" primitive. Its documented way to
    force one is mode ANY plus allowed_function_names holding that single name
    (cloud.google.com FunctionCallingConfig), so a forced tool becomes ANY with
    a one-entry allowlist rather than being dropped.

    Anything with a mode but no tools is ignored: a config with no declarations
    to choose from is a request the API rejects.
    """
    if tool_choice is None:
        return None

    mode: Optional[str] = None
    names: List[str] = []

    if isinstance(tool_choice, str):
        mode = {"none": "NONE", "auto": "AUTO", "required": "ANY"}.get(tool_choice)
    elif isinstance(tool_choice, dict):
        fn = tool_choice.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            mode = "ANY"
            names.append(str(fn["name"]))
        elif tool_choice.get("name"):
            mode = "ANY"
            names.append(str(tool_choice["name"]))
        else:
            ttype = tool_choice.get("type")
            if ttype in ("auto", "any", "none"):
                mode = {"auto": "AUTO", "any": "ANY", "none": "NONE"}[ttype]
            elif ttype == "required":
                mode = "ANY"
            # OpenAI's allowed_tools choice: an allowlist plus its own mode. Each
            # entry is {"type": "function", "name": ...} with the name at the top
            # level, not nested under function.
            allowed = tool_choice.get("allowed_tools")
            if isinstance(allowed, list):
                for entry in allowed:
                    if isinstance(entry, str):
                        names.append(entry)
                    elif isinstance(entry, dict) and entry.get("name"):
                        names.append(str(entry["name"]))
                declared = tool_choice.get("mode")
                mode = {"auto": "AUTO", "required": "ANY",
                        "any": "ANY", "none": "NONE"}.get(str(declared), "ANY")

    if not mode:
        return None
    cfg: Dict[str, Any] = {"mode": mode}
    if names and mode == "ANY":
        cfg["allowed_function_names"] = names
    return {"function_calling_config": cfg}


def _gemini_response_format(value: Any) -> Optional[Dict[str, Any]]:
    """Map OpenAI's response_format onto Gemini's response mime type and schema.

    A client asking for structured JSON got free-form text back and nothing in
    the log said why: response_format was read nowhere in the router.

    Gemini spells it in two pieces rather than one object, and its responseSchema
    is the restricted Schema subset rather than free JSON Schema, so the schema
    goes through the same sanitiser tool declarations get.
    """
    if not isinstance(value, dict):
        return None
    kind = value.get("type")

    if kind == "json_object":
        return {"response_mime_type": "application/json"}

    if kind in ("json_schema", "json"):
        spec = value.get("json_schema")
        # Some clients inline the schema instead of nesting it.
        if isinstance(spec, dict) and "schema" not in spec:
            spec = {"schema": spec}
        out: Dict[str, Any] = {"response_mime_type": "application/json"}
        if isinstance(spec, dict) and isinstance(spec.get("schema"), dict):
            try:
                from .gemini.schema_sanitizer import _sanitize_schema_for_gemini
                out["response_schema"] = _sanitize_schema_for_gemini(spec["schema"])
            except Exception:
                # A schema we cannot translate is better than no request at all;
                # the mime type alone still asks the model for JSON.
                logger.warning("[ResponseFormat] schema could not be sanitized")
        return out

    return None


def _build_sdk_config(
    system_instruction: Optional[Dict],
    temperature: Optional[float],
    max_output_tokens: Optional[int],
    tools: Optional[list],
    thinking_config: Optional[Dict],
    generation_config: Optional[Dict] = None,
    tool_choice: Any = None,
    response_format: Any = None,
    ) -> Dict[str, Any]:
    """Build GenerateContentConfig kwargs dict for GenAI SDK calls."""
    cfg: Dict[str, Any] = {}
    sys_text = _extract_system_text(system_instruction)
    if sys_text:
        cfg["system_instruction"] = sys_text
    if temperature is not None:
        cfg["temperature"] = temperature
    if max_output_tokens is not None:
        cfg["max_output_tokens"] = max_output_tokens
    if tools:
        cfg["tools"] = tools
    if thinking_config:
        cfg["thinking_config"] = thinking_config
    if tools:
        tool_config = _gemini_tool_config(tool_choice)
        if tool_config:
            cfg["tool_config"] = tool_config
    response = _gemini_response_format(response_format)
    if response:
        cfg.update(response)
    if generation_config:
        for k, v in generation_config.items():
            if k not in cfg:
                cfg[k] = v
    cfg["safety_settings"] = _SAFETY_SETTINGS_OFF
    return cfg


async def _sdk_nonstream(
    api_key: str,
    model_id: str,
    contents: list,
    sdk_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Call GenAI SDK non-stream. Returns native Gemini response dict."""
    from src.core.config_n_logg import config as app_config
    from src.core.providers.gemini_api_manager import api_manager
    await api_manager.pool.throttle(api_key, api_manager.pool.get_key_last_used(api_key))
    api_manager.pool.record_key_usage(api_key)
    client = await api_manager.pool.get_client(api_key)
    response = await asyncio.wait_for(
        asyncio.to_thread(
            client.models.generate_content,
            model=model_id,
            contents=contents,
            config=sdk_config,
        ),
        timeout=app_config.REQUEST_TIMEOUT_SECONDS,
    )
    candidates_raw = response.candidates or []
    candidates_out = []
    for cand in candidates_raw:
        parts_out = []
        content = cand.content
        if content:
            for part in (content.parts or []):
                part_d: Dict[str, Any] = {}
                if part.text is not None:
                    part_d["text"] = part.text
                if part.thought:
                    part_d["thought"] = True
                
                ts = getattr(part, "thought_signature", None) or getattr(part, "thoughtSignature", None)
                if ts is not None:
                    import base64
                    if isinstance(ts, bytes):
                        part_d["thought_signature"] = base64.b64encode(ts).decode("utf-8")
                    else:
                        part_d["thought_signature"] = str(ts)
                fc = part.function_call
                if fc:
                    part_d["functionCall"] = {"name": fc.name, "args": dict(fc.args or {})}
                if part_d:
                    parts_out.append(part_d)
        cand_d: Dict[str, Any] = {
            "content": {"parts": parts_out, "role": "model"},
        }
        fr = cand.finish_reason
        if fr:
            cand_d["finishReason"] = fr.name if hasattr(fr, "name") else str(fr)
        candidates_out.append(cand_d)

    usage_out: Optional[Dict[str, Any]] = None
    um = response.usage_metadata
    if um:
        usage_out = {
            "promptTokenCount": getattr(um, "prompt_token_count", 0) or 0,
            "candidatesTokenCount": getattr(um, "candidates_token_count", 0) or 0,
            "thoughtsTokenCount": getattr(um, "thoughts_token_count", 0) or 0,
            "totalTokenCount": getattr(um, "total_token_count", 0) or 0,
        }
    out: Dict[str, Any] = {"candidates": candidates_out}
    if usage_out:
        out["usageMetadata"] = usage_out
    return out


async def _sdk_stream(
    api_key: str,
    model_id: str,
    contents: list,
    sdk_config: Dict[str, Any],
) -> AsyncIterator[Dict[str, Any]]:
    """Call GenAI SDK async stream. Yields native Gemini chunk dicts."""
    from src.core.config_n_logg import config as app_config
    from src.core.providers.gemini_api_manager import api_manager
    await api_manager.pool.throttle(api_key, api_manager.pool.get_key_last_used(api_key))
    api_manager.pool.record_key_usage(api_key)
    client = await api_manager.pool.get_client(api_key)
    stream = await asyncio.wait_for(
        client.aio.models.generate_content_stream(
            model=model_id,
            contents=contents,
            config=sdk_config,
        ),
        timeout=app_config.REQUEST_TIMEOUT_SECONDS,
    )
    async for chunk in stream:
        candidates_raw = chunk.candidates or []
        candidates_out = []
        for cand in candidates_raw:
            parts_out = []
            content = cand.content
            if content:
                for part in (content.parts or []):
                    part_d: Dict[str, Any] = {}
                    if part.text is not None:
                        part_d["text"] = part.text
                    if part.thought:
                        part_d["thought"] = True
                    
                    ts = getattr(part, "thought_signature", None) or getattr(part, "thoughtSignature", None)
                    if ts is not None:
                        import base64
                        if isinstance(ts, bytes):
                            part_d["thought_signature"] = base64.b64encode(ts).decode("utf-8")
                        else:
                            part_d["thought_signature"] = str(ts)
                    fc = part.function_call
                    if fc:
                        part_d["functionCall"] = {"name": fc.name, "args": dict(fc.args or {})}
                    if part_d:
                        parts_out.append(part_d)
            cand_d: Dict[str, Any] = {
                "content": {"parts": parts_out, "role": "model"},
            }
            fr = cand.finish_reason
            if fr:
                cand_d["finishReason"] = fr.name if hasattr(fr, "name") else str(fr)
            candidates_out.append(cand_d)

        usage_out: Optional[Dict[str, Any]] = None
        um = chunk.usage_metadata
        if um:
            usage_out = {
                "promptTokenCount": getattr(um, "prompt_token_count", 0) or 0,
                "candidatesTokenCount": getattr(um, "candidates_token_count", 0) or 0,
                "thoughtsTokenCount": getattr(um, "thoughts_token_count", 0) or 0,
                "totalTokenCount": getattr(um, "total_token_count", 0) or 0,
            }

        out: Dict[str, Any] = {"candidates": candidates_out}
        if usage_out:
            out["usageMetadata"] = usage_out
        yield out


# ── Message builder ──────────────────────────────────────────────────────────

def _build_gemini_inputs(
    model_id: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    temperature: Optional[float],
    max_output_tokens: Optional[int],
    reasoning_effort: Optional[str],
    thinking_config: Optional[Dict[str, Any]],
) -> tuple:
    """Convert OpenAI messages → (contents, system_instruction, gemini_tools, tc)."""
    from .gemini_format import build_gemini_body as _build, convert_messages_to_contents
    from .gemini_thinking import build_thinking_config as _thinking

    tc = thinking_config
    if tc is None and reasoning_effort:
        is_v3 = "gemini-3" in model_id and "gemini-2" not in model_id
        tc = _thinking(thinking_level=reasoning_effort, is_v3=is_v3)

    converted = convert_messages_to_contents(messages)
    contents = converted["contents"]
    system_instruction = converted.get("systemInstruction")

    gemini_tools = None
    if tools:
        body = _build(model_id=model_id, messages=messages, tools=tools)
        gemini_tools = body.get("tools")

    if gemini_tools or any(m.get("tool_calls") for m in messages):
        tc = {}

    return contents, system_instruction, gemini_tools, tc


# ── Response wrapping ────────────────────────────────────────────────────────

class _DeltaObj:
    """Attribute + dict access wrapper (keeps stream executor compatibility)."""

    def __init__(self, data: dict):
        self._data = data

    def __getattr__(self, name: str) -> Any:
        return object.__getattribute__(self, "_data").get(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_data":
            object.__setattr__(self, name, value)
        else:
            self._data[name] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


def _make_choice(delta: dict, finish_reason: Optional[str]) -> Any:
    choice = types.SimpleNamespace()
    choice.delta = _DeltaObj(delta)
    choice.finish_reason = finish_reason
    choice.index = 0
    return choice


class _NativeChunk:
    """OpenAI-compatible chunk: .choices[0].delta.content / .reasoning_content / .tool_calls."""

    def __init__(self, delta_dict: dict):
        self.id: str = delta_dict.get("id", "")
        self.object: str = delta_dict.get("object", "chat.completion.chunk")
        self.created: int = delta_dict.get("created", 0)
        self.model: str = delta_dict.get("model", "")
        choices = delta_dict.get("choices", [])
        self.choices: list = []
        if choices:
            c = choices[0]
            self.choices = [_make_choice(c.get("delta", {}), c.get("finish_reason"))]
        self.usage: Optional[dict] = delta_dict.get("usage")


def _parse_native_chunk(chunk: dict, state: dict) -> List[dict]:
    from .gemini_response import parse_gemini_chunk as _p
    return _p(chunk, state)


def _parse_native_nonstream(response: dict, model_alias: str) -> Any:
    from .gemini_response import parse_gemini_nonstream as _p
    resp_dict = _p(response, model_alias)

    resp = types.SimpleNamespace()
    resp.id = resp_dict.get("id", "")
    resp.object = resp_dict.get("object", "chat.completion")
    resp.created = resp_dict.get("created", 0)
    resp.model = resp_dict.get("model", "")
    resp.usage = resp_dict.get("usage")

    choices = []
    for c in resp_dict.get("choices", []):
        msg_d = c.get("message", {})
        msg = types.SimpleNamespace()
        msg.content = msg_d.get("content", "")
        msg.role = "assistant"
        msg.reasoning_content = msg_d.get("reasoning_content")
        msg.thought_signature = msg_d.get("thought_signature")
        raw_tcs = msg_d.get("tool_calls", [])
        msg.tool_calls = []
        for tc in raw_tcs:
            t = types.SimpleNamespace()
            t.id = tc.get("id", "")
            t.type = tc.get("type", "function")
            t.function = types.SimpleNamespace()
            t.function.name = tc.get("function", {}).get("name", "")
            t.function.arguments = tc.get("function", {}).get("arguments", "")
            msg.tool_calls.append(t)
        choice = types.SimpleNamespace()
        choice.message = msg
        choice.finish_reason = c.get("finish_reason", "stop")
        choice.index = 0
        choices.append(choice)

    resp.choices = choices
    return resp


# ── SDK streaming wrapper ────────────────────────────────────────────────────

class _SDKStreamGen:
    """Async generator: calls GenAI SDK stream and yields _NativeChunk objects."""

    def __init__(self, api_key: str, model_id: str, contents: list, sdk_config: dict):
        self._api_key = api_key
        self._model_id = model_id
        self._contents = contents
        self._sdk_config = sdk_config
        self._state: Dict[str, Any] = {}
        self._buf: List[dict] = []
        self._gen: Optional[AsyncIterator[Dict]] = None
        self._started = False

    async def _start(self) -> None:
        self._gen = _sdk_stream(self._api_key, self._model_id, self._contents, self._sdk_config)
        self._started = True

    def __aiter__(self):
        return self

    async def __anext__(self) -> _NativeChunk:
        if self._buf:
            return _NativeChunk(self._buf.pop(0))
        if not self._started:
            await self._start()

        while True:
            gen = self._gen
            if gen is None:
                raise RuntimeError("Stream generator not initialized")
            try:
                native_chunk = await asyncio.wait_for(gen.__anext__(), timeout=30.0)
            except asyncio.TimeoutError:
                from src.core.config_n_logg.logger import logger_keys as logger
                logger.warning("[Gemini SDK Stream] Chunk read timeout (30s) reached. Closing stream.")
                raise StopAsyncIteration
            except StopAsyncIteration:
                raise

            deltas = _parse_native_chunk(native_chunk, self._state)
            if not deltas:
                continue
            self._buf.extend(deltas)
            return _NativeChunk(self._buf.pop(0))


# ── Public API ───────────────────────────────────────────────────────────────

async def acompletion(**kwargs: Any) -> Any:
    """Unified acompletion — routes to GenAI SDK or CustomEndpoint.

    PATH 1 (Gemini, key pool): no api_base → GenAI SDK
    PATH 2 (Custom endpoint):  api_base set → OpenAI HTTP
    """
    model: str = kwargs.get("model", "")
    messages: list = kwargs.get("messages", [])
    api_key: str = kwargs.get("api_key", "")
    api_base: Optional[str] = kwargs.get("api_base")
    stream: bool = kwargs.get("stream", False)
    tools: Optional[list] = kwargs.get("tools")
    temperature: Optional[float] = kwargs.get("temperature")
    max_tokens: Optional[int] = kwargs.get("max_tokens")
    reasoning_effort: Optional[str] = kwargs.get("reasoning_effort")
    thinking_config: Optional[dict] = kwargs.get("thinking_config")
    extra_body: Optional[dict] = kwargs.get("extra_body", {})
    api_format: str = kwargs.get("api_format") or "openai"
    tool_choice = kwargs.get("tool_choice")
    parallel_tool_calls = kwargs.get("parallel_tool_calls")
    response_format = kwargs.get("response_format")
    stop_sequences: Optional[list] = kwargs.get("stop_sequences")
    top_p: Optional[float] = kwargs.get("top_p")
    top_k: Optional[int] = kwargs.get("top_k")

    model_id = model.split("/")[-1] if "/" in model else model

    # ── PATH 2: Custom endpoint (OpenAI SDK format) ──────────────────────────
    if api_base:
        from .custom_endpoint_client import CustomEndpointStreamGen, call_custom_nonstream
        from .anthropic_wire import is_anthropic_format
        
        merged = dict(extra_body or {})
        is_anth = is_anthropic_format(api_format)
        
        # OpenAI-compatible endpoints spell it "stop" (string or list).
        if stop_sequences:
            merged.setdefault("stop", stop_sequences[0] if len(stop_sequences) == 1 else list(stop_sequences))
        
        thinking = kwargs.get("thinking")
        
        if is_anth:
            # Destination is Anthropic
            # Prevent reasoning_effort from leaking into Anthropic body
            merged.pop("reasoning_effort", None)
            
            if thinking:
                # Client sent Anthropic thinking, pass it verbatim.
                # We absolutely MUST NOT synthesize a thinking block from reasoning_effort
                # or thinking_config here. If we guess {"type": "enabled"} and the endpoint 
                # is Claude 4.7+, it returns 400. If we guess {"type": "adaptive"} and the 
                # endpoint is Claude <= 4.5, it returns 400. If the model is an older version 
                # that doesn't support thinking at all, it returns 400.
                merged["thinking"] = thinking
        else:
            # Destination is OpenAI-compatible
            # Prevent thinking block from leaking into OpenAI body
            merged.pop("thinking", None)
            
            if reasoning_effort:
                # Client sent OpenAI reasoning_effort natively. Pass it verbatim.
                # Do NOT synthesize reasoning_effort if the client didn't ask for it
                # because standard models like gpt-4o, vLLM, or Ollama reject it with 400.
                merged["reasoning_effort"] = reasoning_effort

        if top_k is not None and is_anth:
            # Only Anthropic (and Gemini natively) officially support top_k.
            # Standard OpenAI endpoints reject it with 400.
            merged["top_k"] = top_k
        if top_p is not None:
            merged["top_p"] = top_p
            
        extra_body = merged if merged else None

        if stream:
            return CustomEndpointStreamGen(
                api_base=api_base, api_key=api_key, model=model_id,
                messages=messages, temperature=temperature,
                max_tokens=max_tokens, tools=tools, extra_body=extra_body,
                api_format=api_format, tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
                response_format=response_format,
            )
        return await call_custom_nonstream(
            api_base=api_base, api_key=api_key, model=model_id,
            messages=messages, temperature=temperature,
            max_tokens=max_tokens, tools=tools, extra_body=extra_body,
            api_format=api_format, tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            response_format=response_format,
        )

    # ── PATH 1: Gemini (GenAI SDK via key pool) ──────────────────────────────
    contents, system_instruction, gemini_tools, processed_tc = _build_gemini_inputs(
        model_id, messages, tools, temperature, max_tokens, reasoning_effort, thinking_config
    )
    generation_config: Dict[str, Any] = {}
    if stop_sequences:
        generation_config["stop_sequences"] = list(stop_sequences)
    if top_p is not None:
        generation_config["top_p"] = top_p
    if top_k is not None:
        generation_config["top_k"] = top_k
    sdk_config = _build_sdk_config(
        system_instruction, temperature, max_tokens, gemini_tools, processed_tc,
        generation_config=generation_config or None,
        tool_choice=kwargs.get("tool_choice"),
        response_format=kwargs.get("response_format"),
    )

    if stream:
        return _SDKStreamGen(api_key, model_id, contents, sdk_config)

    resp_dict = await _sdk_nonstream(api_key, model_id, contents, sdk_config)
    return _parse_native_nonstream(resp_dict, model_id)


async def token_counter(model: str, messages: List[Dict[str, Any]]) -> int:
    """Rough token estimate (no liteLLM dependency)."""
    try:
        return max(1, len(str(messages)) // 4)
    except Exception:
        return 1


def register_models(models: List[str]) -> None:
    pass


model_list: List[str] = []

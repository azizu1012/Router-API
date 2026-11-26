"""OpenAI-compatible embeddings route (/v1/embeddings, /embeddings, /opencode/v1/embeddings)."""

import asyncio
import base64
import struct
from typing import Any, Dict, List

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse

from src.core.config.api_config import AVAILABLE_MODELS
from src.core.config_n_logg.logger import logger_api
from src.core.observability.usage_logger import log_usage
from src.core.providers import api_manager
from src.core.providers.gemini import GeminiBadRequestError
from src.core.router import router
from src.server.openai_server.auth import (
    _apply_account_limit,
    _auth_key_prefix,
    _check_auth,
    _resolve_auth,
)
from ..app_init import app


def _parse_embedding_inputs(raw_input: Any) -> List[str]:
    if isinstance(raw_input, str):
        if not raw_input:
            raise ValueError("`input` must not be empty")
        return [raw_input]
    if isinstance(raw_input, list) and raw_input:
        out: List[str] = []
        for item in raw_input:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                out.append(item["text"])
            else:
                raise ValueError("`input` must be a string or a non-empty list of strings")
        return out
    raise ValueError("`input` is required and must be a string or a non-empty list of strings")


def _encode_vector(values: List[float], encoding_format: str) -> Any:
    floats = [float(v) for v in values]
    if encoding_format == "base64":
        raw_bytes = struct.pack(f"<{len(floats)}f", *floats)
        return base64.b64encode(raw_bytes).decode("ascii")
    return floats


@app.post("/v1/embeddings")
@app.post("/embeddings")
@app.post("/opencode/v1/embeddings")
async def create_embeddings(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
):
    account = getattr(request.state, "auth_account", None)
    if account is None:
        auth = _resolve_auth(authorization, x_api_key)
        account = _check_auth(auth)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "Invalid JSON request body", "type": "invalid_request_error"}},
        )
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "Request body must be a JSON object", "type": "invalid_request_error"}},
        )

    try:
        inputs = _parse_embedding_inputs(body.get("input"))
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": str(exc), "type": "invalid_request_error"}},
        )

    encoding_format = str(body.get("encoding_format") or "float").lower()
    if encoding_format not in ("float", "base64"):
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "`encoding_format` must be 'float' or 'base64'", "type": "invalid_request_error"}},
        )

    embed_config: Dict[str, Any] = {}
    if body.get("dimensions") is not None:
        try:
            dims = int(body["dimensions"])
        except (TypeError, ValueError):
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "`dimensions` must be a positive integer", "type": "invalid_request_error"}},
            )
        if dims <= 0:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "`dimensions` must be a positive integer", "type": "invalid_request_error"}},
            )
        embed_config["output_dimensionality"] = dims

    for src_key in ("task_type", "taskType", "title", "auto_truncate", "autoTruncate", "mime_type", "mimeType"):
        if body.get(src_key) is not None:
            embed_config[src_key] = body[src_key]

    requested_model = str(body.get("model") or "gemini-embedding-2").strip() or "gemini-embedding-2"
    try:
        from src.logical_HQ_translator.model.alias import resolve_model_alias
        account_id = str(account.get("account_id") or "")
        account_key_id = account.get("token_key_id") or account.get("account_key_id")
        resolved_model, _ = resolve_model_alias(requested_model, account_id, account_key_id)
    except Exception:
        resolved_model = requested_model

    model_alias = router.resolve_model_alias(resolved_model)
    if not model_alias:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": (
                        f"Model '{resolved_model or ''}' not found. Add a model alias "
                        "for it or enable a custom endpoint that serves it."
                    ),
                    "type": "invalid_request_error",
                }
            },
        )
    cfg = AVAILABLE_MODELS.get(model_alias)
    if cfg and cfg.get("kind") == "embedding":
        limit_tokens = int(cfg.get("context_length") or 0)
        if limit_tokens > 0:
            for idx, item in enumerate(inputs):
                est_item = max(1, len(item) // 4)
                if est_item > limit_tokens:
                    return JSONResponse(
                        status_code=400,
                        content={"error": {
                            "message": f"Input item {idx} (~{est_item} tokens) exceeds embedding model context limit of {limit_tokens}.",
                            "type": "invalid_request_error",
                        }},
                    )

    estimated_tokens = max(1, sum(len(item) for item in inputs) // 4)
    is_opencode = request.url.path.startswith("/opencode/")
    await _apply_account_limit(
        account,
        {
            "model": model_alias,
            "messages": [{"role": "user", "content": "\n".join(inputs)}],
            "max_tokens": 0,
        },
        is_opencode=is_opencode,
    )

    try:
        gresult = await api_manager.call_embed(
            model_alias=model_alias,
            contents=inputs if len(inputs) > 1 else inputs[0],
            embed_config=embed_config or None,
            account=account,
            estimated_tokens=estimated_tokens,
        )
    except GeminiBadRequestError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": str(exc), "type": "invalid_request_error"}},
        )
    except HTTPException:
        raise
    except (TimeoutError, asyncio.TimeoutError):
        return JSONResponse(
            status_code=504,
            content={"error": {"message": "Upstream embedding request timed out", "type": "timeout_error"}},
        )
    except Exception as exc:
        msg = str(exc) or exc.__class__.__name__
        if "quota_exhausted" in msg or "no_available_key" in msg or "no keys available" in msg:
            return JSONResponse(
                status_code=429,
                content={"error": {"message": "All API keys are temporarily unavailable, retry later", "type": "rate_limit_error"}},
            )
        logger_api.error("[Embeddings] %s failed: %s", model_alias, msg, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": {"message": msg, "type": "api_error"}},
        )

    embeddings = gresult.get("embeddings") or []
    input_tokens = int(gresult.get("input_tokens") or estimated_tokens)
    actual_model_id = gresult.get("model_id") or gresult.get("model_alias") or model_alias
    auth_key_prefix = _auth_key_prefix(account)

    await log_usage(
        actual_model_id,
        (gresult.get("api_key") or "")[-8:],
        input_tokens,
        0,
        auth_key_prefix,
        0,
        0,
    )

    data = [
        {
            "object": "embedding",
            "index": idx,
            "embedding": _encode_vector(getattr(emb, "values", None) or [], encoding_format),
        }
        for idx, emb in enumerate(embeddings)
    ]

    return JSONResponse(
        content={
            "object": "list",
            "data": data,
            "model": requested_model,
            "usage": {
                "prompt_tokens": input_tokens,
                "total_tokens": input_tokens,
            },
        }
    )


__all__ = ["create_embeddings"]

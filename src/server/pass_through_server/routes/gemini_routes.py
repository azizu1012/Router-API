from fastapi import Header, Request

from src.server.openai_server.routes.app_init import app
from .gemini_handlers import _handle_gemini_native


@app.post("/v1beta/models/{model_id:path}:generateContent")
@app.post("/v1alpha/models/{model_id:path}:generateContent")
@app.post("/v1/models/{model_id:path}:generateContent")
async def generate_content(
    model_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_goog_api_key: str | None = Header(default=None),
):
    return await _handle_gemini_native(model_id, request, authorization, x_api_key, x_goog_api_key, stream=False)


@app.post("/v1beta/models/{model_id:path}:streamGenerateContent")
@app.post("/v1alpha/models/{model_id:path}:streamGenerateContent")
@app.post("/v1/models/{model_id:path}:streamGenerateContent")
async def stream_generate_content(
    model_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_goog_api_key: str | None = Header(default=None),
):
    return await _handle_gemini_native(model_id, request, authorization, x_api_key, x_goog_api_key, stream=True)

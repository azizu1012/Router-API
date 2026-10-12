"""Administrative custom-endpoint model probe route."""

from fastapi import Request
from fastapi.responses import JSONResponse

from src.core.providers import _custom_endpoint_manager
from ..app_init import app
@app.post("/api/ping-model")
async def ping_model(request: Request):
    # Admin only: this spends the operator's custom-endpoint quota by calling it
    # with that endpoint's own auth_key. Any logged-in tier could otherwise use it
    # as a free metered proxy.
    from ..auth_session import _require_admin
    _require_admin(request)
    from src.core.providers.gemini.facade import acompletion

    body = await request.json()
    model = body.get("model", "")
    if not model:
        return JSONResponse(status_code=400, content={"ok": False, "error": "model required"})

    eps = _custom_endpoint_manager.list_endpoints()
    target = None
    for ep in eps:
        if model in ep.get("models", []):
            target = ep
            break

    if not target:
        return JSONResponse(status_code=404, content={"ok": False, "error": f"Model '{model}' not found in any endpoint"})

    try:
        resp = await acompletion(
            model=model,
            messages=[{"role": "user", "content": "OK"}],
            api_key=target["auth_key"],
            api_base=target["base_url"],
            api_format=target.get("api_format") or "openai",
            max_tokens=5,
            temperature=0,
            stream=False,
            request_timeout=15,
        )
        text = resp.choices[0].message.content if resp.choices else ""
        usage = getattr(resp, "usage", None)
        return {
            "ok": True,
            "model": model,
            "response": (text or "").strip(),
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
        }
    except Exception as e:
        return JSONResponse(status_code=503, content={"ok": False, "error": str(e)[:500]})




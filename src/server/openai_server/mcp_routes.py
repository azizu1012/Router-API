"""MCP server exposing web search, authenticated with an existing API token.

Three things in here are not obvious, and each of them was a way to ship a
server that loads cleanly and answers nothing:

- **The sub-app's lifespan never runs.** ``app.mount(...)`` hands Starlette's
  router ``(scope, receive, send)`` and nothing else — no lifespan. The MCP SDK
  starts its session manager's task group in ``lifespan``, so without running
  that context every tool call dies with "Task group is not initialized". The
  context is entered on first request instead of at import, because a module
  import has no event loop to hold it in.

- **The path would be ``/mcp/mcp``.** ``streamable_http_app()`` defaults its own
  route to ``/mcp``; mounted under ``/mcp`` that becomes ``/mcp/mcp`` and a
  client configured for ``/mcp`` gets a 404. Passing ``streamable_http_path="/"``
  makes the mount itself the endpoint.

- **Auth is opt-in twice.** The constructor rejects ``token_verifier`` without
  ``auth``, and rejects ``auth`` without a verifier. Both are required.

The search tool calls ``execute_hybrid_search`` in-process. It does not make an
HTTP request to this same server: that would double the latency, charge the
account's quota twice, and need a second connection back into the single worker.

The account is carried in a ContextVar, which is the SDK's own mechanism for
exactly this — a tool runs in a task the request did not create, so anything
stashed on the request object is out of reach by the time it runs.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Any

logger = logging.getLogger("router.mcp")


# ── request-scoped account ──────────────────────────────────────────────────
#
# Set by the token verifier, read by the tool. A ContextVar is the only thing
# that survives the hop from the HTTP request into the tool's task; a plain
# attribute on a request object does not.

_mcp_account: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "mcp_account", default=None)

# A tool call can fan out into several engine attempts (google_grounding then
# duckduckgo under "auto"). Bounding it here means a runaway loop costs one
# search, not the account's whole daily budget — the token's rpm limits how
# often the tool is *invoked*, not what one invocation may do.
_MAX_QUERIES_PER_CALL = 3


def current_mcp_account() -> dict[str, Any] | None:
    """The account behind the MCP session currently executing, if any."""
    return _mcp_account.get()


def _allowed_hosts(resource_server_url: str) -> list[str]:
    """Host patterns the MCP transport will accept.

    The SDK compares the Host header against these as an exact string, or
    against a ``host:*`` wildcard. A bare hostname is therefore not enough: the
    header arrives as ``127.0.0.1:58100``, which does not match ``127.0.0.1``,
    and the server answers 421 for every request — an error that names neither
    the header nor the setting.

    Overridable because the production hostname is the operator's, not ours.
    """
    import os
    from urllib.parse import urlsplit

    override = os.getenv("MCP_ALLOWED_HOSTS")
    if override:
        return [h.strip() for h in override.split(",") if h.strip()]

    hosts = ["127.0.0.1:*", "localhost:*", "testserver", "testclient"]
    parsed = urlsplit(resource_server_url)
    if parsed.hostname:
        hosts += [parsed.hostname, f"{parsed.hostname}:*"]
    return hosts


def build_mcp_app(host: str = "127.0.0.1", resource_server_url: str = "http://127.0.0.1:58100"):
    """Build the MCP Starlette app. Returns None if the SDK is unavailable.

    Returns rather than raises on ImportError so the rest of the API still
    serves — but a missing SDK is a deployment mistake, not a runtime
    condition, so it is logged at error level where someone will see it.
    """
    try:
        from mcp.server.auth.provider import AccessToken
        from mcp.server.auth.settings import AuthSettings
        from mcp.server.mcpserver import MCPServer
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError as exc:
        logger.error("MCP SDK unavailable, /mcp will not serve tools: %s", exc)
        return None

    class _RouterTokenVerifier:
        """Resolve a Router API token to the account it belongs to.

        Returning None is the SDK's way of saying "reject", and it answers 401
        before the tool is ever reached — so brute force stops at the door
        instead of costing a search.
        """

        async def verify_token(self, token: str):
            from src.server.openai_server.auth import _check_auth
            from fastapi import HTTPException

            try:
                account = _check_auth(token)
            except HTTPException:
                return None
            except Exception as exc:                      # noqa: BLE001
                logger.warning("MCP token verification failed: %s", exc)
                return None

            _mcp_account.set(account)
            return AccessToken(
                token=token,
                client_id=account.get("name", ""),
                scopes=["search"],
                subject=account.get("account_id", ""),
            )

    engine = MCPServer(
        "Router API Search",
        auth=AuthSettings(
            issuer_url=resource_server_url,
            resource_server_url=resource_server_url,
            # Router tokens are opaque database keys with no audience, so
            # there is nothing for the SDK to compare against — the verifier
            # below already decided by looking the token up. Left unset, the
            # SDK warns now and starts rejecting in 3.0.
            validate_token_resource=False,
        ),
        token_verifier=_RouterTokenVerifier(),
    )

    @engine.tool(
        name="search_web",
        description="Search the web for current information, news, and facts.",
    )
    async def search_web(query: str) -> str:
        """Search the web and return formatted results with their sources."""
        account = current_mcp_account()
        if account is None:
            return "Error: no authenticated account for this session."

        from src.core.providers.search_manager import execute_hybrid_search

        queries = [query][:_MAX_QUERIES_PER_CALL]
        engine_name = str(account.get("search_engine") or "duckduckgo")
        context, citations = await execute_hybrid_search(
            queries,
            search_engine=engine_name,
            auth_key_prefix=str(account.get("auth_key", ""))[-8:],
            account=account,
        )
        if not context:
            return "No results found."
        if citations:
            lines = "\n".join(
                f"- {c.get('title', '')}: {c.get('url', '')}"
                for c in citations[:_MAX_QUERIES_PER_CALL]
            )
            return f"{context}\n\nSources:\n{lines}"
        return context

    # "/" because the mount already carries the /mcp prefix.
    mcp_app = engine.streamable_http_app(
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_allowed_hosts(resource_server_url),
        ),
        host=host,
    )
    _allow_mcp_verbs(mcp_app)
    return mcp_app


# The transport answers GET (opening the SSE stream), POST (JSON-RPC) and DELETE
# (ending a session).
_MCP_VERBS = {"GET", "POST", "DELETE"}


def _allow_mcp_verbs(mcp_app) -> None:
    """Restore the verbs the MCP SDK's route loses to a Starlette default.

    The SDK builds its transport route as ``Route(path, endpoint=asgi_app)``
    without a ``methods`` argument. Starlette used to leave that as ``None``,
    meaning "any method"; 1.2 changed the default to ``["GET"]``. A POST — which
    is every JSON-RPC call, so the entire protocol — then matches partially and
    the server answers 405 before the endpoint is reached.

    The symptom names the wrong thing completely: "Method Not Allowed" on an
    endpoint whose whole purpose is POST. Pinning starlette back would work too,
    but it moves a shared dependency for one SDK's sake; this touches the two
    objects involved and nothing else.
    """
    for route in mcp_app.routes:
        # The transport route sits at the configured path. The
        # .well-known metadata route beside it is genuinely GET-only, and it
        # has a different path, so matching on the transport path is enough.
        if getattr(route, "path", None) == "/":
            route.methods = set(_MCP_VERBS)
            return


class _McpProxy:
    """A stable ASGI target whose implementation is swapped at startup.

    The mount has to exist at import time, before the SPA's catch-all, or
    ``/mcp`` is answered by StaticFiles. But the sub-app cannot be built then:
    its session manager binds to the running event loop, and there is no loop
    during a module import.

    Nor can one instance serve two loops. ``StreamableHTTPSessionManager.run()``
    refuses to be called twice on the same object, so a second event loop — a
    second ``TestClient``, a reload — would fail on a constraint that has
    nothing to do with the request. Building a fresh sub-app per startup and
    pointing this proxy at it is the only shape that satisfies both.
    """

    def __init__(self):
        self.target: Any = None

    async def __call__(self, scope, receive, send) -> None:
        if self.target is None:
            await send({"type": "http.response.start", "status": 503,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body",
                        "body": b'{"error":{"message":"MCP server is starting.",'
                                b'"type":"api_error"}}'})
            return
        await self.target(scope, receive, send)


_proxy = _McpProxy()


async def start_mcp_session(resource_server_url: str = "http://127.0.0.1:58100") -> bool:
    """Build a session-bound MCP app and make it the mounted target.

    The lifespan runs in its own long-lived task rather than being entered and
    left dangling in the caller's. Two SDK constraints make that necessary:
    ``StreamableHTTPSessionManager.run()`` refuses to run twice on one instance,
    and anyio's task group must be entered and exited by the same task. Entering
    it from the application's startup handler and never exiting it passes the
    first check and fails the second, at loop shutdown, as "Attempted to exit
    cancel scope in a different task than it was entered in".
    """
    mcp_app = build_mcp_app(resource_server_url=resource_server_url)
    if mcp_app is None:
        return False

    context = mcp_app.router.lifespan_context
    ready = asyncio.Event()

    async def _hold_open() -> None:
        async with context(mcp_app):
            ready.set()
            await asyncio.Event().wait()      # until this task is cancelled

    task = asyncio.create_task(_hold_open())
    _session_tasks.append(task)
    await ready.wait()
    _proxy.target = mcp_app
    logger.info("MCP session started at /mcp")
    return True


_session_tasks: list = []


async def stop_mcp_session() -> None:
    for task in _session_tasks:
        task.cancel()
    _session_tasks.clear()
    _proxy.target = None


def mount_mcp(app) -> bool:
    """Attach the MCP proxy to ``app``. Returns whether it was mounted.

    Called from the router package import, before the SPA's catch-all mount, so
    that ``/mcp`` is claimed by the MCP server rather than by StaticFiles — the
    same catch-all that made ``POST /v1/messages/`` answer 405.
    """
    app.mount("/mcp", _proxy)
    return True
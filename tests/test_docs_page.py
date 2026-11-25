"""`/docs` must render under the app's own CSP.

FastAPI's stock docs pages load Swagger UI from cdn.jsdelivr.net and bootstrap
with an inline <script>. The app sends `script-src 'self'` on every response, so
both pages rendered blank — the shell arrived, nothing executed. The replacement
in routes/docs_routes.py renders the same /openapi.json from one same-origin
script, which is what these tests pin down:

* the page contains no inline script and no third-party origin;
* the CSP is not loosened to make it work (it still forbids `script-src 'unsafe-inline'`);
* the routes exist and are registered before the catch-all StaticFiles mount,
  otherwise the SPA shell would answer /docs instead.
"""

import re

import pytest

from src.server.openai_server.routes.app_init import app
from src.server.openai_server.routes import docs_routes
from src.server.openai_server.security import add_security_headers


class _Resp:
    def __init__(self):
        self.headers = {}


# ── route wiring ──────────────────────────────────────────────────────────────

def test_docs_routes_are_registered_before_the_static_mount():
    paths = [getattr(r, "path", None) for r in app.routes]
    mount_at = next(i for i, r in enumerate(app.routes) if r.__class__.__name__ == "Mount")
    for path in ("/docs", "/docs.js", "/redoc"):
        assert path in paths, f"{path} missing from the route table"
        assert paths.index(path) < mount_at, (
            f"{path} is registered after the StaticFiles mount and would be "
            "answered by the SPA shell"
        )


def test_openapi_json_survives_the_docs_override():
    """The page is only useful if the spec it fetches still exists."""
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/openapi.json" in paths


def test_fastapi_stock_docs_are_off():
    """The stock /docs would have won had it stayed enabled — it is registered first."""
    assert app.docs_url is None
    assert app.redoc_url is None


# ── page contract ─────────────────────────────────────────────────────────────

PAGE = docs_routes._PAGE
SCRIPT = docs_routes._SCRIPT


@pytest.mark.parametrize("page", [PAGE])
def test_page_has_no_inline_script(page):
    """`script-src 'self'` blocks inline script; one would silently blank the page."""
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", page, re.S | re.I)
    assert scripts, "the page has no script tag at all"
    for attrs, body in scripts:
        assert "src=" in attrs, f"inline <script> found: {attrs.strip()!r}"
        assert not body.strip(), "script tag carries an inline body"


@pytest.mark.parametrize("page", [PAGE])
def test_page_loads_nothing_from_a_third_party(page):
    """Same-origin only: any CDN host here would be blocked by default-src 'self'."""
    lowered = page.lower()
    for host in ("jsdelivr", "unpkg", "cdnjs", "googleapis", "gstatic"):
        assert host not in lowered, f"{host} referenced by the docs page"
    for tag in re.findall(r"<(?:script|link|img)\b[^>]*>", page, re.I):
        for url in re.findall(r'(?:src|href)="([^"]+)"', tag):
            assert url.startswith("/") or url.startswith("#"), (
                f"non-origin asset in docs page: {url}"
            )


def test_page_has_no_data_uri_assets():
    """`default-src 'self'` refuses `url(data:...)`; the replacement draws icons in CSS."""
    assert "url(data:" not in PAGE.replace(" ", "")
    assert "url( data:" not in PAGE


def test_routes_accept_head():
    """Probes and link checkers often HEAD a page before fetching it."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    for url in ("/docs", "/docs.js", "/redoc"):
        r = client.head(url)
        assert r.status_code == 200, f"HEAD {url} -> {r.status_code}"


def test_script_fetches_the_spec_same_origin():
    assert "fetch('/openapi.json'" in SCRIPT


def test_the_script_builds_text_nodes_not_inner_html():
    """The spec is app-owned, but rendering it should never hand text to the HTML parser."""
    assert ".innerHTML" not in SCRIPT
    assert ".textContent" in SCRIPT


# ── the CSP itself ────────────────────────────────────────────────────────────

def test_csp_still_forbids_inline_script():
    """The fix must not have been 'allow unsafe-inline'."""
    resp = _Resp()
    add_security_headers(resp)
    csp = resp.headers["content-security-policy"]
    directives = dict(
        (part.split(" ", 1)[0], part.split(" ", 1)[1] if " " in part else "")
        for part in (p.strip() for p in csp.split(";"))
        if part
    )
    assert "'unsafe-inline'" not in directives["script-src"], csp
    assert "'self'" in directives["script-src"], csp
    assert directives["default-src"].strip() == "'self'", csp
    assert "cdn.jsdelivr.net" not in csp


def test_csp_keeps_the_other_hardening_headers():
    resp = _Resp()
    add_security_headers(resp)
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
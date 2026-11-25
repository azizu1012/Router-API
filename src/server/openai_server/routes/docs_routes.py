"""Self-hosted API reference at /docs and /redoc.

FastAPI's stock Swagger UI and ReDoc pages load their JS/CSS from
cdn.jsdelivr.net and run an inline bootstrap script. The app's CSP
(`script-src 'self'`, `style-src 'self' 'unsafe-inline'`) blocks both, so both
pages rendered blank. Swagger UI also references `data:` images, which
`default-src 'self'` refuses.

Rather than loosening the CSP for every response in the app, this module
renders the reference from the same-origin `/openapi.json` with one external
script and no third-party assets. The CSP therefore stays exactly as strict as
it was; only the docs page itself changed.

FastAPI is constructed with `docs_url=None, redoc_url=None` in `app_init.py` so
these routes are the ones that answer, not FastAPI's built-in ones (a route
registered earlier wins, and FastAPI registers its own during `__init__`).
"""

from fastapi.responses import HTMLResponse

from .app_init import app

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Router API — API reference</title>
<style>
:root{--bg:#fbfbfd;--fg:#1c1d21;--muted:#6b6f76;--line:#e3e5ea;--card:#fff;
--get:#0b6bcb;--post:#0f8a52;--put:#a8620a;--patch:#7b52c9;--delete:#c0392b;
--chip:#eef1f6;--accent:#2f5fe0}
@media (prefers-color-scheme:dark){:root{--bg:#15171c;--fg:#e8eaee;--muted:#9aa0aa;
--line:#282c34;--card:#1c1f26;--chip:#232732;--accent:#7aa2ff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);
padding:14px 20px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;margin:0;font-weight:650}
header .ver{font-size:12px;color:var(--muted)}
header .links{margin-left:auto;font-size:12px;display:flex;gap:12px}
a{color:var(--accent);text-decoration:none}
header input{flex:1 1 220px;min-width:180px;padding:7px 10px;border:1px solid var(--line);
border-radius:8px;background:var(--card);color:inherit;font:inherit}
main{display:grid;grid-template-columns:220px minmax(0,1fr);gap:28px;
max-width:1180px;margin:0 auto;padding:22px 20px 80px}
nav{position:sticky;top:74px;align-self:start;max-height:calc(100vh - 100px);overflow:auto}
nav a{display:block;padding:5px 8px;border-radius:6px;color:var(--muted);font-size:13px}
nav a:hover{background:var(--chip);color:var(--fg)}
section{margin-bottom:34px}
section>h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
margin:0 0 10px;font-weight:600}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:8px;
overflow:hidden}
details[open]{border-color:var(--accent)}
summary{list-style:none;cursor:pointer;display:flex;gap:10px;align-items:baseline;
padding:9px 12px}
summary::-webkit-details-marker{display:none}
.m{font:600 11px/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
text-transform:uppercase;padding:4px 7px;border-radius:5px;color:#fff;flex:0 0 auto;
letter-spacing:.03em}
.get{background:var(--get)}.post{background:var(--post)}.put{background:var(--put)}
.patch{background:var(--patch)}.delete{background:var(--delete)}
.head,.options,.trace{background:#59606d}
.p{font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;word-break:break-all}
.s{color:var(--muted);font-size:12.5px;margin-left:auto;text-align:right;flex:0 1 auto}
.body{padding:2px 14px 14px;border-top:1px solid var(--line);margin-top:2px}
.body h3{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
margin:14px 0 6px;font-weight:600}
.body p{margin:8px 0;white-space:pre-wrap}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}
code{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
background:var(--chip);padding:1px 4px;border-radius:4px}
#status{padding:40px 20px;color:var(--muted)}
@media (max-width:820px){main{grid-template-columns:1fr}nav{position:static;max-height:none}}
</style>
</head>
<body>
<header>
  <h1 id="title">API reference</h1><span class="ver" id="ver"></span>
  <input id="q" type="search" placeholder="Filter paths (e.g. /v1/messages, dashboard)" aria-label="Filter endpoints">
  <span class="links"><a href="/openapi.json">openapi.json</a><a href="/stats">Dashboard</a></span>
</header>
<div id="status">Loading specification…</div>
<main id="main" hidden><nav id="nav" aria-label="Sections"></nav><div id="content"></div></main>
<script src="/docs.js"></script>
</body>
</html>
"""

_SCRIPT = """/** Render the API reference from the same-origin OpenAPI document.
 *  All text is inserted with textContent/createElement: the spec is app-owned,
 *  but building DOM that way keeps a stray `<` in a description inert. */
(function () {
  'use strict';
  var STATUS = document.getElementById('status');
  var MAIN = document.getElementById('main');
  var NAV = document.getElementById('nav');
  var CONTENT = document.getElementById('content');
  var Q = document.getElementById('q');

  var METHODS = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace'];

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function refName(ref) {
    return String(ref || '').split('/').pop();
  }
  function groupOf(path) {
    var seg = path.split('/').filter(Boolean)[0];
    return seg ? '/' + seg : '(root)';
  }

  function paramTable(params) {
    var t = el('table');
    var hr = el('tr');
    ['Name', 'In', 'Type', 'Required', 'Description'].forEach(function (h) {
      hr.appendChild(el('th', null, h));
    });
    t.appendChild(hr);
    params.forEach(function (p) {
      var tr = el('tr');
      tr.appendChild(el('td', null, p.name || ''));
      tr.appendChild(el('td', null, p.in || ''));
      var type = p.schema && (p.schema.type || refName(p.schema.$ref)) || '';
      tr.appendChild(el('td', null, type));
      tr.appendChild(el('td', null, p.required ? 'yes' : ''));
      tr.appendChild(el('td', null, p.description || ''));
      t.appendChild(tr);
    });
    return t;
  }

  function describeBody(body) {
    var box = el('div');
    Object.keys(body.content || {}).forEach(function (ct) {
      var media = body.content[ct] || {};
      var schema = media.schema || {};
      var line = el('p');
      line.appendChild(el('code', null, ct));
      var name = schema.$ref ? refName(schema.$ref)
        : (schema.type === 'array' && schema.items && schema.items.$ref)
          ? refName(schema.items.$ref) + '[]' : (schema.type || 'object');
      line.appendChild(document.createTextNode(' → '));
      line.appendChild(el('code', null, name));
      box.appendChild(line);
    });
    if (body.required) box.appendChild(el('p', null, 'Required'));
    return box;
  }

  function responseTable(responses) {
    var t = el('table');
    var hr = el('tr');
    ['Status', 'Description'].forEach(function (h) { hr.appendChild(el('th', null, h)); });
    t.appendChild(hr);
    Object.keys(responses || {}).forEach(function (code) {
      var r = responses[code] || {};
      var tr = el('tr');
      tr.appendChild(el('td', null, code));
      tr.appendChild(el('td', null, r.description || ''));
      t.appendChild(tr);
    });
    return t;
  }

  var sections = [];

  function renderRow(op, method, path) {
    var d = el('details');
    d.dataset.search = (method + ' ' + path + ' ' + (op.summary || '') + ' ' + groupOf(path)).toLowerCase();
    var sum = el('summary');
    sum.appendChild(el('span', 'm ' + method, method));
    sum.appendChild(el('span', 'p', path));
    if (op.summary) sum.appendChild(el('span', 's', op.summary));
    d.appendChild(sum);

    var b = el('div', 'body');
    if (op.description) b.appendChild(el('p', null, op.description));
    if (op.operationId) b.appendChild(el('p', null, 'operationId: ' + op.operationId));

    var params = (op.parameters || []).slice();
    if (params.length) {
      b.appendChild(el('h3', null, 'Parameters'));
      b.appendChild(paramTable(params));
    }
    if (op.requestBody) {
      b.appendChild(el('h3', null, 'Request body'));
      b.appendChild(describeBody(op.requestBody));
    }
    b.appendChild(el('h3', null, 'Responses'));
    b.appendChild(responseTable(op.responses));
    d.appendChild(b);
    return d;
  }

  function filter(term) {
    var q = term.trim().toLowerCase();
    sections.forEach(function (sec) {
      var visible = 0;
      sec.rows.forEach(function (d) {
        var hit = !q || d.dataset.search.indexOf(q) !== -1;
        d.hidden = !hit;
        if (hit) visible++;
      });
      sec.wrap.hidden = visible === 0;
      sec.link.hidden = visible === 0;
    });
  }

  function render(spec) {
    document.getElementById('title').textContent = (spec.info && spec.info.title) || 'API reference';
    document.getElementById('ver').textContent = (spec.info && spec.info.version) || '';
    if (document.title.indexOf('—') === -1) document.title += ' — ' + ((spec.info && spec.info.title) || '');
    if (spec.info && spec.info.description) {
      var intro = el('p', 's');
      intro.style.margin = '0 0 22px';
      intro.textContent = spec.info.description;
      CONTENT.appendChild(intro);
    }

    var order = [];
    var groups = {};
    Object.keys(spec.paths || {}).forEach(function (path) {
      var ops = spec.paths[path] || {};
      METHODS.forEach(function (method) {
        var op = ops[method];
        if (!op) return;
        var g = groupOf(path);
        if (!groups[g]) { groups[g] = { rows: [], wrap: null, link: null }; order.push(g); }
        groups[g].rows.push(renderRow(op, method, path));
      });
    });

    order.forEach(function (g, i) {
      var sec = groups[g];
      var wrap = el('section');
      wrap.id = 'g' + i;
      wrap.appendChild(el('h2', null, g));
      sec.rows.forEach(function (d) { wrap.appendChild(d); });
      CONTENT.appendChild(wrap);
      sec.wrap = wrap;

      var link = el('a', null, g + ' (' + sec.rows.length + ')');
      link.href = '#' + wrap.id;
      NAV.appendChild(link);
      sec.link = link;
      sections.push(sec);
    });

    STATUS.hidden = true;
    MAIN.hidden = false;
    if (!order.length) CONTENT.appendChild(el('p', null, 'No endpoints in the specification.'));
  }

  fetch('/openapi.json', { headers: { accept: 'application/json' } })
    .then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(render)
    .catch(function (e) {
      STATUS.textContent = 'Could not load openapi.json: ' + e.message;
    });

  Q.addEventListener('input', function () { filter(Q.value); });
})();
"""


@app.api_route("/docs", methods=["GET", "HEAD"], include_in_schema=False)
async def api_docs() -> HTMLResponse:
    """Serve the self-hosted API reference (CSP-safe, no CDN)."""
    return HTMLResponse(_PAGE)


@app.api_route("/docs.js", methods=["GET", "HEAD"], include_in_schema=False)
async def api_docs_script() -> HTMLResponse:
    """Bootstrap script for /docs — external so `script-src 'self'` allows it."""
    return HTMLResponse(_SCRIPT, media_type="application/javascript")


@app.api_route("/redoc", methods=["GET", "HEAD"], include_in_schema=False)
async def api_docs_legacy() -> HTMLResponse:
    """ReDoc used the same blocked CDN assets; send visitors to /docs instead."""
    return HTMLResponse(_PAGE)
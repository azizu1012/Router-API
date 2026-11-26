"""A hand-written catalog of the endpoints, grouped by the dialect they speak.

FastAPI can generate a schema for all 75 routes, but this app's handlers take
`request: Request` and call `await request.json()` rather than declaring Pydantic
models, so the generated spec contains no request bodies at all. /openapi.json
lists that /v1/messages exists and shows you two nullable string headers. That
answers the wrong question.

So this module states the request shape by hand, with a runnable curl per
endpoint. The examples are written against a placeholder token and against the
origin the dashboard itself was loaded from — host, port and scheme included —
so an install behind nginx on https://api.example.com gets examples pointing at
https://api.example.com instead of a 127.0.0.1 the reader cannot reach.

Every human-readable string is a `{lang: text}` map. The dictionary is a flat
`{key: {lang: text}}` shape for one reason: a missing translation must be visible
in review, and a per-language catalog would let a group drift out of sync silently.

Kept as data rather than JSX so it can be asserted on. If a route is renamed or
dropped, the test that compares these paths against the app's own route table
fails, which is the only way this stays honest.
"""

import json
import re
from typing import Any, Dict, List, Optional

TOKEN = "<your-token>"          # sk-<account>-<6 chars>, from the dashboard
KEY = "<your-api-key>"          # the Gemini key, only for native Gemini routes

DEFAULT_ORIGIN = "http://127.0.0.1:58100"
LANGS = ("vi", "en", "ja")
SAMPLE = {"vi": "Xin chào", "en": "Hello", "ja": "こんにちは"}

# A host header reaches us from the client, and its value ends up inside a
# shell-quoted curl example. Anything outside this shape is dropped rather than
# escaped: an origin is not a place to be clever.
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]*)(:\d{1,5})?$")


def pick(value: Any, lang: str) -> Any:
    """Resolve a `{lang: text}` map, falling back to Vietnamese."""
    if isinstance(value, dict):
        return value.get(lang) or value.get("vi") or ""
    return value


def public_origin(request: Any) -> str:
    """The origin the reader's browser is actually on.

    Behind nginx/Caddy the app sees `Host: 127.0.0.1:58100` unless the proxy
    forwards the original. `X-Forwarded-Host` / `X-Forwarded-Proto` carry it, so
    we prefer those, validate them against a hostname shape, and fall back to
    the request's own netloc before the built-in default.
    """
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()

    if proto not in ("http", "https"):
        proto = request.url.scheme

    if not _HOST_RE.match(host or ""):
        host = (request.headers.get("host") or "").strip()
    if not _HOST_RE.match(host or ""):
        host = request.url.netloc

    if not host:
        return DEFAULT_ORIGIN
    return f"{proto or 'http'}://{host}"


SAMPLE_INSTRUCTIONS = {
    "vi": "Trả lời ngắn gọn.",
    "en": "Answer briefly.",
    "ja": "簡潔に答えてください。",
}


def _fill(body: Any, lang: str) -> Any:
    """Replace the language markers so the example reads in the UI language."""
    if isinstance(body, dict):
        return {k: _fill(v, lang) for k, v in body.items()}
    if isinstance(body, list):
        return [_fill(v, lang) for v in body]
    if body == "__sample__":
        return SAMPLE[lang]
    if body == "__instructions__":
        return SAMPLE_INSTRUCTIONS[lang]
    return body


def _curl(
    method: str,
    path: str,
    origin: str,
    body: Optional[Dict[str, Any]] = None,
    auth: str = "token",
) -> str:
    """One runnable curl. `auth` is token | none.

    The URL is always single-quoted: a Gemini example carries `?key=<your-api-key>`
    and an unquoted `<` would be a shell redirect, not a placeholder.
    """
    parts = [f"curl -X {method} '{origin}{path}'"]
    if auth == "token":
        parts.append(f'-H "Authorization: Bearer {TOKEN}"')
    if body is not None:
        parts.append('-H "Content-Type: application/json"')
        payload = json.dumps(body, ensure_ascii=False)
        parts.append(f"-d '{payload}'")
    if len(parts) == 1:
        return parts[0]
    return " \\\n  ".join(parts)


def build_catalog(origin: str = DEFAULT_ORIGIN, lang: str = "vi") -> List[Dict[str, Any]]:
    """Resolve the catalog for one origin and one language."""
    if lang not in LANGS:
        lang = "vi"
    groups: List[Dict[str, Any]] = []

    for group in CATALOG:
        endpoints = []
        for ep in group["endpoints"]:
            path = ep.get("example_path") or ep["path"]
            auth = ep.get("auth", "token")
            built: Dict[str, Any] = {
                "method": ep["method"],
                "path": ep["path"],
                "aliases": list(ep.get("aliases", [])),
                "note": pick(ep["note"], lang),
                "curl": _curl(ep["method"], path, origin,
                              _fill(ep["request"], lang) if ep.get("request") else None, auth),
            }
            if ep.get("stream_request") is not None:
                built["curl_stream"] = _curl(ep["method"], path, origin,
                                             _fill(ep["stream_request"], lang), auth)
            if ep.get("search_request") is not None:
                built["curl_search"] = _curl(ep["method"], path, origin,
                                             _fill(ep["search_request"], lang), auth)
            endpoints.append(built)
        groups.append({
            "id": group["id"],
            "group": pick(group["group"], lang),
            "summary": pick(group["summary"], lang),
            "endpoints": endpoints,
        })

    return groups


def build_notes(lang: str = "vi") -> List[Dict[str, str]]:
    if lang not in LANGS:
        lang = "vi"
    return [{"title": pick(n["title"], lang), "body": pick(n["body"], lang)} for n in NOTES]


CATALOG: List[Dict[str, Any]] = [
    {
        "group": {
            "vi": "Anthropic Messages API",
            "en": "Anthropic Messages API",
            "ja": "Anthropic Messages API",
        },
        "id": "anthropic",
        "summary": {
            "vi": "Claude Code và mọi client nói chuẩn Anthropic. Cùng một endpoint "
                  "cho cả stream và non-stream.",
            "en": "Claude Code and every client that speaks the Anthropic format. One "
                  "endpoint serves both stream and non-stream.",
            "ja": "Claude Code など Anthropic 形式のクライアント向け。ストリームと"
                  "非ストリームを同じエンドポイントで扱います。",
        },
        "endpoints": [
            {
                "method": "POST",
                "path": "/v1/messages",
                "aliases": ["/messages"],
                "note": {
                    "vi": "\"stream\": true trả SSE theo chuẩn Anthropic. "
                          "\"tools\" dùng input_schema kiểu Anthropic. "
                          "\"output_config\": {\"effort\": \"high\"} điều khiển mức suy nghĩ.",
                    "en": "\"stream\": true returns SSE in the Anthropic format. "
                          "\"tools\" takes an Anthropic-style input_schema. "
                          "\"output_config\": {\"effort\": \"high\"} sets how hard the model thinks.",
                    "ja": "\"stream\": true で Anthropic 形式の SSE を返します。"
                          "\"tools\" は Anthropic 形式の input_schema を使います。"
                          "\"output_config\": {\"effort\": \"high\"} で思考量を指定できます。",
                },
                "request": {"model": "gemini-flash", "max_tokens": 1024,
                            "messages": [{"role": "user", "content": "__sample__"}]},
                "stream_request": {"model": "gemini-flash", "max_tokens": 1024, "stream": True,
                                   "messages": [{"role": "user", "content": "__sample__"}]},
            },
            {
                "method": "POST",
                "path": "/v1/messages/count_tokens",
                "note": {
                    "vi": "Ước lượng token theo chuẩn Anthropic, không gọi provider.",
                    "en": "Estimates tokens in the Anthropic format without calling the provider.",
                    "ja": "プロバイダを呼ばずに Anthropic 形式でトークン数を見積もります。",
                },
                "request": {"model": "gemini-flash",
                            "messages": [{"role": "user", "content": "__sample__"}]},
            },
        ],
    },
    {
        "group": {
            "vi": "OpenAI Chat Completions",
            "en": "OpenAI Chat Completions",
            "ja": "OpenAI Chat Completions",
        },
        "id": "openai-chat",
        "summary": {
            "vi": "OpenAI SDK, Cline, Roo Code, và bất kỳ client nói chuẩn OpenAI.",
            "en": "OpenAI SDK, Cline, Roo Code, and any client that speaks the OpenAI format.",
            "ja": "OpenAI SDK、Cline、Roo Code など OpenAI 形式のクライアント向け。",
        },
        "endpoints": [
            {
                "method": "POST",
                "path": "/v1/chat/completions",
                "note": {
                    "vi": "\"tools\" dùng parameters kiểu OpenAI. "
                          "\"reasoning_effort\": low|medium|high được map sang mức nghĩ của Gemini.",
                    "en": "\"tools\" takes OpenAI-style parameters. "
                          "\"reasoning_effort\": low|medium|high maps onto Gemini's thinking budget.",
                    "ja": "\"tools\" は OpenAI 形式の parameters を使います。"
                          "\"reasoning_effort\": low|medium|high は Gemini の思考量に対応します。",
                },
                "request": {"model": "gemini-flash",
                            "messages": [{"role": "user", "content": "__sample__"}]},
            },
            {
                "method": "POST",
                "path": "/opencode/v1/chat/completions",
                "aliases": [],
                "note": {
                    "vi": "Bản OpenCode: trả tool_calls dạng OpenCode và giữ tên model khớp opencode.json.",
                    "en": "The OpenCode flavour: tool_calls come back in OpenCode's shape and model "
                          "names match opencode.json.",
                    "ja": "OpenCode 向け: tool_calls を OpenCode 形式で返し、モデル名は opencode.json と一致します。",
                },
                "request": {"model": "gemini-flash",
                            "messages": [{"role": "user", "content": "__sample__"}]},
            },
            {
                "method": "GET",
                "path": "/v1/models",
                "note": {
                    "vi": "Schema superset: vừa đúng kiểu OpenAI vừa đúng kiểu Anthropic.",
                    "en": "A schema superset: valid as OpenAI and as Anthropic at the same time.",
                    "ja": "スキーマの上位互換: OpenAI 形式としても Anthropic 形式としても有効です。",
                },
            },
        ],
    },
    {
        "group": {
            "vi": "OpenAI Responses API",
            "en": "OpenAI Responses API",
            "ja": "OpenAI Responses API",
        },
        "id": "openai-responses",
        "summary": {
            "vi": "Endpoint kiểu Responses. Đây là chỗ \"có respond\" — và là chỗ "
                  "mapping chứ không phải chuyển tiếp trong suốt.",
            "en": "The Responses-style endpoint. This is where a response object is assembled, "
                  "and where the router maps rather than passes through.",
            "ja": "Responses 形式のエンドポイント。レスポンスを組み立てる場所であり、"
                  "そのまま中継せずマッピングする場所です。",
        },
        "endpoints": [
            {
                "method": "POST",
                "path": "/v1/responses",
                "note": {
                    "vi": "tools dùng kiểu Responses. Gửi "
                          "\"tools\":[{\"type\":\"web_search\"}] để bật tìm kiếm server-side: "
                          "router chạy tìm kiếm Router-owned với trích dẫn, nên client "
                          "không phải tự vòng lặp. "
                          "\"instructions\" thành system message, \"reasoning\":{\"effort\"} "
                          "thành reasoning_effort, \"stream\": true trả SSE.",
                    "en": "tools take the Responses shape. Send "
                          "\"tools\":[{\"type\":\"web_search\"}] to enable server-side search: "
                          "the router runs its own search with citations, so the client does not "
                          "loop. \"instructions\" becomes a system message, \"reasoning\":{\"effort\"} "
                          "becomes reasoning_effort, \"stream\": true returns SSE.",
                    "ja": "tools は Responses 形式です。"
                          "\"tools\":[{\"type\":\"web_search\"}] を送るとサーバー側検索が有効になり、"
                          "ルーターが引用付きで検索を実行するためクライアント側のループは不要です。"
                          "\"instructions\" は system メッセージに、\"reasoning\":{\"effort\"} は "
                          "reasoning_effort に、\"stream\": true は SSE になります。",
                },
                "request": {"model": "gemini-flash",
                            "instructions": "__instructions__",
                            "input": "__sample__"},
                "search_request": {"model": "gemini-flash",
                                   "input": "__sample__",
                                   "tools": [{"type": "web_search"}]},
            },
        ],
    },
    {
        "group": {
            "vi": "Gemini native (pass-through)",
            "en": "Gemini native (pass-through)",
            "ja": "Gemini native（パススルー）",
        },
        "id": "gemini-native",
        "summary": {
            "vi": "Giữ nguyên kiểu Gemini, dùng key Gemini trực tiếp thay vì token router. "
                  "Không quay vòng key, không đổi model.",
            "en": "Keeps the Gemini shape and takes a Gemini key directly instead of a router "
                  "token. No key rotation, no model remapping.",
            "ja": "Gemini 形式のまま、ルータートークンではなく Gemini キーを直接使います。"
                  "キーのローテーションもモデルの変換も行いません。",
        },
        "endpoints": [
            {
                "method": "POST",
                "path": "/v1beta/models/{model_id}:generateContent",
                "aliases": ["/v1/models/{model_id}:generateContent",
                            "/v1alpha/models/{model_id}:generateContent"],
                "example_path": "/v1beta/models/gemini-3.6-flash:generateContent?key=<your-api-key>",
                "auth": "none",
                "note": {
                    "vi": "Pass-through. Nhận token qua query ?key= hoặc header x-goog-api-key. "
                          "Ba version v1, v1beta, v1alpha đều trỏ cùng một handler.",
                    "en": "Pass-through. Takes the key via ?key= or the x-goog-api-key header. "
                          "The v1, v1beta and v1alpha paths all hit the same handler.",
                    "ja": "パススルー。キーは ?key= または x-goog-api-key ヘッダーで渡します。"
                          "v1 / v1beta / v1alpha は同じハンドラーに接続されます。",
                },
                "request": {"contents": [{"role": "user", "parts": [{"text": "__sample__"}]}]},
            },
            {
                "method": "POST",
                "path": "/v1beta/models/{model_id}:streamGenerateContent",
                "aliases": ["/v1/models/{model_id}:streamGenerateContent",
                            "/v1alpha/models/{model_id}:streamGenerateContent"],
                "example_path": "/v1beta/models/gemini-3.6-flash:streamGenerateContent?key=<your-api-key>",
                "auth": "none",
                "note": {
                    "vi": "Bản streaming của endpoint trên.",
                    "en": "The streaming form of the endpoint above.",
                    "ja": "上のエンドポイントのストリーミング版です。",
                },
                "request": {"contents": [{"role": "user", "parts": [{"text": "__sample__"}]}]},
            },
            {
                "method": "POST",
                "path": "/v1beta/models/{model_id}:embedContent",
                "aliases": ["/v1/models/{model_id}:embedContent",
                            "/v1alpha/models/{model_id}:embedContent"],
                "example_path": "/v1beta/models/gemini-embedding-2:embedContent",
                "note": {
                    "vi": "Embedding chuẩn Gemini native (output_dimensionality, task_type). "
                          "Hỗ trợ gemini-embedding-2 và gemini-embedding-001.",
                    "en": "Gemini-native single embedding (output_dimensionality, task_type). "
                          "Supports gemini-embedding-2 and gemini-embedding-001.",
                    "ja": "Gemini ネイティブの単一埋め込み (output_dimensionality, task_type)。"
                          "gemini-embedding-2 と gemini-embedding-001 に対応しています。",
                },
                "request": {"content": {"parts": [{"text": "__sample__"}]}},
            },
            {
                "method": "POST",
                "path": "/v1beta/models/{model_id}:batchEmbedContents",
                "aliases": ["/v1/models/{model_id}:batchEmbedContents",
                            "/v1alpha/models/{model_id}:batchEmbedContents"],
                "example_path": "/v1beta/models/gemini-embedding-2:batchEmbedContents",
                "note": {
                    "vi": "Embedding theo lô chuẩn Gemini native qua mảng requests.",
                    "en": "Gemini-native batch embeddings via a requests array.",
                    "ja": "requests 配列による Gemini ネイティブのバッチ埋め込みです。",
                },
                "request": {"requests": [
                    {"model": "models/gemini-embedding-2",
                     "content": {"parts": [{"text": "__sample__"}]}}
                ]},
            },
        ],
    },
    {
        "group": {
            "vi": "Embeddings & Decisions",
            "en": "Embeddings & Decisions",
            "ja": "Embeddings & Decisions",
        },
        "id": "embeddings-decisions",
        "summary": {
            "vi": "Vector embedding chuẩn OpenAI và Gemini Decisions API dùng chung xoay vòng key và hạn mức.",
            "en": "OpenAI-compatible embeddings and the Gemini Decisions API, backed by the same key rotation and quotas.",
            "ja": "OpenAI 互換の埋め込みと Gemini Decisions API。同じキーローテーションとクォータ制御で動作します。",
        },
        "endpoints": [
            {
                "method": "POST",
                "path": "/v1/embeddings",
                "aliases": ["/embeddings", "/opencode/v1/embeddings"],
                "note": {
                    "vi": "Hỗ trợ input dạng chuỗi hoặc mảng chuỗi, \"dimensions\", "
                          "\"encoding_format\" (float|base64), và \"task_type\". "
                          "Model: gemini-embedding-2 (8192 tok), gemini-embedding-001 (2048 tok).",
                    "en": "Accepts a string or list of strings for input, optional \"dimensions\", "
                          "\"encoding_format\" (float|base64), and \"task_type\". "
                          "Models: gemini-embedding-2 (8192 tok), gemini-embedding-001 (2048 tok).",
                    "ja": "input には文字列または文字列配列を指定可能。\"dimensions\"、"
                          "\"encoding_format\" (float|base64)、\"task_type\" に対応。"
                          "モデル: gemini-embedding-2 (8192 tok)、gemini-embedding-001 (2048 tok)。",
                },
                "request": {"model": "gemini-embedding-2", "input": "__sample__"},
            },
            {
                "method": "POST",
                "path": "/v1/decisions",
                "aliases": ["/decisions", "/v1beta/decisions", "/v1alpha/decisions"],
                "note": {
                    "vi": "Chuyển tiếp tới Gemini Decisions API (mặc định gemini-decision-1). "
                          "Giữ nguyên cấu trúc request/response từ Google.",
                    "en": "Forwards to the Gemini Decisions API (defaults to gemini-decision-1). "
                          "Preserves Google's request and response payload.",
                    "ja": "Gemini Decisions API（既定は gemini-decision-1）へ転送します。"
                          "Google のリクエスト／レスポンス形式をそのまま維持します。",
                },
                "request": {"model": "gemini-decision-1", "input": "__sample__"},
            },
        ],
    },
    {
        "group": {
            "vi": "Tìm kiếm web",
            "en": "Web search",
            "ja": "Web 検索",
        },
        "id": "search",
        "summary": {
            "vi": "Search là tính năng riêng của router, không cần dùng trong chat. "
                  "DuckDuckGo không đụng Gemini key nào, nên agent loop nhiều search "
                  "gần như không tốn hạn mức.",
            "en": "Search is a feature of the router itself, usable outside chat. DuckDuckGo "
                  "touches no Gemini key, so an agent that searches in a loop spends almost "
                  "no quota.",
            "ja": "検索はルーター独自の機能で、チャット以外からも使えます。DuckDuckGo は "
                  "Gemini キーを消費しないため、検索を繰り返すエージェントでもクォータをほぼ使いません。",
        },
        "endpoints": [
            {
                "method": "POST",
                "path": "/v1/search",
                "aliases": ["/search"],
                "note": {
                    "vi": "\"search_engine\": duckduckgo (mặc định, 0 quota) | "
                          "duckduckgo (tìm kiếm Router-owned, không dùng quota model).",
                    "en": "\"search_engine\": duckduckgo (default, 0 quota) — a router-owned "
                          "search that never spends model quota.",
                    "ja": "\"search_engine\": duckduckgo（既定、クォータ 0）— ルーター側の検索で、"
                          "モデルのクォータを消費しません。",
                },
                "request": {"query": "__sample__", "search_engine": "duckduckgo"},
            },
        ],
    },
    {
        "group": {
            "vi": "Vận hành",
            "en": "Operations",
            "ja": "運用",
        },
        "id": "ops",
        "summary": {
            "vi": "Kiểm tra sức khỏe và liệt kê model.",
            "en": "Health checks and model listings.",
            "ja": "ヘルスチェックとモデル一覧。",
        },
        "endpoints": [
            {
                "method": "GET",
                "path": "/health",
                "auth": "none",
                "note": {
                    "vi": "Không cần token.",
                    "en": "Needs no token.",
                    "ja": "トークン不要です。",
                },
            },
            {
                "method": "GET",
                "path": "/api/help",
                "note": {
                    "vi": "Chính trang này, dạng JSON. Cần session token của dashboard, "
                          "không dùng token API thường.",
                    "en": "This page as JSON. Needs a dashboard session token, not a regular "
                          "API token.",
                    "ja": "このページの JSON 版。通常の API トークンではなく、"
                          "ダッシュボードのセッショントークンが必要です。",
                },
            },
            {
                "method": "GET",
                "path": "/preflight",
                "note": {
                    "vi": "Kiểm tra key và model trước khi chạy.",
                    "en": "Checks keys and models before a run.",
                    "ja": "実行前にキーとモデルを確認します。",
                },
            },
        ],
    },
]

NOTES: List[Dict[str, Any]] = [
    {
        "title": {"vi": "Token nào dùng được", "en": "Which token works",
                  "ja": "使えるトークン"},
        "body": {
            "vi": "sk-<tên-tài-khoản>-<6 ký tự>, tạo trong tab tài khoản. "
                  "Master key (sk- + 43 ký tự) cũng gọi được nhưng không bị giới hạn RPM/TPM.",
            "en": "sk-<account-name>-<6 chars>, created in the accounts tab. A master key "
                  "(sk- plus 43 chars) also works but carries no RPM/TPM limit.",
            "ja": "sk-<アカウント名>-<6 文字>。アカウントタブで作成します。マスターキー"
                  "（sk- と 43 文字）も使えますが、RPM/TPM の制限はありません。",
        },
    },
    {
        "title": {"vi": "Nguồn suy nghĩ từ đâu", "en": "Where reasoning comes from",
                  "ja": "推論の出どころ"},
        "body": {
            "vi": "Gemini trả reasoning_content và thought_signature. Khi xuất ra protocol "
                  "Anthropic, signature được chế từ nội dung suy nghĩ vì Anthropic chỉ cần "
                  "chuỗi non-empty. Khi gửi lại cho Gemini, chính signature thật được truyền "
                  "lại — Gemini 3 trả 400 nếu thiếu.",
            "en": "Gemini returns reasoning_content and thought_signature. On the way out to the "
                  "Anthropic protocol the signature is synthesised from the thinking text, since "
                  "Anthropic only needs a non-empty string. On the way back to Gemini the real "
                  "signature is forwarded — Gemini 3 answers 400 without it.",
            "ja": "Gemini は reasoning_content と thought_signature を返します。Anthropic 形式へ"
                  "変換する際、Anthropic は空でない文字列しか要求しないため、思考テキストから"
                  "署名を合成します。Gemini へ戻すときは本物の署名を転送します — Gemini 3 は"
                  "署名がないと 400 を返します。",
        },
    },
    {
        "title": {"vi": "Pool model và model standalone", "en": "Model pools and standalone models", "ja": "モデルプールとスタンドアロンモデル"},
        "body": {
            "vi": "gemini-flash (Gemini 3: 36/30/38), gemini-flash-lite (Lite), gemini-4 (Gemini 4) "
                  "và gemini-2 (Gemini 2) là các pool ảo; router thử lần lượt các member phía sau, "
                  "mỗi member một slot đồng thời và có thể bật/tắt từng member ở trang Models. "
                  "gemma-4-26b, gemini-embedding-2, gemini-embedding-001 và gemini-decision-1 chạy standalone (không nằm trong pool).",
            "en": "gemini-flash (Gemini 3: 36/30/38), gemini-flash-lite (Lite), gemini-4 (Gemini 4), "
                  "and gemini-2 (Gemini 2) are virtual pools; the router tries their members in turn, "
                  "one concurrency slot each, with per-member toggles on the Models tab. "
                  "gemma-4-26b, gemini-embedding-2, gemini-embedding-001, and gemini-decision-1 run standalone outside any pool.",
            "ja": "gemini-flash（Gemini 3: 36/30/38）、gemini-flash-lite（Lite）、gemini-4（Gemini 4）、"
                  "gemini-2（Gemini 2）は仮想プールで、背後のメンバーを各 1 同時実行スロットで順に試し、"
                  "Models タブから個別に有効/無効を切り替えられます。"
                  "gemma-4-26b、gemini-embedding-2、gemini-embedding-001、gemini-decision-1 はプール外のスタンドアロンです。",
        },
    },
    {
        "title": {"vi": "Search trong chat khác gì /v1/search",
                  "en": "Search inside chat vs /v1/search",
                  "ja": "チャット内検索と /v1/search の違い"},
        "body": {
            "vi": "Client chat tự lo search: gửi web_search true (Anthropic/OpenAI) hoặc "
                  "tools web_search (Responses) đều đi qua tìm kiếm Router-owned. "
                  "Còn app tự điều phối thì gọi /v1/search rồi đưa "
                  "kết quả vào lượt sau — rẻ hơn vì không phải gọi lại model.",
            "en": "A chat client can search for itself: web_search true (Anthropic/OpenAI) or a "
                  "web_search tool (Responses) both go through the router-owned search. An app "
                  "that orchestrates on its own calls /v1/search and feeds the results into the "
                  "next turn — cheaper, since the model is not called twice.",
            "ja": "チャットクライアント自身が検索する場合、web_search true（Anthropic/OpenAI）"
                  "または web_search ツール（Responses）がいずれもルーター側の検索を通ります。"
                  "アプリ側で制御する場合は /v1/search を呼び、結果を次のターンに渡します — "
                  "モデルを二度呼ばない分だけ安く済みます。",
        },
    },
    {
        "title": {"vi": "Chỉ chạy một worker", "en": "Run a single worker",
                  "ja": "ワーカーは 1 つだけ"},
        "body": {
            "vi": "SQLite locking là process-local. Nhiều Uvicorn worker sẽ dính "
                  "database is locked.",
            "en": "SQLite locking is process-local. Several Uvicorn workers end up with "
                  "\"database is locked\".",
            "ja": "SQLite のロックはプロセス単位です。Uvicorn ワーカーを複数にすると "
                  "\"database is locked\" が発生します。",
        },
    },
]


def help_payload(origin: Optional[str] = None, lang: str = "vi") -> Dict[str, Any]:
    """The whole catalog, resolved for one origin and one language."""
    origin = (origin or DEFAULT_ORIGIN).rstrip("/")
    if lang not in LANGS:
        lang = "vi"
    return {
        "base_url": origin,
        "lang": lang,
        "groups": build_catalog(origin, lang),
        "notes": build_notes(lang),
    }
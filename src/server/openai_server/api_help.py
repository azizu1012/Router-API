"""A hand-written catalog of the endpoints, grouped by the dialect they speak.

FastAPI can generate a schema for all 75 routes, but this app's handlers take
`request: Request` and call `await request.json()` rather than declaring Pydantic
models, so the generated spec contains no request bodies at all. /openapi.json
lists that /v1/messages exists and shows you two nullable string headers. That
answers the wrong question.

So this module states the request shape by hand, with a runnable curl per
endpoint. The examples are written against a placeholder token and the port from
config, so they work as-is once the reader substitutes their own token.

Kept as data rather than JSX so it can be asserted on. If a route is renamed or
dropped, the test that compares these paths against the app's own route table
fails, which is the only way this stays honest.
"""

from typing import Any, Dict, List

TOKEN = "<your-token>"          # sk-<account>-<6 chars>, from the dashboard
KEY = "<your-api-key>"          # the Gemini key, only for native Gemini routes


def _curl(method: str, path: str, body: str = "", port: int = 58100) -> str:
    lines = [f"curl -X {method} http://127.0.0.1:{port}{path} \\",
             f'  -H "Authorization: Bearer {TOKEN}"']
    if body:
        lines.append("  -H \"Content-Type: application/json\" \\")
        lines.append(f"  -d '{body}'")
    return "\n".join(lines)


CATALOG: List[Dict[str, Any]] = [
    {
        "group": "Anthropic Messages API",
        "id": "anthropic",
        "summary": "Claude Code và mọi client nói chuẩn Anthropic. Cùng một endpoint "
                   "cho cả stream và non-stream.",
        "endpoints": [
            {
                "method": "POST",
                "path": "/v1/messages",
                "aliases": ["/messages"],
                "note": "\"stream\": true trả SSE theo chuẩn Anthropic. "
                        "\"tools\" dùng input_schema kiểu Anthropic. "
                        "\"output_config\": {\"effort\": \"high\"} điều khiển mức suy nghĩ.",
                "curl": _curl("POST", "/v1/messages", '{"model":"gemini-flash-35","max_tokens":1024,'
                                                '"messages":[{"role":"user","content":"Xin chào"}]}'),
                "curl_stream": _curl("POST", "/v1/messages", '{"model":"gemini-flash-35","max_tokens":1024,'
                                                       '"stream":true,"messages":[{"role":"user","content":"Xin chào"}]}'),
            },
            {
                "method": "POST",
                "path": "/v1/messages/count_tokens",
                "note": "Ước lượng token theo chuẩn Anthropic, không gọi provider.",
                "curl": _curl("POST", "/v1/messages/count_tokens", '{"model":"gemini-flash",'
                        '"messages":[{"role":"user","content":"Xin chào"}]}'),
            },
        ],
    },
    {
        "group": "OpenAI Chat Completions",
        "id": "openai-chat",
        "summary": "OpenAI SDK, Cline, Roo Code, và bất kỳ client nói chuẩn OpenAI.",
        "endpoints": [
            {
                "method": "POST",
                "path": "/v1/chat/completions",
                "note": "\"tools\" dùng parameters kiểu OpenAI. "
                        "\"reasoning_effort\": low|medium|high được map sang mức nghĩ của Gemini.",
                "curl": _curl("POST", "/v1/chat/completions", '{"model":"gemini-flash",'
                        '"messages":[{"role":"user","content":"Xin chào"}]}'),
            },
            {
                "method": "POST",
                "path": "/opencode/v1/chat/completions",
                "aliases": [],
                "note": "Bản OpenCode: trả tool_calls dạng OpenCode và giữ tên model khớp opencode.json.",
                "curl": _curl("POST", "/opencode/v1/chat/completions", '{"model":"gemini-flash",'
                        '"messages":[{"role":"user","content":"Xin chào"}]}'),
            },
            {
                "method": "GET",
                "path": "/v1/models",
                "note": "Schema superset: vừa đúng kiểu OpenAI vừa đúng kiểu Anthropic.",
                "curl": _curl("GET", "/v1/models"),
            },
        ],
    },
    {
        "group": "OpenAI Responses API",
        "id": "openai-responses",
        "summary": "Endpoint kiểu Responses. Đây là chỗ \"có respond\" — và là chỗ "
                   "mapping chứ không phải chuyển tiếp trong suốt.",
        "endpoints": [
            {
                "method": "POST",
                "path": "/v1/responses",
                "note": "tools dùng kiểu Responses. Gửi "
                        "\"tools\":[{\"type\":\"web_search\"}] để bật tìm kiếm server-side: "
                        "router chạy grounding trước, DuckDuckGo fallback, nên client "
                        "không phải tự vòng lặp. "
                        "\"instructions\" thành system message, \"reasoning\":{\"effort\"} "
                        "thành reasoning_effort, \"stream\": true trả SSE.",
                "curl": _curl("POST", "/v1/responses", '{"model":"gemini-flash",'
                        '"instructions":"Trả lời ngắn gọn.","input":"Ai là Turing?"}'),
                "curl_search": _curl("POST", "/v1/responses", '{"model":"gemini-flash",'
                        '"input":"Tin mới nhất về Rust 2.0?","tools":[{"type":"web_search"}]}'),
            },
        ],
    },
    {
        "group": "Gemini native (pass-through)",
        "id": "gemini-native",
        "summary": "Giữ nguyên kiểu Gemini, dùng key Gemini trực tiếp thay vì token router. "
                   "Không quay vòng key, không đổi model.",
        "endpoints": [
            {
                "method": "POST",
                "path": "/v1beta/models/{model_id}:generateContent",
                "aliases": ["/v1/models/{model_id}:generateContent",
                            "/v1alpha/models/{model_id}:generateContent"],
                "note": "Pass-through. Nhận token qua query ?key= hoặc header x-goog-api-key. "
                        "Ba version v1, v1beta, v1alpha đều trỏ cùng một handler.",
                "curl": f"curl -X POST 'http://127.0.0.1:58100/v1beta/models/gemini-3.5-flash:generateContent?key={KEY}' \\\n"
                        f"  -H 'Content-Type: application/json' \\\n"
                        f"  -d '{{\"contents\":[{{\"role\":\"user\",\"parts\":[{{\"text\":\"Xin chào\"}}]}}]}}'",
            },
            {
                "method": "POST",
                "path": "/v1beta/models/{model_id}:streamGenerateContent",
                "aliases": ["/v1/models/{model_id}:streamGenerateContent",
                            "/v1alpha/models/{model_id}:streamGenerateContent"],
                "note": "Bản streaming của endpoint trên.",
                "curl": f"curl -X POST 'http://127.0.0.1:58100/v1beta/models/gemini-3.5-flash:streamGenerateContent?key={KEY}' \\\n"
                        f"  -H 'Content-Type: application/json' \\\n"
                        f"  -d '{{\"contents\":[{{\"role\":\"user\",\"parts\":[{{\"text\":\"Xin chào\"}}]}}]}}'",
            },
        ],
    },
    {
        "group": "Tìm kiếm web",
        "id": "search",
        "summary": "Search là tính năng riêng của router, không cần dùng trong chat. "
                   "DuckDuckGo không đụng Gemini key nào, nên agent loop nhiều search "
                   "gần như không tốn hạn mức.",
        "endpoints": [
            {
                "method": "POST",
                "path": "/v1/search",
                "aliases": ["/search"],
                "note": "\"search_engine\": duckduckgo (mặc định, 0 quota) | "
                        "google_grounding (tốn quota, chất lượng cao hơn) | "
                        "auto (grounding trước, DuckDuckGo fallback).",
                "curl": _curl("POST", "/v1/search", '{"query":"tin tức Rust mới nhất",'
                        '"search_engine":"duckduckgo"}'),
            },
        ],
    },
    {
        "group": "Vận hành",
        "id": "ops",
        "summary": "Kiểm tra sức khỏe và liệt kê model.",
        "endpoints": [
            {"method": "GET", "path": "/health", "note": "Không cần token.",
             "curl": "curl -X GET http://127.0.0.1:58100/health"},
            {"method": "GET", "path": "/api/help", "note": "Chính trang này, dạng JSON. "
                                                              "Cần session token của dashboard, "
                                                              "không dùng token API thường.",
             "curl": _curl("GET", "/api/help")},
            {"method": "GET", "path": "/preflight", "note": "Kiểm tra key và model trước khi chạy.",
             "curl": _curl("GET", "/preflight")},
        ],
    },
]

NOTES: List[Dict[str, str]] = [
    {
        "title": "Token nào dùng được",
        "body": "sk-<tên-tài-khoản>-<6 ký tự>, tạo trong tab tài khoản. "
                "Master key (sk- + 43 ký tự) cũng gọi được nhưng không bị giới hạn RPM/TPM.",
    },
    {
        "title": "Nguồn suy nghĩ từ đâu",
        "body": "Gemini trả reasoning_content và thought_signature. Khi xuất ra protocol "
                "Anthropic, signature được chế từ nội dung suy nghĩ vì Anthropic chỉ cần "
                "chuỗi non-empty. Khi gửi lại cho Gemini, chính signature thật được truyền "
                "lại — Gemini 3 trả 400 nếu thiếu.",
    },
    {
        "title": "Pool model",
        "body": "gemini-flash và gemini-flash-lite là pool ảo, không phải model thật. "
                "Router thử lần lượt các member phía sau, mỗi member một slot đồng thời. "
                "Dùng gemini-flash-38 tới gemini-flash-25 để ghim đúng một member.",
    },
    {
        "title": "Search trong chat khác gì /v1/search",
        "body": "Client chat tự lo search: gửi web_search true (Anthropic/OpenAI) hoặc "
                "tools web_search (Responses). Responses thì grounding trước. "
                "Còn lại đi DuckDuckGo. Còn app tự điều phối thì gọi /v1/search rồi đưa "
                "kết quả vào lượt sau — rẻ hơn vì không phải gọi lại model.",
    },
    {
        "title": "Chỉ chạy một worker",
        "body": "SQLite locking là process-local. Nhiều Uvicorn worker sẽ dính "
                "database is locked.",
    },
]


def help_payload() -> Dict[str, Any]:
    """The whole catalog, as the dashboard consumes it."""
    return {"groups": CATALOG, "notes": NOTES}

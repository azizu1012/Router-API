# MCP Server — `/mcp`

Router API đóng vai một **MCP server** (Model Context Protocol) bên cạnh ba giao
thức LLM. Nó expose đúng một tool:

| Tool | Signature | Engine | Tốn quota Gemini? |
|---|---|---|---|
| `search_web` | `query: string` → text + sources | DuckDuckGo (Router-owned) | **không** |

Tool gọi `execute_hybrid_search` **in-process**, không HTTP về lại chính server
này — nếu gọi vòng qua HTTP thì latency nhân đôi, quota bị trừ hai lần, và cần
thêm một connection vào worker đơn.

---

## 1. Endpoint & auth

| | |
|---|---|
| **URL** | `http://127.0.0.1:58100/mcp` local · `https://<domain>/mcp` qua reverse proxy |
| **Transport** | Streamable HTTP — `POST` (JSON-RPC), `GET` (mở SSE stream), `DELETE` (kết thúc session) |
| **Auth** | Header `Authorization: Bearer <token>` |
| **Token** | Token account bình thường (`sk-<name>-<code>`) hoặc master key — xem `account_auth.md` mục 2 |

Token được verify **trước khi** tool được chạy: `_check_auth()` trả `None` thì
SDK trả 401 ngay, nên dò token sai không tốn một lượt search nào.

Header bắt buộc khi gọi tay:

```
Authorization: Bearer <token>
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2025-06-18
```

### Account `mcp_only`

Cột `accounts.mcp_only` giới hạn một account chỉ được dùng qua MCP và **không**
được gọi model route nào. Cổng chặn nằm ở `mcp_only_middleware` — đúng chỗ mọi
request đi qua, kể cả request tới qua mount `/mcp` — nên cần exemption riêng cho
`/mcp`, không có thì account đó bị khoá ngay ở cửa của chính nó.

Chỉ **cả account** mới `mcp_only` được; token minted bên dưới account đó không bị
chặn (xem `app_init.py:56`).

---

## 2. Cấu hình client

### Claude Code

```bash
claude mcp add --transport http router-search http://127.0.0.1:58100/mcp \
  --header "Authorization: Bearer <TOKEN>"
```

Thêm `--scope project` nếu chỉ dùng trong project hiện tại (mặc định là local scope).

### OpenCode — `opencode.json`

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "router-search": {
      "type": "remote",
      "url": "http://127.0.0.1:58100/mcp",
      "enabled": true,
      "headers": {
        "Authorization": "Bearer <TOKEN>"
      }
    }
  }
}
```

### `mcp.json` chuẩn (Cursor, Windsurf, Cline, Roo, Trae, Zed, LibreChat)

```json
{
  "mcpServers": {
    "router-search": {
      "type": "http",
      "url": "http://127.0.0.1:58100/mcp",
      "headers": {
        "Authorization": "Bearer <TOKEN>"
      }
    }
  }
}
```

Client nào chỉ hiểu `"type": "streamable-http"` thì đổi chuỗi đó — `url` và
`headers` giữ nguyên.

---

## 3. Kiểm tra tay bằng curl

### Linux / macOS / WSL

```bash
curl -sS -X POST http://127.0.0.1:58100/mcp \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2025-06-18" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

### Windows CMD

Dán **nguyên một dòng**. CMD không hiểu dấu nháy đơn, nên JSON phải escape bằng
`\"`; nối dòng bằng `^` ở cuối dòng (không có khoảng trắng sau nó) nhưng dạng
nhiều dòng hay bị nuốt mất ký tự escape — ưu tiên một dòng.

```cmd
curl -sS -X POST https://azure.wibu.me/mcp -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "MCP-Protocol-Version: 2025-06-18" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{}}"
```

Chỉ xem mã lỗi (Windows dùng `NUL`, không phải `/dev/null`):

```cmd
curl -sS -o NUL -w "%{http_code}" -X POST https://azure.wibu.me/mcp -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "MCP-Protocol-Version: 2025-06-18" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{}}"
```

Nếu gõ `curl` mà CMD báo không nhận lệnh, dùng `curl.exe`. Response đúng phải có
`"search_web"` trong `tools`.

---

## 4. `MCP_ALLOWED_HOSTS`

MCP SDK bật **DNS-rebinding protection**: header `Host` của request được so với
allowlist, lệch là `421 Misdirected Request` với body plain-text
`Invalid Host header`.

Mặc định (`mcp_routes.py:_allowed_hosts`) chỉ có `127.0.0.1:*`, `localhost:*`,
`testserver`, `testclient`. **Mọi domain — kể cả khi bạn đã đặt reverse proxy —
đều bị 421** cho tới khi khai báo.

```env
MCP_ALLOWED_HOSTS=azure.wibu.me,127.0.0.1:*,localhost:*
```

Đọc một lần trong `start_mcp_session()` lúc khởi động → **sửa xong phải restart**.

### Ba điều dễ sai

**1. Biến ghi đè, không nối thêm.** Đặt `MCP_ALLOWED_HOSTS=example.com` là mất
luôn `127.0.0.1:*` mặc định. Muốn test local song song domain thì phải liệt kê cả
hai.

**2. Wildcard port không tự động có.** SDK so khớp **đúng chuỗi**, hoặc mẫu
`host:*` nhận mọi port:

| `MCP_ALLOWED_HOSTS` | `Host: example.com` | `Host: example.com:58100` |
|---|---|---|
| `example.com` | khớp | **lệch → 421** |
| `example.com:*` | lệch → 421 | khớp |
| `example.com,example.com:*` | khớp | khớp |

HTTPS mặc định là 443 và client **không** gửi port trong `Host` khi port là
default của scheme — nên `https://example.com/mcp` ra đúng `Host: example.com` và
chỉ cần dòng trần. Nếu client gọi `http://example.com:58100` thì header có port
và cần mẫu `:*`.

**3. Không suy ra hostname từ URL public.** `start_mcp_session()` được gọi không
có tham số (`app_init.py:430`), nên `resource_server_url` luôn là default
`http://127.0.0.1:58100` và nhánh tự thêm hostname trong `_allowed_hosts()` luôn
thêm `127.0.0.1`. Env var là đường duy nhất để operator đưa hostname thật vào.

### Chẩn đoán khi 421

SDK log warning ở mỗi lần 421 (`Invalid Host header: <giá trị>`) nhưng logger của
SDK không đi qua `logs/` của dự án — `setup_logger` đặt `propagate = False` cho
logger riêng. Warning nằm ở **stdout của process**:

```bash
sudo journalctl -u router-api -f          # systemd
```

Chép nguyên chuỗi sau `Invalid Host header: ` vào `MCP_ALLOWED_HOSTS`. Kiểm tra
phía client bằng:

```bash
curl -v -X POST https://example.com/mcp -d '{}' 2>&1 | grep -i '^> host'
```

---

## 5. Troubleshooting

| Triệu chứng | Nguyên nhân |
|---|---|
| `421 Misdirected Request` | `Host` không có trong allowlist → mục 4 |
| `403` kèm chữ `Origin` | `allowed_origins` để rỗng; chỉ client chạy trong browser (web UI, extension) mới gửi header này. Client native/CLI không gửi nên không dính |
| `401` | Token sai, hoặc account bị `enabled = 0` |
| `405 Method Not Allowed` | URL có `/mcp/mcp` — chỉ dùng `/mcp` |
| `400 Invalid Content-Type` | Thiếu `\` trước dấu nháy kép khi escape JSON cho CMD |
| `503 MCP server is starting` | Server vừa restart, chờ vài giây |
| `tools/list` rỗng | SDK `mcp==2.3.0` chưa cài (xem `requirements.txt`) |
| 403 ở `/v1/messages` nhưng 200 ở `/mcp` | Đang dùng account `mcp_only` — đúng thiết kế |

---

## 6. Vì sao có mấy tầng này

Ba chi tiết dưới đây từng làm server "load xong nhưng không trả lời gì", và cả ba
đều im lặng — không log, không status code sai:

- **Lifespan của sub-app không chạy.** `app.mount(...)` chỉ tra cho Starlette
  router `(scope, receive, send)` — không có lifespan. SDK mở task group của
  session manager trong `lifespan`, nên không chạy nó thì mọi tool call chết với
  *"Task group is not initialized"*. Vì vậy context được enter **khi request đầu
  tiên** chứ không lúc import: một module import không có event loop để giữ nó.
- **Đường dẫn sẽ là `/mcp/mcp`.** `streamable_http_app()` mặc định route của nó
  là `/mcp`; mount dưới `/mcp` thành `/mcp/mcp` và client cấu hình `/mcp` nhận
  404. Truyền `streamable_http_path="/"` để chính cái mount là endpoint.
  Ngoài ra `app.mount()` chỉ khớp `/mcp/...`, không khớp `/mcp` — nên có
  middleware ghi lại `/mcp` → `/mcp/`. Dùng rewrite chứ không dùng 307, vì 307
  bắt client phát lại một POST JSON-RPC có body.
- **Verb của route bị mất.** SDK tạo route bằng `Route(path, endpoint)` không
  truyền `methods`. Starlette 1.2 đổi default từ `None` ("mọi method") sang
  `["GET"]`, nên POST — tức toàn bộ JSON-RPC — khớp partial và server trả 405
  **trước khi** endpoint được gọi. Triệu chứng gọi sai tên hoàn toàn: "Method Not
  Allowed" trên một endpoint sinh ra để nhận POST. `_allow_mcp_verbs()` gán lại
  `{GET, POST, DELETE}` sau khi build.

Chi tiết đầy đủ và lịch sử: `docs/bug-logs.md` Bug #37.

---

## 7. Test

`tests/test_mcp_server.py` — 20 test, chạy bằng MCP client thật
(`streamable_http_client`) để xác nhận `tools/list` trả về `search_web`.

Test quan trọng nhất là `test_but_its_own_door_stays_open`: account `mcp_only` bị
403 ở `/v1/messages` **nhưng** vẫn 200 ở `/mcp`. Không có test đó thì lỗi "chặn
nhầm cửa" sẽ không ai thấy.

`TestTheHostAllowlist` (8 test) phủ `MCP_ALLOWED_HOSTS`. Nó so khớp bằng chính
`_validate_host` của SDK chứ không viết lại phép so — test viết lại thì chứng
minh bản sao, không chứng minh cái sửa. Một test trong đó đi qua HTTP thật:
`Host: evil.test` phải ra đúng `421` + body `Invalid Host header`, còn
`Host: example.com` phải ra 200 trên cùng request đó.

### `.env` của bạn có thể làm đỏ suite

`MCP_ALLOWED_HOSTS` ghi đè danh sách mặc định, mà mặc định chứa `testserver` —
host mà `TestClient` gửi. Nên khai domain của bạn trong `.env` mà quên
`testserver` là **3 test MCP chết ngay**, trong khi checkout sạch vẫn xanh.

`_mcp_accepts_the_test_client_host` trong `conftest.py` giữ `testserver` và
`testclient` cho mọi lần chạy, bất kể `.env` khai gì. Cùng kiểu với
`_password_recovery_is_testable` — xem `bug-logs.md` Bug #36.

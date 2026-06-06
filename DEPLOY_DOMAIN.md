# Deploy Router API Với Domain

Mục tiêu: app ngoài gọi được `https://azure.wibu.me/v1`  
Router API chạy nội bộ `http://127.0.0.1:58100`, reverse proxy (Caddy/Nginx) đứng trước.

## 1. Yêu cầu

- VPS Linux, mở port `80` và `443`.
- Domain trỏ A record về IP server.
- Router API chạy được local:

```bash
cd /opt/router-api
source .venv/bin/activate
python main.py
```

## 2. Cài đặt

```bash
cd /opt
git clone <your-repo> router-api
cd router-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Tối thiểu `.env`:

```env
GEMINI_API_KEY_1=your_gemini_key
ROUTER_API_HOST=127.0.0.1
ROUTER_API_PORT=58100
```

Tạo account:

```bash
python -m src.console.admin_console create coder
python -m src.console.admin_console list --show-keys
```

## 3. systemd service

```bash
sudo nano /etc/systemd/system/router-api.service
```

```ini
[Unit]
Description=Router API v2
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/router-api
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/router-api/.venv/bin/python main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now router-api
sudo systemctl status router-api
```

Test local: `curl http://127.0.0.1:58100/health`

## 4. Reverse proxy

### Caddy (tự động HTTPS)

```caddy
azure.wibu.me {
    reverse_proxy 127.0.0.1:58100
}
```

### Nginx

```nginx
server {
    listen 80;
    server_name azure.wibu.me;
    client_max_body_size 25m;

    location / {
        proxy_pass http://127.0.0.1:58100;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
```

```
sudo certbot --nginx -d azure.wibu.me
```

## 5. Dùng với Claude Code qua domain

```bash
export ANTHROPIC_BASE_URL="https://azure.wibu.me"
export ANTHROPIC_AUTH_TOKEN="sk-xxxx"
export ANTHROPIC_MODEL="gemini-flash-35"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
claude
```

## 6. Troubleshooting

- `nslookup azure.wibu.me` ra đúng IP?
- Firewall mở port 80, 443?
- `curl http://127.0.0.1:58100/health` OK trên server?
- `sudo journalctl -u router-api -n 100 --no-pager` xem log
- `sudo systemctl status caddy` hoặc `nginx -t`

# ruff: noqa: E402
"""Router API entry point.
Usage:
    python main.py              # foreground (default)
    python main.py --daemon     # background (detached process, win/linux)
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── Block botocore pre-load (unused AWS dependency) ──
from unittest.mock import MagicMock as _Mock
_boto = _Mock()
_boto.exceptions = _Mock()
_boto.compat = _Mock()
_boto.awsrequest = _Mock()
sys.modules["botocore"] = _boto
sys.modules["botocore.exceptions"] = _boto.exceptions
sys.modules["botocore.compat"] = _boto.compat
sys.modules["botocore.awsrequest"] = _boto.awsrequest


import uvicorn
from src.core.config_n_logg import config, logger
from src.core.config_n_logg.logger import CONSOLE_LOG_SYSTEM, CONSOLE_LOG_WEB, _ensure_log_dir
from src.core.api_config import AVAILABLE_MODELS, is_sunset_25



def _free_port(host: str, port: int) -> None:
    """Kill existing process on host:port if any."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex((host, port)) != 0:
            return
    logger.warning("Port %s:%d already in use — killing old process", host, port)
    if sys.platform == "win32":
        try:
            ps = subprocess.run(
                ["powershell", "-Command",
                 f"Get-NetTCPConnection -LocalPort {port} -State Listen "
                 f"| Select-Object -ExpandProperty OwningProcess -Unique"],
                capture_output=True, text=True, timeout=10,
            )
            for line in ps.stdout.strip().splitlines():
                pid = line.strip()
                if pid.isdigit():
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=5)
        except Exception as e:
            logger.warning("Failed to free port via PowerShell: %s", e)
    else:
        try:
            subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True, timeout=10)
        except Exception as e:
            logger.warning("Failed to free port via fuser: %s", e)
    time.sleep(1)


def _start_daemon() -> None:
    """Spawn server in a background process and exit."""
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out = (log_dir / "stdout.log").open("a", encoding="utf-8")
    err = (log_dir / "stderr.log").open("a", encoding="utf-8")
    args = [sys.executable, __file__]
    if sys.platform == "win32":
        proc = subprocess.Popen(
            args,
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
        )
    else:
        proc = subprocess.Popen(
            args,
            start_new_session=True,
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
        )
    out.close()
    err.close()
    print(f"Router API daemon started (PID {proc.pid})")
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="Router API v2")
    parser.add_argument("--daemon", action="store_true", help="Run as background daemon")
    parser.add_argument("--pidfile", type=str, default="", help="Write PID to file")
    parsed, _ = parser.parse_known_args()

    if parsed.daemon:
        _start_daemon()
        return

    if parsed.pidfile:
        Path(parsed.pidfile).write_text(str(os.getpid()))

    _free_port(config.HOST, config.PORT)

    for _alias, _cfg in AVAILABLE_MODELS.items():
        _mid = str(_cfg.get("model_id", ""))
        if "pool" in _mid:
            continue
        if is_sunset_25() and _alias in ("gemini-flash-25", "gemini-flash-25-lite"):
            continue

    logger.info(
        "Starting Router API v2 on %s:%d with %d Gemini keys",
        config.HOST,
        config.PORT,
        len(config.GEMINI_API_KEYS),
    )

    _ensure_log_dir()

    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(asctime)s [%(levelname)s] %(message)s",
                "use_colors": None,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(asctime)s [%(levelname)s] %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
        },
        "handlers": {
            "console": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "access_console": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "system_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "default",
                "filename": "logs/system.log",
                "maxBytes": 10 * 1024 * 1024,
                "backupCount": 3,
                "encoding": "utf-8",
            },
            "web_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "access",
                "filename": "logs/web.log",
                "maxBytes": 10 * 1024 * 1024,
                "backupCount": 3,
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["system_file", "console"] if CONSOLE_LOG_SYSTEM else ["system_file"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["system_file", "console"] if CONSOLE_LOG_SYSTEM else ["system_file"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["web_file", "access_console"] if CONSOLE_LOG_WEB else ["web_file"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }

    ssl_keyfile = os.getenv("SSL_KEYFILE", "")
    ssl_certfile = os.getenv("SSL_CERTFILE", "")
    use_ssl = ssl_keyfile and ssl_certfile and Path(ssl_keyfile).is_file() and Path(ssl_certfile).is_file()

    uvicorn.run(
        "src.server.openai_server:app",
        host=config.HOST,
        port=config.PORT,
        log_config=log_config,
        reload=config.RELOAD,
        ssl_keyfile=ssl_keyfile if use_ssl else None,
        ssl_certfile=ssl_certfile if use_ssl else None,
    )


if __name__ == "__main__":
    main()

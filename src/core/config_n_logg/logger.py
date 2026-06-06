import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


_LOG_DIR = Path(__file__).resolve().parents[3] / "logs"


def _get_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _ensure_log_dir():
    _LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logger(name: str, filename: str, console: bool = True) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    if console:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    _ensure_log_dir()
    file_handler = RotatingFileHandler(
        filename=str(_LOG_DIR / filename),
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger


# Console log configurations from environment variables
CONSOLE_LOG_SYSTEM = _get_bool_env("CONSOLE_LOG_SYSTEM", True)
CONSOLE_LOG_PROXY = _get_bool_env("CONSOLE_LOG_PROXY", False)   # Mặc định False để đỡ rối console
CONSOLE_LOG_KEYS = _get_bool_env("CONSOLE_LOG_KEYS", False)     # Mặc định False
CONSOLE_LOG_WEB = _get_bool_env("CONSOLE_LOG_WEB", True)       # Mặc định True


# Create separate loggers for different categories
logger_system = setup_logger("system", "system.log", console=CONSOLE_LOG_SYSTEM)
logger_proxy = setup_logger("proxy", "proxy.log", console=CONSOLE_LOG_PROXY)
logger_web = setup_logger("web", "web.log", console=CONSOLE_LOG_WEB)
logger_keys = setup_logger("keys", "keys.log", console=CONSOLE_LOG_KEYS)
logger_api = setup_logger("api", "api_calls.log", console=False)
logger_keepalive = setup_logger("keepalive", "keepalive.log", console=False)

# Default logger for backward compatibility
logger = logger_system

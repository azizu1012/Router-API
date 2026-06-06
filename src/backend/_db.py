import sqlite3
import threading
from pathlib import Path

_DB = str(Path(__file__).resolve().parents[2] / "usage.db")
_LOCK = threading.RLock()


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    return c

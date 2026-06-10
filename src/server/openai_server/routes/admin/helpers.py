"""Shared helpers for admin endpoints — environment / DB key management."""
import json
import uuid

from src.core.config_n_logg import config, ENV_PATH
from src.backend._db import _LOCK, conn as _conn


def append_key_to_env(api_key: str) -> None:
    """Append a Gemini API key to the ``.env`` file."""
    if not ENV_PATH.exists():
        ENV_PATH.touch()
    content = ENV_PATH.read_text(encoding="utf-8")
    if api_key in content:
        return
    key_name = f"GEMINI_API_KEY_{uuid.uuid4().hex[:8].upper()}"
    with open(ENV_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n{key_name}={api_key}\n")


def remove_key_from_env(api_key: str) -> bool:
    """Remove a Gemini API key line from the ``.env`` file. Returns True if found and removed."""
    if not ENV_PATH.exists():
        return False
    content = ENV_PATH.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines = []
    found = False
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            parts = line.split("=", 1)
            if parts[1].strip() == api_key:
                found = True
                continue
        new_lines.append(line)
    if found:
        ENV_PATH.write_text("\n".join(new_lines), encoding="utf-8")
    return found


def normalize_key_name(key: str) -> str:
    """Match a key name against the DB (case-insensitive)."""
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute("SELECT key FROM key_status").fetchall()
            for r in rows:
                db_k = r["key"]
                if db_k.lower().replace("\\", "/") == key.lower().replace("\\", "/"):
                    return db_k
        finally:
            c.close()
    return key

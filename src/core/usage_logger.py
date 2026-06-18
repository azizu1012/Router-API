import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

import aiosqlite

from src.core.config_n_logg.logger import logger_system as logger

_DB = str(Path(__file__).resolve().parents[2] / "usage_logs.db")

_queue: asyncio.Queue = asyncio.Queue()
_flush_task: asyncio.Task | None = None


async def init_db() -> None:
    async with aiosqlite.connect(_DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model_alias TEXT NOT NULL,
                key_prefix TEXT DEFAULT '',
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                auth_key_prefix TEXT DEFAULT '',
                cache_creation_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0
            )
        """)
        for col in ["auth_key_prefix", "cache_creation_tokens", "cache_read_tokens"]:
            try:
                await db.execute(f"ALTER TABLE usage_logs ADD COLUMN {col} INTEGER DEFAULT 0")
            except Exception:
                pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_logs(timestamp)")
        await db.commit()


async def log_usage(
    model_alias: str,
    key_prefix: str,
    prompt_tokens: int,
    completion_tokens: int,
    auth_key_prefix: str = "",
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> None:
    from src.core.api_config import resolve_model_alias
    resolved = resolve_model_alias(model_alias)
    await _queue.put((
        datetime.now().isoformat(),
        resolved,
        key_prefix or "unknown",
        prompt_tokens or 0,
        completion_tokens or 0,
        (prompt_tokens or 0) + (completion_tokens or 0),
        auth_key_prefix or "",
        cache_creation_tokens or 0,
        cache_read_tokens or 0,
    ))


async def _flush_loop(interval: float = 5.0) -> None:
    while True:
        await asyncio.sleep(interval)
        batch: List[tuple] = []
        while not _queue.empty():
            batch.append(await _queue.get())
        if not batch:
            continue
        try:
            async with aiosqlite.connect(_DB) as db:
                await db.executemany(
                    "INSERT INTO usage_logs (timestamp, model_alias, key_prefix, prompt_tokens, completion_tokens, total_tokens, auth_key_prefix, cache_creation_tokens, cache_read_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    batch,
                )
                cutoff = (datetime.now() - timedelta(days=30)).isoformat()
                await db.execute("DELETE FROM usage_logs WHERE timestamp < ?", (cutoff,))
                await db.commit()
        except Exception as e:
            logger.error("[UsageLogger] Flush error: %s", e)
            # Re-queue on failure to avoid losing usage data.
            for item in batch:
                await _queue.put(item)


def start_flush_loop(interval: float = 5.0) -> None:
    global _flush_task
    if _flush_task is None or _flush_task.done():
        _flush_task = asyncio.create_task(_flush_loop(interval))


def normalize_to_pool_alias(alias: str) -> str:
    alias_lower = str(alias).strip().lower()
    if "lite" in alias_lower:
        return "gemini-flash-lite"
    if "flash" in alias_lower:
        return "gemini-flash"
    return alias


async def get_stats(days: int = 30) -> Dict[str, Any]:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        async with aiosqlite.connect(_DB) as db:
            db.row_factory = aiosqlite.Row

            c = await db.execute(
                "SELECT model_alias, SUM(prompt_tokens) as p, SUM(completion_tokens) as c, SUM(total_tokens) as t, "
                "SUM(cache_creation_tokens) as cc, SUM(cache_read_tokens) as cr, COUNT(*) as req "
                "FROM usage_logs WHERE timestamp >= ? GROUP BY model_alias ORDER BY t DESC",
                (cutoff,),
            )
            rows = await c.fetchall()
            summary_dict = {}
            for r in rows:
                item = dict(r)
                raw_alias = item.get("model_alias") or "unknown"
                norm_alias = normalize_to_pool_alias(raw_alias)
                if norm_alias not in summary_dict:
                    summary_dict[norm_alias] = {
                        "model_alias": norm_alias,
                        "p": 0,
                        "c": 0,
                        "t": 0,
                        "cc": 0,
                        "cr": 0,
                        "req": 0,
                    }
                entry = summary_dict[norm_alias]
                entry["p"] += item.get("p", 0) or 0
                entry["c"] += item.get("c", 0) or 0
                entry["t"] += item.get("t", 0) or 0
                entry["cc"] += item.get("cc", 0) or 0
                entry["cr"] += item.get("cr", 0) or 0
                entry["req"] += item.get("req", 0) or 0

            summary = sorted(summary_dict.values(), key=lambda x: x["t"], reverse=True)

            c2 = await db.execute(
                "SELECT DATE(timestamp) as d, model_alias, SUM(total_tokens) as t, COUNT(*) as req "
                "FROM usage_logs WHERE timestamp >= ? GROUP BY d, model_alias ORDER BY d",
                (cutoff,),
            )
            rows2 = await c2.fetchall()
            daily_dict = {}
            for r in rows2:
                item = dict(r)
                d = item.get("d")
                raw_alias = item.get("model_alias") or "unknown"
                norm_alias = normalize_to_pool_alias(raw_alias)
                key = (d, norm_alias)
                if key not in daily_dict:
                    daily_dict[key] = {
                        "d": d,
                        "model_alias": norm_alias,
                        "t": 0,
                        "req": 0,
                    }
                entry = daily_dict[key]
                entry["t"] += item.get("t", 0) or 0
                entry["req"] += item.get("req", 0) or 0

            daily = sorted(daily_dict.values(), key=lambda x: (x["d"], x["model_alias"]))

            c3 = await db.execute(
                "SELECT COUNT(*) as total FROM usage_logs WHERE timestamp >= ?",
                (cutoff,),
            )
            row = await c3.fetchone()
            total_requests = row["total"] if row else 0

            return {
                "summary": summary,
                "daily": daily,
                "total_requests": total_requests,
            }
    except Exception as e:
        logger.error("[UsageLogger] get_stats error: %s", e)
        return {"summary": [], "daily": [], "total_requests": 0}


async def get_top_keys(days: int = 30, limit: int = 5) -> List[Dict[str, Any]]:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        async with aiosqlite.connect(_DB) as db:
            db.row_factory = aiosqlite.Row
            c = await db.execute(
                "SELECT auth_key_prefix as key_prefix, SUM(total_tokens) as t, COUNT(*) as req "
                "FROM usage_logs WHERE timestamp >= ? AND auth_key_prefix != '' "
                "GROUP BY auth_key_prefix ORDER BY t DESC LIMIT ?",
                (cutoff, limit),
            )
            return [dict(r) for r in await c.fetchall()]
    except Exception as e:
        logger.error("[UsageLogger] get_top_keys error: %s", e)
        return []


async def get_stats_for_prefix(auth_key_prefix: str, days: int = 30) -> Dict[str, Any]:
    """Usage stats filtered by a single auth_key_prefix (for per-user dashboard)."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        async with aiosqlite.connect(_DB) as db:
            db.row_factory = aiosqlite.Row
            c = await db.execute(
                "SELECT model_alias, SUM(prompt_tokens) as p, SUM(completion_tokens) as c, "
                "SUM(total_tokens) as t, SUM(cache_creation_tokens) as cc, SUM(cache_read_tokens) as cr, COUNT(*) as req FROM usage_logs "
                "WHERE timestamp >= ? AND auth_key_prefix = ? GROUP BY model_alias ORDER BY t DESC",
                (cutoff, auth_key_prefix),
            )
            rows = await c.fetchall()
            summary_dict = {}
            for r in rows:
                item = dict(r)
                raw_alias = item.get("model_alias") or "unknown"
                norm_alias = normalize_to_pool_alias(raw_alias)
                if norm_alias not in summary_dict:
                    summary_dict[norm_alias] = {
                        "model_alias": norm_alias,
                        "p": 0,
                        "c": 0,
                        "t": 0,
                        "cc": 0,
                        "cr": 0,
                        "req": 0,
                    }
                entry = summary_dict[norm_alias]
                entry["p"] += item.get("p", 0) or 0
                entry["c"] += item.get("c", 0) or 0
                entry["t"] += item.get("t", 0) or 0
                entry["cc"] += item.get("cc", 0) or 0
                entry["cr"] += item.get("cr", 0) or 0
                entry["req"] += item.get("req", 0) or 0

            summary = sorted(summary_dict.values(), key=lambda x: x["t"], reverse=True)

            c2 = await db.execute(
                "SELECT DATE(timestamp) as d, model_alias, SUM(total_tokens) as t, COUNT(*) as req "
                "FROM usage_logs WHERE timestamp >= ? AND auth_key_prefix = ? "
                "GROUP BY d, model_alias ORDER BY d",
                (cutoff, auth_key_prefix),
            )
            rows2 = await c2.fetchall()
            daily_dict = {}
            for r in rows2:
                item = dict(r)
                d = item.get("d")
                raw_alias = item.get("model_alias") or "unknown"
                norm_alias = normalize_to_pool_alias(raw_alias)
                key = (d, norm_alias)
                if key not in daily_dict:
                    daily_dict[key] = {
                        "d": d,
                        "model_alias": norm_alias,
                        "t": 0,
                        "req": 0,
                    }
                entry = daily_dict[key]
                entry["t"] += item.get("t", 0) or 0
                entry["req"] += item.get("req", 0) or 0

            daily = sorted(daily_dict.values(), key=lambda x: (x["d"], x["model_alias"]))

            c3 = await db.execute(
                "SELECT COUNT(*) as total FROM usage_logs WHERE timestamp >= ? AND auth_key_prefix = ?",
                (cutoff, auth_key_prefix),
            )
            row = await c3.fetchone()
            return {
                "summary": summary,
                "daily": daily,
                "total_requests": row["total"] if row else 0,
            }
    except Exception as e:
        logger.error("[UsageLogger] get_stats_for_prefix error: %s", e)
        return {"summary": [], "daily": [], "total_requests": 0}


async def dump_to_json() -> Dict[str, Any]:
    stats = await get_stats(30)
    top_keys = await get_top_keys(5)
    return {
        "stats": stats,
        "top_keys": top_keys,
    }

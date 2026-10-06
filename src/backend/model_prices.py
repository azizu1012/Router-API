from ._db import conn as _conn, _LOCK


def get_model_price(model_name: str) -> dict:
    with _LOCK:
        db = _conn()
        try:
            cur = db.execute("SELECT * FROM model_prices WHERE model_name = ?", (model_name,))
            row = cur.fetchone()
            if row:
                return {
                    "model_name": row["model_name"],
                    "input_rate_per_1k": row["input_rate_per_1k"],
                    "output_rate_per_1k": row["output_rate_per_1k"],
                    "response_model_name": row["response_model_name"],
                }
            return {}
        finally:
            db.close()


def set_model_price(model_name: str, input_rate: float, output_rate: float, response_model: str = "") -> None:
    with _LOCK:
        db = _conn()
        try:
            db.execute(
                "INSERT OR REPLACE INTO model_prices (model_name, input_rate_per_1k, output_rate_per_1k, response_model_name) VALUES (?,?,?,?)",
                (model_name, input_rate, output_rate, response_model),
            )
            db.commit()
        finally:
            db.close()


def list_model_prices() -> list[dict]:
    with _LOCK:
        db = _conn()
        try:
            rows = db.execute("SELECT * FROM model_prices ORDER BY model_name").fetchall()
            return [
                {
                    "model_name": r["model_name"],
                    "input_rate_per_1k": r["input_rate_per_1k"],
                    "output_rate_per_1k": r["output_rate_per_1k"],
                    "response_model_name": r["response_model_name"],
                }
                for r in rows
            ]
        finally:
            db.close()


def delete_model_price(model_name: str) -> None:
    with _LOCK:
        db = _conn()
        try:
            db.execute("DELETE FROM model_prices WHERE model_name = ?", (model_name,))
            db.commit()
        finally:
            db.close()


# Giá thực tế Google Gemini API (USD per 1,000 tokens)
# Flash 2.0 / 2.5: $0.10 / $0.40 per 1M tokens -> 0.00010 / 0.00040 per 1K
# Flash Lite: $0.075 / $0.30 per 1M tokens -> 0.000075 / 0.00030 per 1K
# Flash 3.x (3.8, 3.7, 3.6, 3.5, 3.0): $0.15 / $0.60 per 1M tokens -> 0.00015 / 0.00060 per 1K
# Pro 2.5 / 3.1: $1.25 / $5.00 per 1M tokens -> 0.00125 / 0.0050 per 1K
DEFAULT_MODEL_PRICES: list[tuple[str, float, float, str]] = [
    # Flash 3.x series
    ("gemini-3.8-flash", 0.00015, 0.0006, "gemini-3.8-flash"),
    ("gemini-flash-38", 0.00015, 0.0006, "gemini-3.8-flash"),
    ("gemini-3.7-flash", 0.00015, 0.0006, "gemini-3.7-flash"),
    ("gemini-flash-37", 0.00015, 0.0006, "gemini-3.7-flash"),
    ("gemini-3.6-flash", 0.00015, 0.0006, "gemini-3.6-flash"),
    ("gemini-flash-36", 0.00015, 0.0006, "gemini-3.6-flash"),
    ("gemini-3.5-flash", 0.00015, 0.0006, "gemini-3.5-flash"),
    ("gemini-flash-35", 0.00015, 0.0006, "gemini-3.5-flash"),
    ("gemini-3-flash-preview", 0.00015, 0.0006, "gemini-3-flash-preview"),
    ("gemini-flash-30", 0.00015, 0.0006, "gemini-3-flash-preview"),
    ("gemini-3.1-flash", 0.00015, 0.0006, "gemini-3.1-flash"),
    ("gemini-flash", 0.00015, 0.0006, "gemini-flash-pool"),
    # Flash Lite series
    ("gemini-3.5-flash-lite", 0.000075, 0.0003, "gemini-3.5-flash-lite"),
    ("gemini-flash-35-lite", 0.000075, 0.0003, "gemini-3.5-flash-lite"),
    ("gemini-3.1-flash-lite", 0.000075, 0.0003, "gemini-3.1-flash-lite"),
    ("gemini-flash-lite", 0.000075, 0.0003, "gemini-3.1-flash-lite"),
    ("gemini-2.5-flash-lite", 0.000075, 0.0003, "gemini-2.5-flash-lite"),
    ("gemini-flash-25-lite", 0.000075, 0.0003, "gemini-2.5-flash-lite"),
    # Flash 2.x series
    ("gemini-2.5-flash", 0.0001, 0.0004, "gemini-2.5-flash"),
    ("gemini-flash-25", 0.0001, 0.0004, "gemini-2.5-flash"),
    ("gemini-2.0-flash", 0.0001, 0.0004, "gemini-2.0-flash"),
    # Pro series
    ("gemini-2.5-pro", 0.00125, 0.005, "gemini-2.5-pro"),
    ("gemini-3.1-pro", 0.00125, 0.005, "gemini-3.1-pro"),
    # Custom
    ("custom-model", 0.0, 0.0, "custom-model"),
]


def sync_default_model_prices(force: bool = False) -> int:
    """Đồng bộ giá thực tế Google Gemini vào bảng model_prices.
    Nếu force=True, ghi đè toàn bộ giá mặc định (loại bỏ giá ảo cũ).
    Nếu force=False, chỉ chèn các model chưa có trong bảng.
    """
    with _LOCK:
        db = _conn()
        try:
            sql = (
                "INSERT OR REPLACE INTO model_prices (model_name, input_rate_per_1k, output_rate_per_1k, response_model_name) VALUES (?,?,?,?)"
                if force
                else "INSERT OR IGNORE INTO model_prices (model_name, input_rate_per_1k, output_rate_per_1k, response_model_name) VALUES (?,?,?,?)"
            )
            db.executemany(sql, DEFAULT_MODEL_PRICES)
            db.commit()
            return len(DEFAULT_MODEL_PRICES)
        finally:
            db.close()


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

"""Check Router API health + per-key status via proxy (not direct Google calls)."""
import sys
import time
import json
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "check-log-api.txt"
TEST_COUNT = 2


def get_config():
    sys.path.insert(0, str(ROOT / "src"))
    from core.config import config
    return config


def get_accounts():
    sys.path.insert(0, str(ROOT / "src"))
    from backend.accounts import list_accounts_db
    return list_accounts_db()


def get_gemini_keys():
    sys.path.insert(0, str(ROOT / "src"))
    from core.config import config
    return list(config.GEMINI_API_KEYS)


def get_key_status_db_with_retry():
    sys.path.insert(0, str(ROOT / "src"))
    from backend.key_status import get_key_status_db
    for _ in range(3):
        try:
            return get_key_status_db()
        except Exception:
            time.sleep(0.1)
    return {}


def get_penalties():
    """Read in-memory penalty state from rate limiter."""
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from core.gemini_rate_limiter import _score_penalties
        return dict(_score_penalties)
    except Exception:
        return {}


def test_proxy(url: str, auth_key: str) -> list[dict]:
    results = []
    for i in range(TEST_COUNT):
        body = json.dumps({
            "model": "gemini-flash-lite",
            "messages": [{"role": "user", "content": "Say hi in one word."}],
            "max_tokens": 10,
        }).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_key}",
            },
            method="POST",
        )
        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=5) as resp:
                latency = time.time() - t0
            results.append({"ok": True, "latency": latency})
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:120].replace("\n", " ")
            results.append({"ok": False, "code": e.code, "detail": detail})
        except Exception as e:
            results.append({"ok": False, "code": 0, "detail": str(e)[:100]})
        if i < TEST_COUNT - 1:
            time.sleep(2)
    return results


def main():
    cfg = get_config()
    url = f"http://{cfg.HOST}:{cfg.PORT}/v1/chat/completions"

    print("=" * 60)
    print("ROUTER V2 - DIAGNOSTIC & TELEMETRY TOOL")
    print("=" * 60)
    print(f"  Endpoint: {url}\n")

    # Step 1: proxy health check
    accounts = [a for a in get_accounts() if a.get("enabled") and a.get("auth_key")]
    if not accounts:
        print("No enabled accounts. Exiting.")
        sys.exit(1)
    auth_key = accounts[0]["auth_key"]
    print(f"Auth: {accounts[0]['name']} ...{auth_key[-8:]}\n")

    print(f"Proxy test ({TEST_COUNT} requests, 2s gap)...")
    proxy_results = test_proxy(url, auth_key)
    ok = sum(1 for r in proxy_results if r["ok"])
    ok_times = [r["latency"] for r in proxy_results if r["ok"]]
    avg_lat = sum(ok_times) / len(ok_times) if ok_times else 0.0

    for r in proxy_results:
        if r["ok"]:
            print(f"  OK  {r['latency']:.1f}s")
        else:
            print(f"  FAIL {r.get('code',0)}  {r.get('detail','')[:60]}")
    print(f"  Result: {ok}/{TEST_COUNT}  avg {avg_lat:.1f}s\n" if ok_times else f"  Result: {ok}/{TEST_COUNT}\n")

    # Step 2: per-key DB + penalty scan
    print("Per-key status (DB frozen → memory penalty):")
    keys = get_gemini_keys()
    db_status = get_key_status_db_with_retry()
    penalties = get_penalties()

    now = time.time()
    by_status = {"OK": 0, "FROZEN": 0, "PENALIZED": 0, "CLEAN": 0}
    report = []
    for i, k in enumerate(keys, 1):
        var = f"GEMINI_API_KEY_{i}"
        s = db_status.get(k, {})
        frozen_until = s.get("frozen_until", 0) or 0
        usage = s.get("usage", 0) or 0

        pen = penalties.get(k, {})
        pen_expires = pen.get("expires", 0) if isinstance(pen, dict) else 0
        score_red = pen.get("score_reduction", 0) if isinstance(pen, dict) else 0

        if frozen_until >= now:
            st = "FROZEN"
            meta = f"thaw={int(frozen_until - now)}s"
        elif pen_expires >= now and score_red < 0:
            st = "PENALIZED"
            meta = f"score={score_red} ({int(pen_expires - now)}s remain)"
        elif usage > 0:
            st = "OK"
            meta = f"used={usage}"
        else:
            st = "CLEAN"
            meta = "pristine"

        by_status[st] += 1
        line = f"  {var:20s} ...{k[-8:]}  {st:10s}  {meta}"
        print(line)
        report.append(line)

    print()
    print("=" * 60)
    print("SUMMARY METRICS")
    print("=" * 60)
    print(f"  Total Keys : {len(keys)}")
    print(f"  OK         : {by_status['OK']}")
    print(f"  FROZEN     : {by_status['FROZEN']}")
    print(f"  PENALIZED  : {by_status['PENALIZED']}")
    print(f"  CLEAN      : {by_status['CLEAN']}")
    print("=" * 60)

    lines = [
        f"Endpoint: {url}",
        f"Proxy: {ok}/{TEST_COUNT} OK  avg {avg_lat:.1f}s" if ok_times else f"Proxy: {ok}/{TEST_COUNT}",
        "",
        *report,
        "",
        f"OK={by_status['OK']}  FROZEN={by_status['FROZEN']}  PENALIZED={by_status['PENALIZED']}  CLEAN={by_status['CLEAN']}",
    ]
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport: {OUTPUT}")


if __name__ == "__main__":
    main()

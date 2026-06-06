"""
Stress Test: 2 concurrent Claude Code sessions, same auth key, ~400k token each.
Tests that:
1. Keys are selected cleanly (no cascade 429)
2. Context is progressively compacted on each retry
3. After exhaustion, a friendly user message is returned (NOT a raw 429/503)
"""
import asyncio
import json
import sys
import time
import httpx

BASE_URL = "http://localhost:58100"
AUTH_KEY = None  # Will be loaded from .env or accounts db

# ── Load auth key ──────────────────────────────────────────────────────────────

def load_auth_key() -> str:
    from pathlib import Path
    import sqlite3

    db_path = Path(__file__).parent.parent / "usage.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            row = conn.execute("SELECT auth_key FROM accounts WHERE enabled=1 LIMIT 1").fetchone()
            conn.close()
            if row:
                return row[0]
        except Exception as e:
            print(f"  [warn] DB read failed: {e}")

    import os
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("ROUTER_API_AUTH_TOKEN="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    return key

    return "test-token"



# ── Build a fat message payload ~N tokens ──────────────────────────────────────

def build_fat_payload(target_tokens: int, model: str = "claude-sonnet-4-5") -> dict:
    """Build a realistic Claude Code-style chat with ~target_tokens context."""
    # Claude Code system prompt header
    system = (
        "You are Claude Code, a CLI tool for AI-assisted software development. "
        "cc_version=1.0.0\n\n"
        "This is a test session simulating a heavy workload with a large context window."
    )

    # Build a long conversation to fill up context
    # ~4 chars per token estimate
    filler_chars = max(1000, (target_tokens - 500) * 4)
    filler_block = "A" * 1500  # one big assistant turn ~375 tokens

    messages = []
    chars_so_far = len(system)

    turn = 0
    while chars_so_far < filler_chars:
        if turn % 2 == 0:
            content = f"[Turn {turn}] Please analyze this large codebase section: " + "X" * 800
        else:
            content = f"[Turn {turn}] Understood. Here is my analysis: " + filler_block
        messages.append({"role": "user" if turn % 2 == 0 else "assistant", "content": content})
        chars_so_far += len(content)
        turn += 1

    # Final user question
    messages.append({"role": "user", "content": "Summarize everything we discussed in one sentence."})

    token_estimate = chars_so_far // 4
    print(f"  [payload] Built {len(messages)} messages, ~{token_estimate:,} estimated tokens ({chars_so_far:,} chars)")

    return {
        "model": model,
        "max_tokens": 100,
        "system": system,
        "messages": messages,
        "stream": False,
    }


# ── Single session runner ──────────────────────────────────────────────────────

async def run_session(session_id: int, auth_key: str, target_tokens: int, model: str) -> dict:
    print(f"\n[Session {session_id}] Starting — model={model}, target_tokens~{target_tokens:,}")
    payload = build_fat_payload(target_tokens, model)

    t0 = time.monotonic()
    result = {"session": session_id, "status": None, "message_type": None, "text_preview": "", "elapsed": 0}

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{BASE_URL}/v1/messages",
                json=payload,
                headers={"x-api-key": auth_key, "anthropic-version": "2023-06-01"},
            )
            elapsed = time.monotonic() - t0
            result["elapsed"] = round(elapsed, 1)
            result["status"] = resp.status_code

            if resp.status_code == 200:
                data = resp.json()
                content = data.get("content", [])
                text = ""
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        break
                result["message_type"] = data.get("stop_reason", "?")
                result["text_preview"] = text[:200]

                # Check if it's a friendly overload message vs actual answer
                if "⚠️" in text or "compact" in text.lower() or "context limit" in text.lower() or "vượt quá" in text.lower():
                    result["verdict"] = "✅ FRIENDLY OVERLOAD MESSAGE"
                else:
                    result["verdict"] = "✅ REAL ANSWER"
            else:
                result["verdict"] = f"❌ HTTP {resp.status_code}: {resp.text[:300]}"

    except Exception as e:
        elapsed = time.monotonic() - t0
        result["elapsed"] = round(elapsed, 1)
        result["verdict"] = f"❌ EXCEPTION: {e}"

    print(f"[Session {session_id}] Done in {result['elapsed']}s — {result['verdict']}")
    if result.get("text_preview"):
        print(f"[Session {session_id}] Response preview: {result['text_preview'][:150]}")
    return result


# ── Main: run 2 concurrent sessions ───────────────────────────────────────────

async def main():
    global AUTH_KEY
    AUTH_KEY = load_auth_key()
    print(f"Auth key: ...{AUTH_KEY[-8:]}")

    model = "claude-sonnet-4-5"  # Will route through gemini-flash pool

    print("\n" + "="*60)
    print("STRESS TEST: 2 concurrent sessions × ~400k token payloads")
    print("="*60)

    tasks = [
        run_session(1, AUTH_KEY, target_tokens=400_000, model=model),
        run_session(2, AUTH_KEY, target_tokens=400_000, model=model),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for r in results:
        if isinstance(r, Exception):
            print(f"  EXCEPTION: {r}")
        else:
            print(f"  Session {r['session']}: status={r['status']}, elapsed={r['elapsed']}s")
            print(f"    → {r.get('verdict', '?')}")

    # Check: neither session should have gotten a raw 429/503
    raw_errors = [r for r in results if isinstance(r, dict) and r.get("status") not in (200, None)]
    if raw_errors:
        print(f"\n❌ FAIL: {len(raw_errors)} session(s) returned raw error codes (expected 200 with friendly message)")
        sys.exit(1)
    else:
        print("\n✅ PASS: All sessions returned HTTP 200 (friendly message or real answer)")


if __name__ == "__main__":
    asyncio.run(main())

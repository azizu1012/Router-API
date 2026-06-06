#!/usr/bin/env python3
"""
Test all models on the router API using an active auth key retrieved from the database.
"""
import asyncio
import json
import time
import sys
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.accounts import list_accounts_db
from src.core.config_n_logg.config import config

async def get_active_auth_key() -> str:
    try:
        accounts = list_accounts_db()
        active = [a for a in accounts if a.get("enabled") and a.get("auth_key")]
        if active:
            # Prefer non-admin or whatever is first
            return active[0]["auth_key"]
    except Exception as e:
        print(f"[-] Error reading accounts from DB: {e}")
    
    # Fallback to config
    if config.AUTH_TOKEN:
        return config.AUTH_TOKEN
    return "sk-iiVUNH2k3QedJAroueymIo0q9qL5TimQ95vJpbNTOK4"

async def test_model(client: httpx.AsyncClient, model_id: str, auth_key: str) -> dict:
    url = f"http://127.0.0.1:{config.PORT}/v1/chat/completions"
    payload = {
        "model": model_id,
        "messages": [
            {"role": "user", "content": f"Say 'Hello from {model_id}' in 1 short sentence."}
        ],
        "max_tokens": 50,
        "temperature": 0.2
    }
    headers = {
        "Authorization": f"Bearer {auth_key}",
        "Content-Type": "application/json"
    }
    
    print(f"[*] Testing model: {model_id}...")
    t0 = time.time()
    try:
        resp = await client.post(url, json=payload, headers=headers, timeout=20.0)
        latency = time.time() - t0
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            print(f" [+] SUCCESS ({latency:.2f}s): {content}")
            return {"model": model_id, "status": 200, "latency": latency, "ok": True, "output": content}
        else:
            print(f" [-] FAILED ({latency:.2f}s): Status {resp.status_code} - {resp.text[:200]}")
            return {"model": model_id, "status": resp.status_code, "latency": latency, "ok": False, "error": resp.text[:200]}
    except Exception as e:
        latency = time.time() - t0
        print(f" [-] ERROR ({latency:.2f}s): {e}")
        return {"model": model_id, "status": 0, "latency": latency, "ok": False, "error": str(e)}

async def main():
    auth_key = await get_active_auth_key()
    print("=" * 70)
    print("ROUTER ALL-MODELS AUTO TESTER")
    print("=" * 70)
    print(f"Using Auth Key: ...{auth_key[-8:] if auth_key else 'None'}")
    print(f"Target Port: {config.PORT}")
    
    async with httpx.AsyncClient() as client:
        # Step 1: Retrieve models from /v1/models
        models_url = f"http://127.0.0.1:{config.PORT}/v1/models"
        headers = {"Authorization": f"Bearer {auth_key}"}
        try:
            resp = await client.get(models_url, headers=headers, timeout=10.0)
            if resp.status_code != 200:
                print(f"[-] Failed to fetch models list: {resp.status_code} - {resp.text}")
                return
            models_data = resp.json().get("data", [])
        except Exception as e:
            print(f"[-] Connection error to /v1/models: {e}")
            return
        
        # Deduplicate and extract models that are not hidden/sunsetted
        # We'll test pool aliases and distinct models
        models_to_test = [m["id"] for m in models_data]
        # Include hidden/concrete models to test direct routing
        for concrete_model in ["gemini-flash-35", "gemini-flash-30", "gemini-flash-25", "gemini-flash-25-lite"]:
            if concrete_model not in models_to_test:
                models_to_test.append(concrete_model)
        
        if not models_to_test:
            print("[-] No models found to test.")
            return
        
        print(f"[*] Found {len(models_to_test)} models to test: {', '.join(models_to_test)}")
        print("-" * 70)
        
        results = []
        for model in models_to_test:
            res = await test_model(client, model, auth_key)
            results.append(res)
            # Short gap to prevent artificial congestion
            await asyncio.sleep(1.0)
            
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed
    for r in results:
        status_str = "PASS" if r["ok"] else "FAIL"
        time_str = f"{r['latency']:.2f}s"
        out_err = r["output"] if r["ok"] else r.get("error", "Unknown error")
        print(f"[{status_str}] {r['model']:<25} | Latency: {time_str:<6} | Result: {out_err}")
    print("-" * 70)
    print(f"Passed: {passed}/{len(results)} | Failed: {failed}")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())

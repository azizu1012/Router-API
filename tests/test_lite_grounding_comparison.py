import asyncio
import httpx
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

async def get_active_auth_key() -> str:
    from src.backend.accounts import list_accounts_db
    from src.core.config_n_logg.config import config
    try:
        accounts = list_accounts_db()
        active = [a for a in accounts if a.get("enabled") and a.get("auth_key")]
        if active:
            return active[0]["auth_key"]
    except Exception as e:
        print(f"[-] Error reading accounts from DB: {e}")
    if config.AUTH_TOKEN:
        return config.AUTH_TOKEN
    return "sk-iiVUNH2k3QedJAroueymIo0q9qL5TimQ95vJpbNTOK4"

async def test_grounding_path(client: httpx.AsyncClient, url: str, headers: dict, model: str, name: str, query: str):
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": query}
        ],
        "max_tokens": 1024,
        "temperature": 0.2,
        "web_search": True
    }
    
    print(f"\n[*] Testing {name} ({model})...")
    t0 = time.time()
    try:
        resp = await client.post(url, json=payload, headers=headers, timeout=60.0)
        latency = time.time() - t0
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            print(f"[+] SUCCESS in {latency:.2f} seconds!")
            print("-" * 80)
            print(content)
            print("-" * 80)
            return {"ok": True, "latency": latency, "content": content}
        else:
            print(f"[-] FAILED with status {resp.status_code}: {resp.text}")
            return {"ok": False, "error": resp.text}
    except Exception as e:
        print(f"[-] Error: {e}")
        return {"ok": False, "error": str(e)}

async def main():
    auth_key = await get_active_auth_key()
    url = "http://127.0.0.1:58100/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {auth_key}",
        "Content-Type": "application/json"
    }
    
    query = "What is the price of 1 ounce of gold today? Tell me the price and provide the sources."
    
    async with httpx.AsyncClient() as client:
        # 1. Native Grounding (gemini-flash-lite)
        native_res = await test_grounding_path(
            client, url, headers, 
            model="gemini-flash-lite", 
            name="Native Google Grounding (Lite)", 
            query=query
        )
        
        # Gap
        await asyncio.sleep(2.0)
        
        # 2. Hybrid Search (gemini-flash-30)
        hybrid_res = await test_grounding_path(
            client, url, headers, 
            model="gemini-flash-30", 
            name="Server-Side Hybrid Search (DDG + Flash)", 
            query=query
        )
        
        print("\n" + "=" * 80)
        print("COMPARISON SUMMARY")
        print("=" * 80)
        if native_res.get("ok"):
            print(f"- Native Grounding (Lite): {native_res['latency']:.2f}s")
        else:
            print("- Native Grounding (Lite): FAILED")
            
        if hybrid_res.get("ok"):
            print(f"- Hybrid Search (Flash):    {hybrid_res['latency']:.2f}s")
        else:
            print("- Hybrid Search (Flash):    FAILED")
        print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())

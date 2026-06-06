import asyncio
import httpx
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

async def main():
    auth_key = await get_active_auth_key()
    url = "http://127.0.0.1:58100/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {auth_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "gemini-flash-lite", 
        "messages": [
            {"role": "user", "content": "tôi có câu hỏi ko liên quan tới code, yaoguang hsr playable từ khi nài"}
        ],
        "max_tokens": 1024,
        "temperature": 0.2,
        "web_search": True
    }
    
    print("[*] Sending Vietnamese query to gemini-flash-lite...")
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers, timeout=45.0)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            print("=" * 80)
            print(content)
            print("=" * 80)
        else:
            print(f"Error: {resp.text}")

if __name__ == "__main__":
    asyncio.run(main())

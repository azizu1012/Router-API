import asyncio
import httpx
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

async def test_hybrid_search():
    url = "http://127.0.0.1:58100/v1/chat/completions"
    headers = {
        "Authorization": "Bearer sk-iiVUNH2k3QedJAroueymIo0q9qL5TimQ95vJpbNTOK4",
        "Content-Type": "application/json"
    }
    
    # We query gemini-flash-30, which does NOT support native search grounding.
    # Therefore, it MUST trigger server-side DuckDuckGo fallback search.
    payload = {
        "model": "gemini-flash-30",
        "messages": [
            {
                "role": "user",
                "content": "What is the current price of gold today and who won the 2024 US Open tennis men's singles?"
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.2,
        "web_search": True
    }
    
    print("[*] Sending multi-intent query to gemini-flash-30 (hybrid search)...")
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                print("[+] SUCCESS!")
                print("=" * 80)
                print(content)
                print("=" * 80)
                # Check if citations are present (from DuckDuckGo)
                has_sources = "sources:" in content.lower()
                print(f"[+] Contains formatted Sources/Citations: {has_sources}")
            else:
                print(f"[-] FAILED with status {resp.status_code}: {resp.text}")
    except Exception as e:
        import traceback
        print("[-] Exception:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_hybrid_search())

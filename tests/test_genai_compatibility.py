#!/usr/bin/env python3
"""
Verification script for Google GenAI compatibility endpoints.
This script tests standard generation, streaming generation, and search grounding
using the official google-genai library.
"""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from google import genai
from google.genai import types
from src.backend.accounts import list_accounts_db
from src.core.config_n_logg.config import config

async def get_active_auth_key() -> str:
    try:
        accounts = list_accounts_db()
        active = [a for a in accounts if a.get("enabled") and a.get("auth_key")]
        if active:
            return active[0]["auth_key"]
    except Exception as e:
        print(f"[-] Error reading accounts: {e}")
    if config.AUTH_TOKEN:
        return config.AUTH_TOKEN
    return "sk-iiVUNH2k3QedJAroueymIo0q9qL5TimQ95vJpbNTOK4"

async def test_standard_generation(client: genai.Client, model: str):
    print(f"\n[*] Testing standard generation on model: {model}...")
    t0 = time.time()
    try:
        response = client.models.generate_content(
            model=model,
            contents="Say 'Hello World from GenAI!' in exactly 5 words.",
        )
        latency = time.time() - t0
        print(f"  [+] SUCCESS ({latency:.2f}s)")
        print(f"  [+] Response Text: {response.text.strip()}")
        if response.usage_metadata:
            um = response.usage_metadata
            print(f"  [+] Usage: Prompt={um.prompt_token_count}, Candidates={um.candidates_token_count}, Total={um.total_token_count}")
        return True
    except Exception as e:
        print(f"  [-] FAILED: {e}")
        return False

async def test_streaming_generation(client: genai.Client, model: str):
    print(f"\n[*] Testing streaming generation on model: {model}...")
    t0 = time.time()
    try:
        response_stream = client.models.generate_content_stream(
            model=model,
            contents="Tell me a very short 2-sentence story about a lazy cat.",
        )
        print("  [+] Stream started: ", end="", flush=True)
        chunks = []
        for chunk in response_stream:
            print(chunk.text, end="", flush=True)
            chunks.append(chunk.text)
        print()
        latency = time.time() - t0
        print(f"  [+] SUCCESS ({latency:.2f}s)")
        return True
    except Exception as e:
        print(f"\n  [-] FAILED: {e}")
        return False

async def test_grounding_generation(client: genai.Client, model: str):
    print(f"\n[*] Testing grounding search generation on model: {model}...")
    t0 = time.time()
    try:
        # Requesting web search grounding
        response = client.models.generate_content(
            model=model,
            contents="What is the current stock price of Apple Inc. (AAPL) today?",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        latency = time.time() - t0
        print(f"  [+] SUCCESS ({latency:.2f}s)")
        print(f"  [+] Response Text: {response.text.strip()}")
        # Check if the output has citations
        has_citations = "sources:" in response.text.lower() or "source" in response.text.lower()
        print(f"  [+] Citations detected in response text: {has_citations}")
        return True
    except Exception as e:
        print(f"  [-] FAILED: {e}")
        return False

async def main():
    auth_key = await get_active_auth_key()
    print("=" * 80)
    print("GOOGLE GENAI Compatibility Endpoint Verification")
    print("=" * 80)
    print(f"Router Auth Key Prefix: ...{auth_key[-8:]}")
    print(f"Router Port: {config.PORT}")
    
    # Initialize the official Google GenAI Client pointing to our Router
    client = genai.Client(
        api_key=auth_key,
        http_options=types.HttpOptions(
            base_url=f"http://127.0.0.1:{config.PORT}"
        )
    )
    
    # 1. Test Standard Generation
    ok_std = await test_standard_generation(client, "gemini-flash-lite")
    
    # 2. Test Streaming Generation
    ok_stream = await test_streaming_generation(client, "gemini-flash-lite")
    
    # 3. Test Grounding Generation
    # We will target gemini-flash-25 which supports grounding in our router config
    ok_ground = await test_grounding_generation(client, "gemini-flash-25")
    
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print(f"Standard Generation Compatibility:  {'PASS' if ok_std else 'FAIL'}")
    print(f"Streaming Generation Compatibility: {'PASS' if ok_stream else 'FAIL'}")
    print(f"Grounding Search Compatibility:     {'PASS' if ok_ground else 'FAIL'}")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())

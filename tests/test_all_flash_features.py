#!/usr/bin/env python3
"""
Test script to verify image recognition (vision pass-through) and search grounding 
features across all Gemini Flash models in the router configuration.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
import httpx
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.accounts import list_accounts_db
from src.core.config_n_logg.config import config

async def get_active_auth_key() -> str:
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

def load_gemini_keys() -> list:
    keys = []
    # Read keys from .env file
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    key_part, val_part = line.split("=", 1)
                    if key_part.strip().startswith("GEMINI_API_KEY"):
                        keys.append(val_part.strip())
    if not keys and os.getenv("GEMINI_API_KEY"):
        keys.append(os.getenv("GEMINI_API_KEY"))
    return [k for k in keys if k]

async def test_vision_for_model(client: httpx.AsyncClient, model_id: str, auth_key: str) -> dict:
    url = f"http://127.0.0.1:{config.PORT}/v1/chat/completions"
    img_url = "https://www.google.com/images/branding/googlelogo/1x/googlelogo_color_272x92dp.png"
    
    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the main text written in this image?"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": img_url
                        }
                    }
                ]
            }
        ],
        "max_tokens": 100,
        "temperature": 0.2
    }
    
    headers = {
        "Authorization": f"Bearer {auth_key}",
        "Content-Type": "application/json"
    }
    
    print(f"[*] Testing Vision on model: {model_id}...")
    t0 = time.time()
    try:
        resp = await client.post(url, json=payload, headers=headers, timeout=25.0)
        latency = time.time() - t0
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            # Check if "google" is recognized (case-insensitive)
            passed = "google" in content.lower()
            print(f"  [+] SUCCESS ({latency:.2f}s): Output='{content}' (Contains 'google': {passed})")
            return {"model": model_id, "latency": latency, "ok": passed, "output": content, "status": 200}
        else:
            print(f"  [-] FAILED ({latency:.2f}s): Status {resp.status_code} - {resp.text[:200]}")
            return {"model": model_id, "latency": latency, "ok": False, "error": resp.text[:200], "status": resp.status_code}
    except Exception as e:
        latency = time.time() - t0
        print(f"  [-] ERROR ({latency:.2f}s): {e}")
        return {"model": model_id, "latency": latency, "ok": False, "error": str(e), "status": 0}

def test_grounding_direct(api_key: str, model_id: str, prompt: str) -> dict:
    masked_key = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else "***"
    print(f"[*] Testing Search Grounding direct with key {masked_key} on model: {model_id}...")
    t0 = time.time()
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        latency = time.time() - t0
        text = getattr(response, "text", "") or ""
        grounding = getattr(response.candidates[0], "grounding_metadata", None)
        queries = getattr(grounding, "web_search_queries", []) if grounding else []
        chunks = getattr(grounding, "grounding_chunks", []) if grounding else []
        
        has_citations = len(chunks) > 0
        print(f"  [+] SUCCESS ({latency:.2f}s): Text='{text[:80]}...' | Search Queries={queries} | Citations={len(chunks)}")
        return {
            "model": model_id,
            "latency": latency,
            "ok": True,
            "text": text,
            "queries": queries,
            "citations_count": len(chunks),
            "citations": [getattr(getattr(c, "web", None), "uri", "No Link") for c in chunks if getattr(c, "web", None)]
        }
    except Exception as e:
        latency = time.time() - t0
        print(f"  [-] ERROR ({latency:.2f}s): {e}")
        return {"model": model_id, "latency": latency, "ok": False, "error": str(e)}

async def test_grounding_via_router(client: httpx.AsyncClient, model_id: str, auth_key: str) -> dict:
    url = f"http://127.0.0.1:{config.PORT}/v1/chat/completions"
    prompt = "What is the current price of gold per ounce today?"
    
    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.2,
        "web_search": True
    }
    
    headers = {
        "Authorization": f"Bearer {auth_key}",
        "Content-Type": "application/json"
    }
    
    print(f"[*] Testing Router Search Grounding on model: {model_id}...")
    t0 = time.time()
    try:
        resp = await client.post(url, json=payload, headers=headers, timeout=25.0)
        latency = time.time() - t0
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            passed = any(kw in content.lower() for kw in ["gold", "price", "ounce", "dollar", "usd", "$", "market"])
            has_sources = "sources:" in content.lower() or "source" in content.lower()
            print(f"  [+] SUCCESS ({latency:.2f}s): Gold Price Info: {passed} | Has Sources: {has_sources}")
            return {"model": model_id, "latency": latency, "ok": passed, "output": content, "status": 200, "has_sources": has_sources}
        else:
            print(f"  [-] FAILED ({latency:.2f}s): Status {resp.status_code} - {resp.text[:200]}")
            return {"model": model_id, "latency": latency, "ok": False, "error": resp.text[:200], "status": resp.status_code, "has_sources": False}
    except Exception as e:
        latency = time.time() - t0
        print(f"  [-] ERROR ({latency:.2f}s): {e}")
        return {"model": model_id, "latency": latency, "ok": False, "error": str(e), "status": 0, "has_sources": False}

async def main():
    auth_key = await get_active_auth_key()
    print("=" * 80)
    print("FLASH MODELS FEATURES COMPLETE CHECKER (VISION & GROUNDING)")
    print("=" * 80)
    print(f"Router Auth Key: ...{auth_key[-8:] if auth_key else 'None'}")
    print(f"Router Target Port: {config.PORT}")
    
    # 1. Test Vision for all Flash models and pools
    flash_models_to_test = [
        "gemini-flash",
        "gemini-flash-lite",
        "gemini-flash-35",
        "gemini-flash-30",
        "gemini-flash-25",
        "gemini-flash-25-lite"
    ]
    
    print("\n--- PHASE 1: TESTING OPENAI VISION PASS-THROUGH ON ALL FLASH MODELS ---")
    vision_results = []
    async with httpx.AsyncClient() as client:
        for model in flash_models_to_test:
            res = await test_vision_for_model(client, model, auth_key)
            vision_results.append(res)
            await asyncio.sleep(1.0)
            
    # 2. Test Grounding direct using the available Gemini API keys
    print("\n--- PHASE 2: TESTING DIRECT GOOGLE SEARCH GROUNDING ON ALL SUPPORTED FLASH MODELS ---")
    gemini_keys = load_gemini_keys()
    grounding_results = []
    if not gemini_keys:
        print("[-] No Gemini API keys found to test direct Search Grounding.")
    else:
        # We will use the first key to test all potential models
        test_key = gemini_keys[0]
        # Grounding models to test
        grounding_models = [
            "gemini-3.5-flash",
            "gemini-2.5-flash",
            "gemini-3.1-flash-lite"
        ]
        prompt = "Who won the men's singles at the 2024 US Open tennis tournament and what was the score?"
        for model in grounding_models:
            res = test_grounding_direct(test_key, model, prompt)
            grounding_results.append(res)
            time.sleep(1.0)

    # 3. Test Grounding via Router API completions endpoint
    print("\n--- PHASE 3: TESTING GOOGLE SEARCH GROUNDING VIA ROUTER API ---")
    router_grounding_results = []
    async with httpx.AsyncClient() as client:
        # Test models:
        # - gemini-flash-25 (maps to gemini-2.5-flash which supports grounding)
        # - gemini-flash-lite (maps to gemini-3.1-flash-lite which supports grounding)
        # - gemini-flash-35 (maps to gemini-3.5-flash which should NOT support search grounding)
        # - gemini-flash (maps to gemini-3.5-flash which should NOT support search grounding)
        router_models_to_test = [
            "gemini-flash-25",
            "gemini-flash-lite",
            "gemini-flash-35",
            "gemini-flash"
        ]
        for model in router_models_to_test:
            res = await test_grounding_via_router(client, model, auth_key)
            router_grounding_results.append(res)
            await asyncio.sleep(1.0)
            
    print("\n" + "=" * 80)
    print("PHASE 1: VISION TEST SUMMARY")
    print("=" * 80)
    for r in vision_results:
        status_str = "PASS" if r["ok"] else "FAIL"
        print(f"[{status_str}] Model: {r['model']:<25} | Latency: {r['latency']:.2f}s | Output: {r.get('output', r.get('error', ''))}")
        
    print("\n" + "=" * 80)
    print("PHASE 2: DIRECT GROUNDING TEST SUMMARY")
    print("=" * 80)
    for r in grounding_results:
        status_str = "PASS" if r["ok"] else "FAIL"
        if r["ok"]:
            citations_str = f"{r['citations_count']} sources cited"
            print(f"[{status_str}] Model: {r['model']:<25} | Latency: {r['latency']:.2f}s | Citations: {citations_str} | Queries: {r['queries']}")
        else:
            print(f"[{status_str}] Model: {r['model']:<25} | Latency: {r['latency']:.2f}s | Error: {r.get('error', 'Unknown error')}")

    print("\n" + "=" * 80)
    print("PHASE 3: ROUTER GROUNDING TEST SUMMARY")
    print("=" * 80)
    for r in router_grounding_results:
        status_str = "PASS" if r["ok"] else "FAIL"
        sources_str = "Has Citations" if r.get("has_sources") else "No Citations"
        print(f"[{status_str}] Model: {r['model']:<25} | Latency: {r['latency']:.2f}s | {sources_str} | Preview: {r.get('output', r.get('error', ''))[:80]}...")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())

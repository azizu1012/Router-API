"""Test thinking capability of all configured Gemini models.

Usage:
    python tests/test_thinking.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google import genai
from google.genai import types

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


TEST_PROMPT = "What is the sum of the first 30 prime numbers? Show your reasoning."


def _get_gemini_keys():
    keys = []
    for k, v in sorted(os.environ.items()):
        if k.startswith("GEMINI_API_KEY_") and v.strip():
            keys.append(v.strip())
    return keys


def _get_model_ids():
    """Get actual model IDs used by this project."""
    models = {
        "gemini-3.5-flash": "Gemini 3.5 Flash (thinking_level)",
        "gemini-3-flash-preview": "Gemini 3.0 Flash (thinking_level)",
        "gemini-2.5-flash": "Gemini 2.5 Flash (thinking_budget)",
        "gemini-2.5-flash-lite": "Gemini 2.5 Flash Lite (thinking_budget)",
        "gemini-3.1-flash-lite": "Gemini 3.1 Flash Lite (thinking_level)",
    }
    # Allow override from env
    for env_key in ("GEMINI_FLASH_35_MODEL", "GEMINI_FLASH_30_MODEL", "GEMINI_FLASH_25_MODEL",
                    "GEMINI_FLASH_25_LITE_MODEL", "GEMINI_FLASH_LITE_MODEL"):
        val = os.getenv(env_key)
        if val:
            label = env_key.replace("_MODEL", "").replace("_", " ")
            models[val] = f"{label} (from env)"
    return models


async def test_model(client, model_id: str, label: str, key_prefix: str):
    print(f"\n{'='*60}")
    print(f"[{key_prefix}] Testing: {model_id} ({label})")
    print(f"{'='*60}")

    # ── Test 1: Default (no thinking config) ──
    print("\n  1. Default (no thinking config)...", end=" ")
    try:
        resp = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=model_id,
                contents=TEST_PROMPT,
                config=types.GenerateContentConfig(max_output_tokens=500),
            ),
            timeout=30,
        )
        text = resp.text or ""
        has_thought = any(
            getattr(p, "thought", False)
            for c in getattr(resp, "candidates", []) or []
            for p in getattr(getattr(c, "content", None), "parts", []) or []
        )
        print(f"OK (len={len(text)}, thought_part={has_thought})")
    except Exception as e:
        print(f"FAIL: {e}")
        return

    # ── Test 2: With include_thoughts=True ──
    thoughts_key = "thinking_level" if "3." in model_id or "3.5" in model_id or "gemini-3" in model_id else "thinking_budget"
    print(f"\n  2. include_thoughts=True ({thoughts_key})...", end=" ")

    is_v3 = "3." in model_id or "gemini-3" in model_id
    try:
        if is_v3:
            config = types.GenerateContentConfig(
                max_output_tokens=500,
                thinking_config=types.ThinkingConfig(
                    include_thoughts=True,
                    thinking_level="low",
                ),
            )
        else:
            config = types.GenerateContentConfig(
                max_output_tokens=500,
                thinking_config=types.ThinkingConfig(
                    include_thoughts=True,
                    thinking_budget=-1,
                ),
            )

        resp = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=model_id,
                contents=TEST_PROMPT,
                config=config,
            ),
            timeout=60,
        )

        text_parts = []
        thought_parts = []
        for c in getattr(resp, "candidates", []) or []:
            for p in getattr(getattr(c, "content", None), "parts", []) or []:
                txt = getattr(p, "text", "") or ""
                if getattr(p, "thought", False):
                    thought_parts.append(txt)
                elif txt:
                    text_parts.append(txt)

        print(f"OK (thoughts={len(thought_parts)}, text_len={sum(len(t) for t in text_parts)})")
        if thought_parts:
            print(f"    └─ Thought preview: {thought_parts[0][:120]}...")
        else:
            print(f"    └─ ⚠️  No thought parts returned (model may not expose thoughts)")

    except Exception as e:
        print(f"FAIL: {e}")

    # ── Test 3: thinking disabled (budget=0 or level=minimal) ──
    print(f"\n  3. Thinking disabled (budget=0)...", end=" ")
    try:
        if is_v3:
            config = types.GenerateContentConfig(
                max_output_tokens=200,
                thinking_config=types.ThinkingConfig(
                    thinking_level="low",
                ),
            )
        else:
            config = types.GenerateContentConfig(
                max_output_tokens=200,
                thinking_config=types.ThinkingConfig(
                    thinking_budget=0,
                ),
            )

        resp = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=model_id,
                contents="Say 'hello world'",
                config=config,
            ),
            timeout=30,
        )
        print(f"OK (text='{resp.text[:50]}')")
    except Exception as e:
        print(f"FAIL: {e}")

    # ── Test 4: low thinking, no include_thoughts (cheaper) ──
    print(f"\n  4. Low thinking (no thoughts returned)...", end=" ")
    try:
        if is_v3:
            config = types.GenerateContentConfig(
                max_output_tokens=300,
                thinking_config=types.ThinkingConfig(
                    thinking_level="low",
                ),
            )
        else:
            config = types.GenerateContentConfig(
                max_output_tokens=300,
                thinking_config=types.ThinkingConfig(
                    thinking_budget=-1,
                ),
            )

        resp = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=model_id,
                contents=TEST_PROMPT,
                config=config,
            ),
            timeout=60,
        )
        usage = getattr(resp, "usage_metadata", None)
        thought_tokens = getattr(usage, "thoughts_token_count", 0) if usage else 0
        total_tokens = getattr(usage, "total_token_count", 0) if usage else 0
        print(f"OK (text_len={len(resp.text or '')}, thought_tokens={thought_tokens}, total={total_tokens})")
    except Exception as e:
        print(f"FAIL: {e}")


async def main():
    keys = _get_gemini_keys()
    if not keys:
        print("ERROR: No GEMINI_API_KEY_N found in .env")
        sys.exit(1)

    key = keys[0]
    key_prefix = key[-8:]
    print(f"Using key: ...{key_prefix}")
    print(f"Models to test: {len(_get_model_ids())}")

    client = genai.Client(api_key=key, http_options=types.HttpOptions(base_url="https://generativelanguage.googleapis.com"))

    models = _get_model_ids()
    for model_id, label in models.items():
        await test_model(client, model_id, label, key_prefix)


if __name__ == "__main__":
    asyncio.run(main())

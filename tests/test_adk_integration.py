import asyncio
import os
import sys

# Ensure project root is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.providers.gemini.manager import api_manager
from src.core.providers.genai_types import types

async def test_non_streaming():
    print("\n=== Testing Non-Streaming Call ===")
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text="What is the weather in Hanoi right now?")]
        )
    ]
    
    try:
        result = await api_manager.call_gemini(
            model_alias="gemini-2.5-flash",
            system_instruction="You are a helpful assistant.",
            contents=contents,
            max_tokens=500,
            web_search=True,
            account={"web_search_enabled": True}
        )
        print("Call success!")
        print("Model ID used:", result.get("model_id"))
        print("API Key used (suffix):", result.get("api_key")[-8:] if result.get("api_key") else "None")
        print("Input tokens:", result.get("input_tokens"))
        print("Output tokens:", result.get("output_tokens"))
        
        response = result["response"]
        candidates = getattr(response, "candidates", [])
        if candidates and candidates[0].content and candidates[0].content.parts:
            print("Response text:", candidates[0].content.parts[0].text)
            
    except Exception as e:
        print("Error during non-streaming call:", e)


async def test_streaming():
    print("\n=== Testing Streaming Call ===")
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text="Tell me a joke about Google Gemini search grounding.")]
        )
    ]
    
    try:
        stream_generator = api_manager.call_gemini_stream(
            model_alias="gemini-2.5-flash",
            system_instruction="You are a helpful assistant.",
            contents=contents,
            max_tokens=500,
            web_search=True,
            account={"web_search_enabled": True}
        )
        
        async for chunk_data in stream_generator:
            chunk = chunk_data["response_chunk"]
            candidates = chunk.get("candidates") or []
            if candidates:
                parts = candidates[0].get("content", {}).get("parts") or []
                for p in parts:
                    if p.get("text"):
                        print(p["text"], end="", flush=True)
        print("\nStreaming finished successfully!")
    except Exception as e:
        print("Error during streaming call:", e)


async def main():
    # Make sure we use a working key by checking the environment variable or forcing a working one
    # Note: the key pool already has GEMINI_API_KEY_64+ which are verified as working!
    await test_non_streaming()
    await test_streaming()

if __name__ == "__main__":
    asyncio.run(main())

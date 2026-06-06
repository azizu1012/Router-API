import asyncio
import json
import httpx

AUTH_KEY = "sk-iiVUNH2k3QedJAroueymIo0q9qL5TimQ95vJpbNTOK4"
PROXY_URL = "http://localhost:58100/v1/messages"

async def main():
    # 1. Create a large conversation context representing Claude Code.
    # We insert a system prompt that identifies as Claude Code.
    system_prompt = (
        "you are claude code, a helpful assistant. cc_version=0.1.0. "
        "Help the user edit code and answer questions. Keep context clean."
    )
    
    # Generate historical messages to test ~400K tokens.
    large_text = "This is some dummy text to fill up the context limit. " * 15000
    
    messages = [
        {"role": "user", "content": "Here is the codebase context:\n" + large_text},
        {"role": "assistant", "content": "Understood. I have loaded the codebase. How can I help you?"},
        {"role": "user", "content": "Another large context file:\n" + large_text},
        {"role": "assistant", "content": "Got it. I have indexed the second file."},
        {"role": "user", "content": "Say 'COMPACTION SUCCESS' if you can read this message."}
    ]
    
    payload = {
        "model": "gemini-flash",
        "system": system_prompt,
        "messages": messages,
        "max_tokens": 100
    }
    
    headers = {
        "x-api-key": AUTH_KEY,
        "Content-Type": "application/json"
    }
    
    print("Sending large request to Claude Proxy to test Compaction & Truncation...")
    print(f"Total message count: {len(messages)}")
    total_chars = len(system_prompt) + sum(len(m["content"]) for m in messages)
    print(f"Approximate input tokens: {total_chars // 4}")
    
    async with httpx.AsyncClient(timeout=300) as client:
        try:
            response = await client.post(PROXY_URL, json=payload, headers=headers)
            print(f"Status Code: {response.status_code}")
            
            if response.status_code == 200:
                print("Success! Stream response:")
                body = response.text
                text_content = []
                for line in body.strip().split('\n'):
                    if line.startswith('data: '):
                        try:
                            data = json.loads(line[6:])
                            t = data.get('type', '')
                            if t == 'content_block_delta':
                                delta_text = data.get('delta', {}).get('text', '')
                                text_content.append(delta_text)
                                print(delta_text, end='', flush=True)
                            elif t == 'message_start':
                                msg = data.get('message', {})
                                print(f"\n[Start Event: model={msg.get('model')}]")
                            elif t == 'message_delta':
                                print(f"\n[Usage: {data.get('usage')}]")
                        except Exception:
                            pass
                print("\n\nFull Text Output:", "".join(text_content))
            else:
                print(f"Error Response: {response.text}")
        except Exception as e:
            import traceback
            print(f"Request failed: {type(e).__name__} - {e}")
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())

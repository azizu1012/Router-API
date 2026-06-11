import asyncio
import json
import sys
import httpx

def get_auth_key():
    return 'sk-iiVUNH2k3QedJAroueymIo0q9qL5TimQ95vJpbNTOK4'

async def _consume_stream(client, url, json_body, auth_key):
    chunks = []
    full_content = ""
    async with client.stream(
        'POST',
        url,
        json=json_body,
        headers={'Authorization': f'Bearer {auth_key}'},
    ) as response:
        if response.status_code != 200:
            err_body = await response.aread()
            print('Error status:', response.status_code, 'body:', err_body.decode('utf-8', errors='ignore'))
            return response.status_code, chunks, full_content
            
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            if line.startswith('data: '):
                data_str = line[6:]
                if data_str.strip() == '[DONE]':
                    break
                try:
                    data = json.loads(data_str)
                    chunks.append(data)
                    choices = data.get('choices', [])
                    if choices:
                        delta = choices[0].get('delta', {})
                        if 'content' in delta and delta['content']:
                            full_content += delta['content']
                except Exception as e:
                    print(f'Error parsing chunk: {e} | Line: {line}')
    return 200, chunks, full_content

async def test_non_stream(client, auth_key):
    print("\n=== Testing Non-Stream Chat Completion ===")
    try:
        # Note: Server force-enables stream=True for all OpenCode completions
        status, chunks, content = await _consume_stream(
            client,
            'http://127.0.0.1:58100/opencode/v1/chat/completions',
            {
                'model': 'gemini-flash',
                'messages': [{'role': 'user', 'content': 'Say "Hello, this is a non-stream test"'}],
                'temperature': 0.0,
            },
            auth_key
        )
        print('Status:', status)
        if status == 200:
            if chunks:
                print('First Chunk JSON model:', chunks[0].get('model'))
            print('Content:', content)
    except Exception as e:
        print('Exception:', e)

async def test_stream(client, auth_key):
    print("\n=== Testing Stream Chat Completion ===")
    try:
        status, chunks, content = await _consume_stream(
            client,
            'http://127.0.0.1:58100/opencode/v1/chat/completions',
            {
                'model': 'gemini-flash',
                'messages': [{'role': 'user', 'content': 'Count from 1 to 5 slowly with commas.'}],
                'stream': True,
                'temperature': 0.0,
            },
            auth_key
        )
        print('Status:', status)
        if status == 200:
            print('Content:', content)
            # Find and print usage stats from chunks if available
            for chunk in chunks:
                if 'usage' in chunk and chunk['usage']:
                    print(f'[Usage Stats]: {json.dumps(chunk["usage"])}')
                    break
    except Exception as e:
        print('Exception:', e)

async def test_web_search(client, auth_key):
    print("\n=== Testing Web Search Integration ===")
    try:
        status, chunks, content = await _consume_stream(
            client,
            'http://127.0.0.1:58100/opencode/v1/chat/completions',
            {
                'model': 'gemini-flash',
                'messages': [{'role': 'user', 'content': 'What is the current price of Bitcoin today?'}],
                'web_search': True,
                'temperature': 0.0,
            },
            auth_key
        )
        print('Status:', status)
        if status == 200:
            print('Content:', content)
    except Exception as e:
        print('Exception:', e)

async def test_subagent_override(client, auth_key):
    print("\n=== Testing Subagent Override (gemini-flash-lite) ===")
    try:
        status, chunks, content = await _consume_stream(
            client,
            'http://127.0.0.1:58100/opencode/v1/chat/completions',
            {
                'model': 'gemini-flash', # Will be overridden to gemini-flash-lite
                'messages': [
                    {'role': 'system', 'content': 'You are a code search specialist subagent.'},
                    {'role': 'user', 'content': 'Identify yourself and say "Subagent ready".'}
                ],
                'temperature': 0.0,
            },
            auth_key
        )
        print('Status:', status)
        if status == 200:
            models_used = set(chunk.get('model') for chunk in chunks if chunk.get('model'))
            print('Models Used in Response:', list(models_used))
            print('Content:', content)
    except Exception as e:
        import traceback
        traceback.print_exc()

async def main():
    auth_key = get_auth_key()
    print(f"Using auth_key: {auth_key}")

    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        await test_non_stream(client, auth_key)
        await test_stream(client, auth_key)
        await test_web_search(client, auth_key)
        await test_subagent_override(client, auth_key)

if __name__ == '__main__':
    asyncio.run(main())

import asyncio
import json
import sys
import httpx

def get_auth_key():
    try:
        with open('accounts.json', 'r') as f:
            data = json.load(f)
            if data.get('accounts'):
                return data['accounts'][0]['auth_key']
    except Exception as e:
        print(f"Error reading accounts.json: {e}")
    return 'test'

async def test_non_stream(client, auth_key):
    print("\n=== Testing Non-Stream Chat Completion ===")
    try:
        resp = await client.post(
            'http://127.0.0.1:58100/opencode/v1/chat/completions',
            json={
                'model': 'gemini-flash',
                'messages': [{'role': 'user', 'content': 'Say "Hello, this is a non-stream test"'}],
                'temperature': 0.0,
            },
            headers={'Authorization': f'Bearer {auth_key}'},
        )
        print('Status:', resp.status_code)
        if resp.status_code == 200:
            res_json = resp.json()
            print('Response JSON:', json.dumps(res_json, indent=2))
            print('Content:', res_json['choices'][0]['message']['content'])
        else:
            print('Error Response:', resp.text)
    except Exception as e:
        print('Exception:', e)

async def test_stream(client, auth_key):
    print("\n=== Testing Stream Chat Completion ===")
    try:
        async with client.stream(
            'POST',
            'http://127.0.0.1:58100/opencode/v1/chat/completions',
            json={
                'model': 'gemini-flash',
                'messages': [{'role': 'user', 'content': 'Count from 1 to 5 slowly with commas.'}],
                'stream': True,
                'temperature': 0.0,
            },
            headers={'Authorization': f'Bearer {auth_key}'},
        ) as response:
            print('Status:', response.status_code)
            if response.status_code != 200:
                print('Error:', await response.aread())
                return

            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                if line.startswith('data: '):
                    data_str = line[6:]
                    if data_str.strip() == '[DONE]':
                        print('\n[DONE]')
                        break
                    try:
                        data = json.loads(data_str)
                        if 'usage' in data and data['usage']:
                            print(f'\n[Usage Stats]: {json.dumps(data["usage"])}')
                        choices = data.get('choices', [])
                        if choices:
                            delta = choices[0].get('delta', {})
                            if 'content' in delta and delta['content']:
                                sys.stdout.write(delta['content'])
                                sys.stdout.flush()
                    except Exception as e:
                        print(f'\nError parsing chunk: {e} | Line: {line}')
    except Exception as e:
        print('Exception:', e)

async def test_web_search(client, auth_key):
    print("\n=== Testing Web Search Integration ===")
    try:
        resp = await client.post(
            'http://127.0.0.1:58100/opencode/v1/chat/completions',
            json={
                'model': 'gemini-flash',
                'messages': [{'role': 'user', 'content': 'What is the current price of Bitcoin today?'}],
                'web_search': True,
                'temperature': 0.0,
            },
            headers={'Authorization': f'Bearer {auth_key}'},
        )
        print('Status:', resp.status_code)
        if resp.status_code == 200:
            res_json = resp.json()
            print('Response JSON:', json.dumps(res_json, indent=2))
            print('Content:', res_json['choices'][0]['message']['content'])
        else:
            print('Error Response:', resp.text)
    except Exception as e:
        print('Exception:', e)

async def test_subagent_override(client, auth_key):
    print("\n=== Testing Subagent Override (gemini-flash-lite) ===")
    try:
        resp = await client.post(
            'http://127.0.0.1:58100/opencode/v1/chat/completions',
            json={
                'model': 'gemini-flash', # Will be overridden to gemini-flash-lite
                'messages': [
                    {'role': 'system', 'content': 'You are a code search specialist subagent.'},
                    {'role': 'user', 'content': 'Identify yourself and say "Subagent ready".'}
                ],
                'temperature': 0.0,
            },
            headers={'Authorization': f'Bearer {auth_key}'},
        )
        print('Status:', resp.status_code)
        if resp.status_code == 200:
            res_json = resp.json()
            print('Model Used in Response:', res_json.get('model'))
            print('Content:', res_json['choices'][0]['message']['content'])
        else:
            print('Error Response:', resp.text)
    except Exception as e:
        print('Exception:', e)

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

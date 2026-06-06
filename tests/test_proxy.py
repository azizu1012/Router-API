import asyncio, json, sys
import httpx

def get_auth_key():
    try:
        with open('accounts.json', 'r') as f:
            data = json.load(f)
            # Lấy key đầu tiên từ danh sách accounts
            if data['accounts']:
                return data['accounts'][0]['auth_key']
    except Exception as e:
        print(f"Error reading accounts.json: {e}")
    return 'test' # Fallback default

async def test():
    auth_key = get_auth_key()
    print(f"Using auth_key: {auth_key}")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            'http://localhost:58100/v1/messages',
            json={
                'model': 'gemini-flash',
                'max_tokens': 50,
                'messages': [{'role': 'user', 'content': 'Say just "Hello World" and nothing else'}],
            },
            headers={'x-api-key': auth_key},
        )
        body = resp.text
        print('Status:', resp.status_code)
        print('Body preview:', body[:300])
        for line in body.strip().split('\n'):
            if line.startswith('data: '):
                try:
                    data = json.loads(line[6:])
                    t = data.get('type', '')
                    if t == 'content_block_delta':
                        print('CONTENT:', data.get('delta', {}).get('text', ''))
                    elif t == 'message_delta':
                        print('USAGE:', data.get('usage', {}))
                    elif t == 'error':
                        print('ERROR:', json.dumps(data, indent=2)[:500])
                    elif t == 'message_start':
                        msg = data.get('message', {})
                        print('START: model=%s stop_reason=%s' % (msg.get('model'), msg.get('stop_reason')))
                except json.JSONDecodeError:
                    continue

if __name__ == '__main__':
    asyncio.run(test())

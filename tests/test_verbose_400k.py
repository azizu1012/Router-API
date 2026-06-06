"""Verbose concurrent 400k test — prints full response text and real token counts."""
import asyncio, sqlite3, time, httpx

def get_key():
    conn = sqlite3.connect('usage.db')
    row = conn.execute('SELECT auth_key FROM accounts WHERE enabled=1 LIMIT 1').fetchone()
    conn.close()
    return row[0] if row else 'test'

async def call(sid, key, tokens):
    system = 'You are Claude Code, cc_version=1.0.0.'
    msgs = []
    chars = 0
    target = tokens * 4
    i = 0
    while chars < target:
        c = ('Analyze: ' + 'X'*800) if i%2==0 else ('Result: ' + 'A'*1500)
        msgs.append({'role': 'user' if i%2==0 else 'assistant', 'content': c})
        chars += len(c)
        i += 1
    msgs.append({'role':'user','content':'Summarize in one sentence.'})
    print(f'[S{sid}] {len(msgs)} msgs, ~{chars//4:,} est tokens')

    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=300) as cl:
        r = await cl.post(
            'http://localhost:58100/v1/messages',
            json={
                'model': 'claude-sonnet-4-5',
                'max_tokens': 512,
                'system': system,
                'messages': msgs,
                'stream': False,
            },
            headers={'x-api-key': key, 'anthropic-version': '2023-06-01'},
        )
    elapsed = time.monotonic() - t0

    data = r.json()
    text = ''
    for b in data.get('content', []):
        if isinstance(b, dict) and b.get('type') == 'text':
            text = b['text']
            break

    usage = data.get('usage', {})
    stop  = data.get('stop_reason', '?')
    inp   = usage.get('input_tokens', '?')
    out   = usage.get('output_tokens', '?')

    print(f'[S{sid}] HTTP {r.status_code} in {elapsed:.1f}s | stop={stop} | input_tok={inp} | output_tok={out}')
    print(f'[S{sid}] === FULL RESPONSE ===')
    print(repr(text))
    print(f'[S{sid}] === END ===\n')

    # Validate
    is_friendly = any(w in text for w in ['⚠️', 'vượt quá', 'context limit', 'compact', 'WARNING'])
    is_real     = len(text) > 5 and not is_friendly
    if is_friendly:
        print(f'[S{sid}] VERDICT: ✅ FRIENDLY OVERLOAD MESSAGE')
    elif is_real:
        print(f'[S{sid}] VERDICT: ✅ REAL ANSWER (model answered after compaction)')
    else:
        print(f'[S{sid}] VERDICT: ❓ EMPTY OR UNKNOWN ({repr(text[:50])})')

async def main():
    key = get_key()
    print(f'Auth key: ...{key[-8:]}')
    print('='*60)
    await asyncio.gather(call(1, key, 400_000), call(2, key, 400_000))
    print('='*60)
    print('DONE')

asyncio.run(main())

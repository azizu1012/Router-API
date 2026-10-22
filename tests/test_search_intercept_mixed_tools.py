# Mixed parallel tool calls must not produce dangling assistant history.
# When a model turn contains both a router-intercepted tool (WebSearch)
# and a user tool in parallel, drive_search_loop must hand the turn back
# to the client instead of feeding a history where the user call has no
# tool output. That history used to reach strict custom endpoints and fail
# the whole request with 400 No tool output found for function call.
import asyncio
from types import SimpleNamespace
from src.api.opencode_proxy.handler import search_intercept as si
def _msg(calls):
    return SimpleNamespace(tool_calls=calls)
def _call(cid, name):
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments='{}'))
def test_mixed_parallel_returns_to_client_without_dangling_history():
    seen = []
    async def fake_call_once(messages, tools):
        seen.append(list(messages))
        msg = _msg([_call('call_search1', 'WebSearch'), _call('call_user1', 'my_tool')])
        return {'response': SimpleNamespace(choices=[SimpleNamespace(message=msg)])}
    async def main():
        return await si.drive_search_loop(fake_call_once, [{'role': 'user', 'content': 'hi'}], [], {}, None, '')
    result, messages, tools, trace = asyncio.run(main())
    assert len(seen) == 1
    assert trace == []
    assert messages == [{'role': 'user', 'content': 'hi'}]
def test_solo_search_still_loops_and_pairs_output():
    async def fake_run(tc, body, account, prefix):
        return ('search result text', {'type': 'search', 'query': 'q'})
    si.run_intercepted = fake_run
    state = {'n': 0}
    async def fake_call_once(messages, tools):
        state['n'] = state['n'] + 1
        if state['n'] == 1:
            msg = _msg([_call('call_s1', 'WebSearch')])
            return {'response': SimpleNamespace(choices=[SimpleNamespace(message=msg)])}
        return {'response': SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=None, content='done'))])}
    async def main():
        return await si.drive_search_loop(fake_call_once, [{'role': 'user', 'content': 'hi'}], [], {}, None, '')
    try:
        result, messages, tools, trace = asyncio.run(main())
    finally:
        import importlib
        importlib.reload(si)
    assert state['n'] == 2
    assert len(trace) == 1
    roles = [m.get('role') for m in messages]
    assert roles == ['user', 'assistant', 'tool']

import asyncio, json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

async def main():
    from src.api.claude_proxy import claude_proxy
    from src.api.opencode_proxy import opencode_proxy
    from src.core.providers.gemini import caller
    from src.core.router import router

    model = "gemini-flash-35"

    # 1. OpenAI-style request with include_thoughts
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Tổng 30 số nguyên tố đầu tiên là bao nhiêu? Trình bày suy luận."}],
        "max_tokens": 500,
        "thinking_level": "medium",
        "include_thoughts": True,
        "stream": False,
    }
    try:
        from src.server.openai_server.handler import _openai_chat_completion, _completion_response
        result = await _openai_chat_completion(body)
        resp = _completion_response(body, result)
        msg = resp["choices"][0]["message"]
        has_reasoning = "reasoning_content" in msg and msg["reasoning_content"]
        print(f"[OpenAI] content={msg['content'][:80]}...")
        print(f"[OpenAI] reasoning_content={'yes' if has_reasoning else 'no'}")
        if has_reasoning:
            print(f"[OpenAI]  -> preview: {msg['reasoning_content'][:120]}...")
    except Exception as e:
        print(f"[OpenAI] FAIL: {e}")

    # 2. Test with Anthropic thinking format via Claude proxy
    body_anthropic = {
        "model": model,
        "messages": [{"role": "user", "content": "Tổng 30 số nguyên tố đầu tiên là bao nhiêu?"}],
        "max_tokens": 500,
        "thinking": {"type": "enabled", "budget_tokens": 2000},
        "stream": False,
    }
    try:
        result = await claude_proxy.create_message(body_anthropic, "", account=None)
        blocks = result.get("content", [])
        thinking_blocks = [b for b in blocks if b.get("type") == "thinking"]
        print(f"[Claude] content blocks: {len(blocks)}, thinking: {len(thinking_blocks)}")
        if thinking_blocks:
            print(f"[Claude]  -> thinking preview: {thinking_blocks[0].get('thinking', '')[:120]}...")
    except Exception as e:
        print(f"[Claude] FAIL: {e}")

    # 3. Test OpenCode proxy path
    body_opencode = {
        "model": model,
        "messages": [{"role": "user", "content": "Tổng 30 số nguyên tố đầu tiên là bao nhiêu?"}],
        "max_tokens": 500,
        "thinking_level": "medium",
        "include_thoughts": True,
        "stream": False,
    }
    try:
        result = await opencode_proxy.chat_completion(body_opencode, account=None, is_opencode=True)
        msg = result["choices"][0]["message"]
        has_rc = "reasoning_content" in msg and msg["reasoning_content"]
        print(f"[OpenCode] content={msg['content'][:80]}...")
        print(f"[OpenCode] reasoning_content={'yes' if has_rc else 'no'}")
        if has_rc:
            print(f"[OpenCode]  -> preview: {msg['reasoning_content'][:120]}...")
    except Exception as e:
        print(f"[OpenCode] FAIL: {e}")

    # 4. Verify default thinking config builder
    for m in ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-3.5-flash", "gemini-2.5-flash-lite"]:
        tc = caller.build_thinking_config(m)
        print(f"[Default] {m}: thinking_level={getattr(tc, 'thinking_level', '-')}, thinking_budget={getattr(tc, 'thinking_budget', '-')}")

if __name__ == "__main__":
    asyncio.run(main())

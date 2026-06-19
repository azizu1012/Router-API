import asyncio
import secrets

from src.core.providers import _custom_endpoint_manager
from .console_helpers import _prompt_hidden, _prompt_yesno


def _wizard_add_endpoint() -> None:
    """Interactive endpoint addition with auto-verify & account assignment."""
    print("\n  ── Add New Endpoint ──\n")

    try:
        url = input("  URL (e.g. https://openrouter.ai): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.")
        return
    if not url:
        print("  Cancelled.")
        return

    auth_key = _prompt_hidden("  API Key: ")
    if not auth_key:
        print("  Cancelled.")
        return

    try:
        name = input("  Name (leave empty = auto from URL): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.")
        return
    if not name:
        import re as _re
        m = _re.match(r'https?://([^.]+)', url)
        name = m.group(1) if m else f"ep_{secrets.token_hex(3)}"

    print("\n  ▶ Verifying endpoint...")
    try:
        _custom_endpoint_manager.add(name, url, auth_key)
        models = asyncio.run(_custom_endpoint_manager.fetch_models(name))
        print(f"  ✅ Connected! Found {len(models)} models (endpoint: {name}).")
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return

    if not models:
        print("  No models found from this endpoint.")
        return

    # Optional: assign to an account
    from src.backend.accounts import list_accounts_db
    accs = list_accounts_db()
    if accs and _prompt_yesno("\n  Assign to an account", default=True):
        print("\n  Available accounts:")
        for i, a in enumerate(accs, 1):
            print(f"  {i}. {a['name']} ({a['tier']})")
        try:
            line = input("  Choose account (number, 0=skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            line = "0"
        try:
            choice = int(line)
            if 1 <= choice <= len(accs):
                acct = accs[choice - 1]
                _custom_endpoint_manager.assign_to_account(name, acct["account_id"])
                print(f"  ✅ Assigned to {acct['name']}")
        except (ValueError, IndexError):
            print("  Skipped.")


def _list_endpoints() -> None:
    """List all endpoints with their account assignments."""
    eps = _custom_endpoint_manager.list_endpoints()
    if not eps:
        print("  No endpoints configured.")
        return

    from src.backend.accounts import list_accounts_db
    accs = {a.get("account_id"): a for a in list_accounts_db()}

    print("\n  Custom Endpoints:")
    for i, ep in enumerate(eps, 1):
        models_count = len(ep.get("models", []))
        aid = ep.get("account_id", "")
        if aid:
            assigned = accs.get(aid)
            assigned_name = f" → {assigned['name']} ({assigned['tier']})" if assigned else f" → {aid}"
        else:
            assigned_name = " (unassigned)"
        print(f"  {i}. {ep['name']} ({models_count} models){assigned_name}  [{ep['base_url']}]")


def _ping_endpoint() -> None:
    """Test an endpoint by sending a minimal request."""
    eps = _custom_endpoint_manager.list_endpoints()
    if not eps:
        print("  No endpoints configured.")
        return
    print("\n  Endpoints:")
    for i, ep in enumerate(eps, 1):
        print(f"  {i}. {ep['name']}: {ep['base_url']}")
    try:
        line = input("\n  Choose endpoint (number): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("  Cancelled.")
        return
    try:
        choice = int(line)
        ep = eps[choice - 1]
    except (ValueError, IndexError):
        print("  Invalid choice.")
        return

    try:
        model_id = input("  Model ID (leave empty for first available): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("  Cancelled.")
        return
    if not model_id:
        models = ep.get("models", [])
        if models:
            model_id = models[0]
        else:
            print("  No models available.")
            return

    print(f"\n  🏓 Pinging {ep['name']}:{model_id} ...")
    from src.core.providers.gemini_facade import acompletion
    try:
        resp = asyncio.run(acompletion(
            model=model_id,
            messages=[{"role": "user", "content": "Hi, reply OK in 1 word."}],
            api_key=ep["auth_key"],
            api_base=ep["base_url"],
            max_tokens=10,
            temperature=0,
            stream=False,
            request_timeout=15,
        ))
        text = resp.choices[0].message.content if resp.choices else ""
        usage = getattr(resp, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0)
        ct = getattr(usage, "completion_tokens", 0)
        print(f"  ✅ Alive! Response: \"{text.strip()}\"  (in={pt} out={ct})")
    except Exception as e:
        print(f"  ❌ Dead / Error: {e}")

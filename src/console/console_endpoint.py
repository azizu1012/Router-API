import asyncio
import secrets
import sys

from src.core.providers import _custom_endpoint_manager
from .console_helpers import _prompt_hidden, _prompt_yesno, _select_models_interactively


def _wizard_add_endpoint() -> None:
    """Interactive endpoint addition with auto-verify & pool assignment."""
    print("\n  ── Add New Endpoint ──\n")

    url = input("  URL (e.g. https://openrouter.ai): ").strip()
    if not url:
        print("  Cancelled.")
        return

    auth_key = _prompt_hidden("  API Key: ")
    if not auth_key:
        print("  Cancelled.")
        return

    name = input("  Name (leave empty = auto from URL): ").strip()
    if not name:
        import re as _re
        m = _re.match(r'https?://([^.]+)', url)
        name = m.group(1) if m else f"ep_{secrets.token_hex(3)}"

    print(f"\n  ▶ Verifying endpoint...")
    try:
        ep_obj = _custom_endpoint_manager.add(name, url, auth_key)
        models = asyncio.run(_custom_endpoint_manager.fetch_models(name))
        print(f"  ✅ Connected! Found {len(models)} models (endpoint: {name}).")
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return

    if not models:
        print("  No models found from this endpoint.")
        return

    if _prompt_yesno("\n  Add models to pool", default=False):
        chosen = _select_models_interactively(models, "Pick models to assign to pools")
        if not chosen:
            print("  No models selected.")
            return

        pool_assignments: dict = {}
        for mid in chosen:
            pool_raw = input(f"  {mid} → pool (flash/lite/both/skip) [skip]: ").strip().lower()
            if pool_raw in ("flash", "f"):
                pool_assignments[mid] = "gemini-flash"
            elif pool_raw in ("lite", "l"):
                pool_assignments[mid] = "gemini-flash-lite"
            elif pool_raw in ("both", "b"):
                pool_assignments[mid] = "both"

        if pool_assignments:
            print()
            for mid, pn in pool_assignments.items():
                try:
                    if pn == "both":
                        _custom_endpoint_manager.assign_to_pool(name, mid, "gemini-flash")
                        _custom_endpoint_manager.assign_to_pool(name, mid, "gemini-flash-lite")
                        print(f"  ✅ {mid} → flash + lite")
                    else:
                        _custom_endpoint_manager.assign_to_pool(name, mid, pn)
                        display = "flash" if pn == "gemini-flash" else "lite"
                        print(f"  ✅ {mid} → pool {display}")
                except Exception as e:
                    print(f"  ❌ {mid}: {e}")
        else:
            print("  No pool assignments made.")
    else:
        print("  Skipped pool assignment.")


def _wizard_pool_assign() -> None:
    """Interactive pool assignment for existing endpoint models."""
    eps = _custom_endpoint_manager.list_endpoints()
    if not eps:
        print("  No endpoints configured.")
        return

    print("\n  Endpoints:")
    for i, ep in enumerate(eps, 1):
        pool_count = len(ep.get("pool_assignments", {}))
        print(f"  {i}. {ep['name']} ({len(ep.get('models', []))} models, {pool_count} in pool)")
    print()

    try:
        choice = int(input("  Choose endpoint (number): ").strip())
        ep = eps[choice - 1]
    except (ValueError, IndexError):
        print("  Invalid choice.")
        return

    models = ep.get("models", [])
    if not models:
        print(f"  No models in endpoint '{ep['name']}'. Try refreshing first.")
        return

    chosen = _select_models_interactively(models, f"Pick models from '{ep['name']}' to assign")
    if not chosen:
        print("  No models selected.")
        return

    for mid in chosen:
        pool_raw = input(f"  {mid} → pool (flash/lite/both/skip) [skip]: ").strip().lower()
        if pool_raw in ("flash", "f"):
            _custom_endpoint_manager.assign_to_pool(ep["name"], mid, "gemini-flash")
            print(f"  ✅ {mid} → flash")
        elif pool_raw in ("lite", "l"):
            _custom_endpoint_manager.assign_to_pool(ep["name"], mid, "gemini-flash-lite")
            print(f"  ✅ {mid} → lite")
        elif pool_raw in ("both", "b"):
            _custom_endpoint_manager.assign_to_pool(ep["name"], mid, "gemini-flash")
            _custom_endpoint_manager.assign_to_pool(ep["name"], mid, "gemini-flash-lite")
            print(f"  ✅ {mid} → flash + lite")


def _list_pool_assignments() -> None:
    for pool_name in ("gemini-flash", "gemini-flash-lite"):
        display = "flash" if pool_name == "gemini-flash" else "lite"
        items = _custom_endpoint_manager.get_pool_models(pool_name)
        if items:
            print(f"\n  Pool '{display}' ({len(items)} models):")
            for pm in items:
                print(f"    {pm['endpoint_name']}: {pm['model_id']}")
        else:
            print(f"\n  Pool '{display}': (empty)")


def _remove_pool_assignment() -> None:
    name = input("  Endpoint name: ").strip()
    model_id = input("  Model ID: ").strip()
    if not name or not model_id:
        return
    try:
        _custom_endpoint_manager.remove_from_pool(name, model_id)
        print(f"  ✅ Removed {name}:{model_id} from pool")
    except Exception as e:
        print(f"  ❌ Error: {e}")


def _ping_pool_model() -> None:
    """Test a pool-assigned custom model by sending a minimal request."""
    all_models = []
    for pool_name in ("gemini-flash", "gemini-flash-lite"):
        for pm in _custom_endpoint_manager.get_pool_models(pool_name):
            all_models.append(pm)
    if not all_models:
        print("  No pool-assigned models to test.")
        return
    print("\n  Pool models:")
    for i, pm in enumerate(all_models, 1):
        pid = pm.get("pool", "?")
        print(f"  {i}. [{pid}] {pm['endpoint_name']}: {pm['model_id']}")
    try:
        choice = int(input("\n  Choose model (number): ").strip())
        pm = all_models[choice - 1]
    except (ValueError, IndexError):
        print("  Invalid choice.")
        return

    model_id = pm["model_id"]
    ep = pm["endpoint"]
    base_url = ep.get("base_url", "")
    auth_key = ep.get("auth_key", "")

    print(f"\n  🏓 Pinging {pm['endpoint_name']}:{model_id} ...")
    import litellm
    try:
        resp = asyncio.run(litellm.acompletion(
            model=f"openai/{model_id}",
            messages=[{"role": "user", "content": "Hi, reply OK in 1 word."}],
            api_key=auth_key,
            api_base=base_url,
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

import argparse
import cmd
import secrets
import shlex
import webbrowser

from src.core.accounts import account_manager
from src.core.config_n_logg import config
from src.core.providers import _custom_endpoint_manager

from .console_helpers import _print_accounts, _print_defaults, _prompt_yesno
from .console_endpoint import _wizard_add_endpoint, _list_endpoints, _ping_endpoint


# ── CLI main ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Router API account/key console")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create an account and auto-generate auth key")
    create.add_argument("name", nargs="?", default="random")

    list_cmd = sub.add_parser("list", help="List accounts")
    list_cmd.add_argument("--show-keys", action="store_true")
    list_cmd.add_argument("--enabled-only", action="store_true")

    for name in ["enable", "disable", "rotate-key", "delete"]:
        p = sub.add_parser(name, help=f"{name} account")
        p.add_argument("name")

    set_tier = sub.add_parser("set-tier", help="Set account tier (free/premium/admin)")
    set_tier.add_argument("name")
    set_tier.add_argument("tier", choices=["free", "premium", "admin"])

    sub.add_parser("defaults", help="Show default account limits from env")

    sub.add_parser("shell", help="Open interactive console")

    args = parser.parse_args()

    if args.command == "create":
        name = args.name.strip()
        if not name or name.lower() == "random":
            name = f"account_{secrets.token_hex(4)}"
        acct = account_manager.create_account(name)
        print("Created account:")
        _print_accounts([acct], show_keys=True)
    elif args.command == "list":
        _print_accounts(
            account_manager.list_accounts(include_disabled=not args.enabled_only),
            show_keys=args.show_keys,
        )
    elif args.command == "enable":
        acct = account_manager.update_account(args.name, enabled=True)
        _print_accounts([acct], show_keys=False)
    elif args.command == "disable":
        acct = account_manager.update_account(args.name, enabled=False)
        _print_accounts([acct], show_keys=False)
    elif args.command == "rotate-key":
        acct = account_manager.rotate_key(args.name)
        _print_accounts([acct], show_keys=True)
    elif args.command == "delete":
        acct = account_manager.delete_account(args.name)
        print(f"Deleted account: {acct.get('name')}")
    elif args.command == "defaults":
        _print_defaults()
    elif args.command == "shell":
        AccountConsole().cmdloop()


# ── Interactive Console ───────────────────────────────────

_BOX_W = 70

def _pad(text: str) -> str:
    pad = _BOX_W - len(text)
    return text + " " * pad if pad > 0 else text

_INTRO = (
    f"╔{'═' * _BOX_W}╗\n"
    f"║{_pad('ROUTER API INTERACTIVE CONSOLE'.center(_BOX_W))}║\n"
    f"╠{'═' * _BOX_W}╣\n"
    f"║{_pad('  Accounts')}║\n"
    f"║{_pad('    create [name]    — create account (random if empty)')}║\n"
    f"║{_pad('    list             — list accounts (--show-keys / --enabled-only)')}║\n"
    f"║{_pad('    enable/disable/delete/rotate-key <name>')}║\n"
    f"║{_pad('    defaults         — show default rate limits')}║\n"
    f"╠{'═' * _BOX_W}╣\n"
    f"║{_pad('  Endpoints (interactive wizard)')}║\n"
    f"║{_pad('    endpoint         — open endpoint management menu')}║\n"
    f"╠{'═' * _BOX_W}╣\n"
    f"║{_pad('  Other')}║\n"
    f"║{_pad('    dashboard        — open usage dashboard in browser')}║\n"
    f"║{_pad('    exit / quit      — exit')}║\n"
    f"╚{'═' * _BOX_W}╝"
)


class AccountConsole(cmd.Cmd):
    _dashboard_url = f"http://{config.HOST}:{config.PORT}/stats"
    intro = _INTRO
    prompt = "router-api> "

    # ── Account commands ──────────────────────────────────

    def do_create(self, arg: str) -> None:
        name = arg.strip()
        if not name or name.lower() == "random":
            name = f"account_{secrets.token_hex(4)}"
        try:
            acct = account_manager.create_account(name)
            print("Created:")
            _print_accounts([acct], show_keys=True)
        except Exception as e:
            print(f"Error: {e}")

    def do_list(self, arg: str) -> None:
        tokens = shlex.split(arg)
        _print_accounts(
            account_manager.list_accounts(include_disabled="--enabled-only" not in tokens),
            show_keys="--show-keys" in tokens,
        )

    def do_enable(self, arg: str) -> None:
        self._update_enabled(arg, True)

    def do_disable(self, arg: str) -> None:
        self._update_enabled(arg, False)

    def _update_enabled(self, arg: str, enabled: bool) -> None:
        name = arg.strip()
        if not name:
            print("Usage: enable|disable <name>")
            return
        try:
            acct = account_manager.update_account(name, enabled=enabled)
            _print_accounts([acct], show_keys=False)
        except Exception as e:
            print(f"Error: {e}")

    def do_rotate_key(self, arg: str) -> None:
        name = arg.strip()
        if not name:
            print("Usage: rotate-key <name>")
            return
        try:
            acct = account_manager.rotate_key(name)
            _print_accounts([acct], show_keys=True)
        except Exception as e:
            print(f"Error: {e}")

    def do_delete(self, arg: str) -> None:
        name = arg.strip()
        if not name:
            print("Usage: delete <name>")
            return
        try:
            acct = account_manager.delete_account(name)
            print(f"Deleted: {acct.get('name')}")
        except Exception as e:
            print(f"Error: {e}")

    def do_defaults(self, arg: str) -> None:
        _print_defaults()

    def do_set_tier(self, arg: str) -> None:
        parts = shlex.split(arg)
        if len(parts) != 2 or parts[1] not in ("free", "premium", "admin"):
            print("Usage: set-tier <name> <free|premium|admin>")
            return
        try:
            acct = account_manager.set_tier(parts[0], parts[1])
            _print_accounts([acct], show_keys=False)
        except Exception as e:
            print(f"Error: {e}")

    # ── Dashboard ─────────────────────────────────────────

    def do_dashboard(self, arg: str) -> None:
        """Open usage dashboard in browser"""
        url = self._dashboard_url
        print(f"Opening {url} ...")
        webbrowser.open(url)

    # ── Endpoint wizard ───────────────────────────────────

    def do_endpoint(self, arg: str) -> None:
        """Interactive endpoint management"""
        while True:
            print()
            print("  ╔══ Endpoint Management ═══════════════════════╗")
            print("  ║  1. ➕  Add new endpoint                     ║")
            print("  ║  2. 📋  List endpoints                       ║")
            print("  ║  3. ❌  Remove endpoint                      ║")
            print("  ║  4. 🔄  Enable / Disable endpoint            ║")
            print("  ║  5. 🔁  Refresh models                       ║")
            print("  ║  6. 🏓  Test endpoint (ping)                 ║")
            print("  ║  7. 🏊  Pool assignments                     ║")
            print("  ║  8. 🔙  Back                                 ║")
            print("  ╚══════════════════════════════════════════════╝")
            choice = input("  Choose (1-8): ").strip()

            if choice == "1":
                _wizard_add_endpoint()
            elif choice == "2":
                self._list_endpoints()
            elif choice == "3":
                self._remove_endpoint()
            elif choice == "4":
                self._toggle_endpoint()
            elif choice == "5":
                self._refresh_endpoint()
            elif choice == "6":
                _ping_endpoint()
            elif choice == "7":
                self._pool_assignments_menu()
            elif choice in ("8", ""):
                break

    # ── Endpoint sub-functions ────────────────────────────

    @staticmethod
    def _list_endpoints() -> None:
        eps = _custom_endpoint_manager.list_endpoints()
        if not eps:
            print("  No endpoints configured.")
            return
        from src.backend.accounts import list_accounts_db
        accs = {a.get("account_id"): a for a in list_accounts_db()}
        print(f"\n  {'name':20} {'enabled':8} {'models':>6} {'assigned_to':18} {'base_url'}")
        print(f"  {'─' * 95}")
        for ep in eps:
            aid = ep.get("account_id", "")
            if aid:
                assigned = accs.get(aid)
                assigned_name = f"{assigned['name']} ({assigned['tier']})" if assigned else aid
            else:
                assigned_name = "(unassigned)"
            print(
                f"  {ep['name'][:20]:20} "
                f"{'yes' if ep.get('enabled', True) else 'no':8} "
                f"{len(ep.get('models', [])):6} "
                f"{assigned_name[:18]:18} "
                f"{ep.get('base_url', '')}"
            )
        print()

    @staticmethod
    def _remove_endpoint() -> None:
        name = input("  Endpoint name to remove: ").strip()
        if not name:
            return
        if not _prompt_yesno(f"  Remove '{name}'?", default=False):
            return
        ep = _custom_endpoint_manager.remove(name)
        if ep:
            print(f"  ✅ Removed: {ep['name']}")
        else:
            print(f"  ❌ Not found: {name}")

    @staticmethod
    def _toggle_endpoint() -> None:
        name = input("  Endpoint name: ").strip()
        if not name:
            return
        ep = _custom_endpoint_manager.get(name)
        if not ep:
            print(f"  ❌ Not found: {name}")
            return
        enable = ep.get("enabled", True)
        if enable:
            _custom_endpoint_manager.disable(name)
            print(f"  🔴 Disabled: {name}")
        else:
            _custom_endpoint_manager.enable(name)
            print(f"  🟢 Enabled: {name}")

    @staticmethod
    def _refresh_endpoint() -> None:
        name = input("  Endpoint name: ").strip()
        if not name:
            return
        try:
            print(f"  🔁 Fetching models for '{name}'...")
            import asyncio
            models = asyncio.run(_custom_endpoint_manager.fetch_models(name))
            print(f"  ✅ Found {len(models)} models.")
        except Exception as e:
            print(f"  ❌ Error: {e}")

    @staticmethod
    def _endpoint_assignments_menu() -> None:
        while True:
            print()
            print("  ╔══ Endpoint Account Assignments ════════════════╗")
            print("  ║  1. 📋  List all endpoints & assignments        ║")
            print("  ║  2. 🏓  Ping / test endpoint                    ║")
            print("  ║  3. 🔙  Back                                     ║")
            print("  ╚════════════════════════════════════════════════╝")
            choice = input("  Choose (1-3): ").strip()

            if choice == "1":
                _list_endpoints()
            elif choice == "2":
                _ping_endpoint()
            elif choice in ("3", ""):
                break

    @staticmethod
    def _pool_assignments_menu() -> None:
        from src.core.providers import _custom_endpoint_manager
        while True:
            print()
            print("  ╔══ Pool Model Assignments ═══════════════════════╗")
            print("  ║  1. 📋  List all pool assignments                ║")
            print("  ║  2. ➕  Assign model to pool                     ║")
            print("  ║  3. ➖  Remove pool assignment                   ║")
            print("  ║  4. 🔙  Back                                      ║")
            print("  ╚════════════════════════════════════════════════╝")
            choice = input("  Choose (1-4): ").strip()

            if choice == "1":
                eps = _custom_endpoint_manager.list_endpoints()
                for ep in eps:
                    pas = ep.get("pool_assignments", {})
                    if pas:
                        print(f"  {ep['name']}:")
                        for pool, model in pas.items():
                            print(f"    {pool} -> {model}")
                    else:
                        print(f"  {ep['name']}: (no pool assignments)")
            elif choice == "2":
                name = input("  Endpoint name: ").strip()
                pool = input("  Pool name (e.g. gemini-flash): ").strip()
                model = input("  Model ID: ").strip()
                if name and pool and model:
                    _custom_endpoint_manager.assign_pool_model(name, pool, model)
                    print(f"  ✅ Assigned {pool} -> {model} on {name}")
            elif choice == "3":
                name = input("  Endpoint name: ").strip()
                pool = input("  Pool name to remove: ").strip()
                if name and pool:
                    _custom_endpoint_manager.remove_pool_model(name, pool)
                    print(f"  ✅ Removed {pool} from {name}")
            elif choice in ("4", ""):
                break

    # ── Re-show menu after each command ────────────────────

    def postcmd(self, stop: bool, line: str) -> bool:
        if not stop:
            print()
            print(self.intro)
        return stop

    # ── Exit ──────────────────────────────────────────────

    def do_exit(self, arg: str) -> bool:
        return True

    def do_quit(self, arg: str) -> bool:
        return True

    def emptyline(self) -> bool:
        return False


if __name__ == "__main__":
    main()

"""Non-interactive command-line entry points."""

import argparse
import secrets

from src.core.accounts import account_manager
from src.core.config_n_logg import config
from src.core.providers import _custom_endpoint_manager

from .interactive.helpers import _print_accounts, _print_defaults
from .interactive.account_console import AccountConsole


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



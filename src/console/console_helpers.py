import sys
from typing import Any, Dict, Iterable, List

from src.core.config_n_logg import config


def _print_accounts(accounts: Iterable[Dict[str, Any]], show_keys: bool) -> None:
    rows = list(accounts)
    if not rows:
        print("No accounts.")
        return
    key_header = "auth_key" if show_keys else "auth_key_tail"
    print(f"{'name':20} {'enabled':8} {'tier':8} {'rpm':>6} {'tpm':>10} {'rpd':>8} {key_header}")
    print("-" * 100)
    for acct in rows:
        key = str(acct.get("auth_key") or "")
        kd = key if show_keys else (f"...{key[-8:]}" if key else "")
        print(
            f"{str(acct.get('name') or '')[:20]:20} "
            f"{str(bool(acct.get('enabled', True))):8} "
            f"{str(acct.get('tier') or 'free'):8} "
            f"{int(acct.get('rpm') or 0):6d} "
            f"{int(acct.get('tpm') or 0):10d} "
            f"{int(acct.get('rpd') or 0):8d} "
            f"{kd}"
        )


def _print_defaults() -> None:
    print("Default account limits from env:")
    print(f"  ROUTER_API_DEFAULT_ACCOUNT_RPM={config.DEFAULT_ACCOUNT_RPM}")
    print(f"  ROUTER_API_DEFAULT_ACCOUNT_TPM={config.DEFAULT_ACCOUNT_TPM}")
    print(f"  ROUTER_API_DEFAULT_ACCOUNT_RPD={config.DEFAULT_ACCOUNT_RPD}")
    print(f"  ROUTER_API_ACCOUNTS_FILE={config.ACCOUNTS_FILE}")


def _getch() -> str:
    """Get a single keypress. Returns key name."""
    if sys.platform == "win32":
        import msvcrt
        b = msvcrt.getch()
        if b == b'\xe0':
            b2 = msvcrt.getch()
            mapping = {b'H': 'up', b'P': 'down', b'M': 'right', b'K': 'left'}
            return mapping.get(b2, '?')
        try:
            ch = b.decode('utf-8')
        except UnicodeDecodeError:
            ch = '?'
        return ch
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        b = sys.stdin.read(1)
        if b == '\x1b':
            nxt = sys.stdin.read(2)
            mapping = {'[A': 'up', '[B': 'down', '[C': 'right', '[D': 'left'}
            return mapping.get(nxt, '?')
        return b
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _prompt_hidden(prompt: str) -> str:
    """Read a password-style input without echo."""
    if sys.platform == "win32":
        import msvcrt
        print(prompt, end='', flush=True)
        chars = []
        while True:
            b = msvcrt.getch()
            if b == b'\r':
                print()
                break
            if b == b'\x08':
                if chars:
                    chars.pop()
                    print('\b \b', end='', flush=True)
                continue
            ch = b.decode('utf-8', errors='replace')
            chars.append(ch)
            print('*', end='', flush=True)
        return ''.join(chars)
    from getpass import getpass
    return getpass(prompt)


def _prompt_yesno(prompt: str, default: bool = False) -> bool:
    """Ask yes/no question."""
    hint = "Y/n" if default else "y/N"
    while True:
        r = input(f"{prompt} [{hint}]: ").strip().lower()
        if not r:
            return default
        if r in ('y', 'yes'):
            return True
        if r in ('n', 'no'):
            return False


def _select_models_interactively(models: List[str], title: str = "Select models") -> List[str]:
    """Show scrollable model list with arrow / WASD navigation and Space to toggle."""
    if not models:
        return []

    import shutil as _shutil

    selected: set = set()
    idx = 0
    filter_text = ""
    page_size = 12

    while True:
        cols = _shutil.get_terminal_size().columns
        max_w = max(cols - 10, 30)
        trunc = lambda s: s if len(s) <= max_w else s[:max_w - 3] + "..."

        filtered = [m for m in models if filter_text.lower() in m.lower()] if filter_text else models
        if not filtered:
            filtered = models
        if idx >= len(filtered):
            idx = 0

        if sys.platform == "win32":
            import os as _os
            _os.system("cls")
        else:
            import os as _os
            _os.system("clear")

        bar = "─" * min(cols - 2, 60)
        print(f"  ╔══ {title} ══╗")
        print(f"  ║  ↑↓/WS: move | Space: toggle | Enter: done | Q: quit  ║")
        print(f"  ║  Type to filter | BS: clear filter                    ║")
        print(f"  ╚{'═' * (len(bar))}╝")
        print()

        start = max(0, idx - page_size + page_size // 2)
        end = min(len(filtered), start + page_size)
        if end - start < page_size and start > 0:
            start = max(0, end - page_size)

        for i in range(start, end):
            arrow = "▸" if i == idx else " "
            sel = "✓" if filtered[i] in selected else " "
            print(f"  {arrow} [{sel}] {trunc(filtered[i])}")

        if len(filtered) > end:
            print(f"  ... {len(filtered) - end} more")

        print()
        flt_display = filter_text if filter_text else "(type to filter)"
        print(f"  Filter: {flt_display}  |  Selected: {len(selected)}  /  {len(filtered)} models")

        key = _getch()

        if key in ('up', 'w'):
            idx = max(0, idx - 1)
        elif key in ('down', 's'):
            idx = min(len(filtered) - 1, idx + 1)
        elif key == ' ':
            m = filtered[idx]
            if m in selected:
                selected.remove(m)
            else:
                selected.add(m)
        elif key in ('\r', '\n', ''):
            if selected:
                print(f"\n  ✅ Confirmed {len(selected)} model(s).")
                return sorted(selected)
        elif key == 'q' or key == '\x1b':
            print(f"\n  {'Cancelled.' if not selected else f'Selected {len(selected)}.'}")
            return sorted(selected)
        elif key in ('\x08', '\x7f'):
            filter_text = filter_text[:-1]
            idx = 0
        elif key == '/':
            filter_text = ""
            idx = 0
        else:
            if key.isprintable():
                filter_text += key
                idx = 0

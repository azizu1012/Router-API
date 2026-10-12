"""Public CLI entry point for the router administration console."""

from .cli import main
from .interactive.account_console import AccountConsole

__all__ = ["main", "AccountConsole"]

if __name__ == "__main__":
    main()

"""Marker for routes that intentionally return a credential.

Some endpoints have to hand back a secret: an operator reading a key they own,
or recovering a password they set. That is legitimate, and it is exactly the
shape a scanner should flag by default — a bulk listing that leaks is
indistinguishable from a deliberate reveal unless someone says which one it is.

So the intent is declared in code. A route carrying this decorator is exempt from
``credential-in-payload`` in scripts/scan_security.py, and only if it also uses
``_require_admin``: the marker alone is not enough, because "show me the key" is
still privilege escalation if any signed-in user can ask.

Deliberate means one record at a time. A route that takes no name, or returns
credentials for many accounts, should not wear this.
"""

from __future__ import annotations

from typing import Any, Callable


def reveals_credential(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Declare that this handler returns a secret on purpose."""
    fn.__reveals_credential__ = True  # type: ignore[attr-defined]
    return fn
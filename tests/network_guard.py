"""Fail any test that reaches a paid LLM host, and let real ones opt in.

Why this is a guard and not a convention
----------------------------------------
A test that calls `api.anthropic.com` for real is not slow, it is *unreliable
and expensive*: it needs a credential that CI does not have, so it either fails
there or is quietly skipped, and it spends money on every run on a developer
machine. Worse, a test that talks to a real provider asserts against whatever
the provider felt like returning today, so it fails for reasons that have
nothing to do with this repository.

So the rule is enforced where the call has to pass through: the socket. Every
HTTP client in this repo — httpx via the OpenAI/Anthropic SDKs, google-genai,
urllib3 — ends at `socket.socket.connect` or `socket.create_connection`, and
patching those two covers all of them at once. The guard raises before any DNS
lookup or TLS handshake happens, which is what keeps it free.

Three deliberate choices:

* **Blocked, not "any network".** Loopback and everything else stay reachable, so
  local HTTP fixtures keep working. A DuckDuckGo scrape is a real request but it
  costs nothing; the thing worth forbidding is the billed call.
* **Raise *and* record.** The exception is what a test sees when nothing catches
  it. The record is what catches the case that matters more: production retry
  loops swallow exceptions by design, so a blocked call can leave a test green.
  `assert_no_blocked_attempts` runs in the fixture teardown and fails the test on
  anything that got through, swallowed or not.
* **No `pytest.fail` in here.** The check raises `BlockedHostError` and
  `tests/conftest.py` translates it, so this module stays importable (and
  unit-testable) without a pytest dependency.

Opt-in for genuinely live tests: mark them `@pytest.mark.live` and run with
`ROUTER_API_RUN_LIVE_TESTS=1`. Without that variable the tests are skipped and
the guard applies to everything else.
"""

from __future__ import annotations

import os
import socket
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Tuple

__all__ = [
    "LIVE_ENV_VAR",
    "BLOCKED_HOST_SUFFIXES",
    "LOCAL_HOSTS",
    "Attempt",
    "BlockedHostError",
    "BlockedHostInTest",
    "LiveProviderBlocked",
    "assert_no_blocked_attempts",
    "attempts",
    "expected_block",
    "guard_exempt",
    "install",
    "is_installed",
    "is_blocked_host",
    "is_live",
    "is_local_host",
    "live_tests_allowed",
    "reset",
    "skip_unallowed_live",
    "unblocked",
]

LIVE_ENV_VAR = "ROUTER_API_RUN_LIVE_TESTS"

_TRUTHY = frozenset({"1", "true", "yes", "on", "y"})

# Suffix-matched, so a regional endpoint or a new subdomain is covered without
# an edit. `googleapis.com` is the umbrella that catches Vertex/AI Studio; the
# Gemini native endpoint lives under `generativelanguage`.
BLOCKED_HOST_SUFFIXES: Tuple[str, ...] = (
    "anthropic.com",
    "openai.com",
    "generativelanguage.googleapis.com",
    "googleapis.com",
    "aiplatform.googleapis.com",
)

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})


class LiveProviderBlocked(RuntimeError):
    """A socket tried to reach a paid provider host.

    Deliberately not an `OSError`: connection errors are things libraries catch
    and retry, and a guard that can be caught is a guard that can be ignored.
    """


class BlockedHostError(RuntimeError):
    """A test reached a paid host; raised by the teardown check."""


BlockedHostInTest = BlockedHostError


@dataclass(frozen=True)
class Attempt:
    host: str
    port: Any
    entry: str
    frames: Tuple[str, ...] = field(default=())
    expected: bool = False

    def describe(self) -> str:
        where = "\n".join(f"    {f}" for f in self.frames)
        return f"{self.host}:{self.port} via {self.entry}\n{where}"


@dataclass
class _State:
    attempts: list = field(default_factory=list)
    depth: int = 0                 # nested install()/restore() pairs
    originals: Optional[tuple] = None
    expected_depth: int = 0


_STATE = _State()


# ── host predicates ────────────────────────────────────────────────────────

def _normalise(host: str) -> str:
    h = (host or "").strip().lower().rstrip(".")
    if h.startswith("[") and "]" in h:          # [::1]:443 -> ::1
        h = h[1:h.index("]")]
    return h


def is_local_host(host: str) -> bool:
    """True for loopback, so local HTTP fixtures stay reachable."""
    h = _normalise(host)
    return h in LOCAL_HOSTS or h.endswith(".localhost") or h.startswith("127.")


def is_blocked_host(host: str) -> bool:
    h = _normalise(host)
    if not h or is_local_host(h):
        return False
    return any(h == suffix or h.endswith("." + suffix)
               for suffix in BLOCKED_HOST_SUFFIXES)


def host_from_address(address: Any) -> Optional[str]:
    """The host out of a socket address, or None when there is not one.

    AF_INET/AF_INET6 addresses are tuples whose first element is the host. A
    plain string is an AF_UNIX path — local IPC, never a provider — so it is
    deliberately not extracted.
    """
    if isinstance(address, tuple) and address:
        host = address[0]
        if isinstance(host, (bytes, bytearray)):
            host = host.decode("ascii", "ignore")
        return host if isinstance(host, str) else None
    return None


# ── recording ──────────────────────────────────────────────────────────────

def attempts() -> Tuple[Attempt, ...]:
    """Every blocked connection seen since the last `reset()`."""
    return tuple(_STATE.attempts)


def reset() -> None:
    _STATE.attempts.clear()


@contextmanager
def expected_block():
    """Mark the blocks inside this block as the thing under test.

    Without it the teardown check would fail a test whose subject is the guard
    firing — the assertion would succeed and the test would fail anyway.
    """
    _STATE.expected_depth += 1
    try:
        yield
    finally:
        _STATE.expected_depth -= 1


def _record(host: str, port: Any, entry: str) -> Attempt:
    frames = tuple(
        line.strip() for line in traceback.format_stack(limit=8)[:-1]
        if "/tests/" not in line or "test_network_guard" in line
    )[-4:]
    attempt = Attempt(host=host, port=port, entry=entry, frames=frames,
                      expected=_STATE.expected_depth > 0)
    _STATE.attempts.append(attempt)
    return attempt


def unblocked(attempts: Iterable[Attempt], before: int = 0) -> None:
    """Raise if anything at or after `before` was an unexpected block.

    Pure: takes the record rather than reading the global, so the rule can be
    tested without manufacturing a real connection attempt that would then have
    to be cleaned up.
    """
    leaked = [a for a in list(attempts)[before:] if not a.expected]
    if not leaked:
        return
    listed = "\n".join(f"  - {a.describe()}" for a in leaked)
    raise BlockedHostError(
        f"{len(leaked)} connection attempt(s) to a paid provider host during "
        f"this test:\n{listed}\n\nTests must not call a real LLM API. Install a "
        "deterministic provider from tests/fake_provider.py "
        "(text_scenario, json_scenario, stream_scenario, tool_call_scenario, "
        "rate_limit_scenario, ...), use the local search fixture for web search, "
        "or mark the test @pytest.mark.live and run it with "
        f"{LIVE_ENV_VAR}=1."
    )


def assert_no_blocked_attempts(before: int = 0) -> None:
    """`unblocked` over the live record — what the fixture teardown calls.

    Called from teardown, which is where a swallowed exception is still caught:
    the retry loops in `pool_manager` handle provider errors on purpose, so a
    blocked call can otherwise turn a test green.
    """
    unblocked(_STATE.attempts, before)


# ── the live gate ──────────────────────────────────────────────────────────

def live_tests_allowed(env: Optional[dict] = None) -> bool:
    return _normalise((env if env is not None else os.environ).get(LIVE_ENV_VAR, "")) \
        in _TRUTHY


def is_live(item: Any) -> bool:
    """True when the item carries `@pytest.mark.live`."""
    try:
        return item.get_closest_marker("live") is not None
    except AttributeError:
        return False


def guard_exempt(item: Any, env: Optional[dict] = None) -> bool:
    """A live test that was actually opted into is not guarded.

    Both halves are required. The marker alone is not consent — it would be a
    way to disable the guard by decorating a test, which is the opposite of an
    opt-in.
    """
    return is_live(item) and live_tests_allowed(env)


def skip_unallowed_live(items: Iterable[Any], env: Optional[dict] = None) -> int:
    """Skip every `live` test unless `ROUTER_API_RUN_LIVE_TESTS` says otherwise.

    Returns how many were skipped, so the hook can stay silent instead of
    re-describing the whole selection.
    """
    if live_tests_allowed(env):
        return 0
    skipped = 0
    for item in items:
        if is_live(item):
            item.add_marker("skip",
                            reason=f"live test: set {LIVE_ENV_VAR}=1 to run it")
            skipped += 1
    return skipped


# ── installation ───────────────────────────────────────────────────────────

def is_installed() -> bool:
    return _STATE.originals is not None


def install() -> Callable[[], None]:
    """Block paid provider hosts at the socket layer.

    Returns the matching restore callable. Nested calls are counted, so a test
    that installs its own guard cannot tear down the session-wide one.
    """
    if _STATE.depth:
        _STATE.depth += 1

        def _nested_restore() -> None:
            _STATE.depth = max(0, _STATE.depth - 1)

        return _nested_restore

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def _check(address: Any, entry: str) -> None:
        host = host_from_address(address)
        if not is_blocked_host(host):
            return
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else "?"
        _record(host, port, entry)
        raise LiveProviderBlocked(
            f"blocked connection to {host}:{port} via {entry}. Tests must not "
            f"call a real LLM provider — use tests/fake_provider.py. If this test "
            f"is genuinely live, mark it @pytest.mark.live and run with "
            f"{LIVE_ENV_VAR}=1."
        ) from None

    def guarded_connect(self, address, *args, **kwargs):
        _check(address, "socket.socket.connect")
        return original_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):
        _check(address, "socket.socket.connect_ex")
        return original_connect_ex(self, address, *args, **kwargs)

    def guarded_create_connection(address, *args, **kwargs):
        _check(address, "socket.create_connection")
        return original_create_connection(address, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.create_connection = guarded_create_connection

    _STATE.originals = (
        (socket.socket, "connect", original_connect),
        (socket.socket, "connect_ex", original_connect_ex),
        (socket, "create_connection", original_create_connection),
    )
    _STATE.depth = 1

    def restore() -> None:
        if _STATE.depth > 1:
            _STATE.depth -= 1
            return
        for owner, name, value in (_STATE.originals or ()):
            setattr(owner, name, value)
        _STATE.originals = None
        _STATE.depth = 0

    return restore
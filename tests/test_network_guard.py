"""The network guard is only worth anything if it is proven to fire.

A guard nobody has seen fail is indistinguishable from a guard that has silently
stopped working — patching the wrong socket entry point, matching on a host list
that no longer names any real endpoint, or a teardown check that swallows what it
recorded. So this file does three things: it triggers real blocks, it checks that
loopback is untouched, and it exercises the live-test gate that decides who is
allowed to call a provider at all.

None of these tests reach the network. Blocked hosts are caught before DNS, and
the one connection that is allowed is to a socket this file binds itself.
"""

import os
import socket

import pytest

import network_guard as ng


class TestABlockedHostIsRefused:
    def test_connecting_to_a_paid_host_raises(self):
        with ng.expected_block(), pytest.raises(ng.LiveProviderBlocked) as exc:
            socket.create_connection(("api.anthropic.com", 443))

        assert "api.anthropic.com" in str(exc.value)
        assert ng.LIVE_ENV_VAR in str(exc.value), (
            "the message must say how to run a genuinely live test, or the next "
            "person removes the guard instead of using the kit")

    def test_the_socket_entry_point_is_blocked_too(self):
        """`create_connection` is only one of the two doors in.

        An async client (asyncio under httpcore) calls `sock.connect` directly,
        so patching only `create_connection` would leave every streaming path
        unguarded.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with ng.expected_block(), pytest.raises(ng.LiveProviderBlocked):
                sock.connect(("api.openai.com", 443))
            with ng.expected_block(), pytest.raises(ng.LiveProviderBlocked):
                sock.connect_ex(("generativelanguage.googleapis.com", 443))
        finally:
            sock.close()

    def test_every_paid_provider_is_named(self):
        hosts = ["api.anthropic.com",           # Anthropic
                 "anthropic.com",
                 "api.openai.com",               # OpenAI
                 "openai.com",
                 "generativelanguage.googleapis.com",   # Gemini native
                 "aiplatform.googleapis.com",    # Vertex
                 "oauth2.googleapis.com"]        # any googleapis subdomain
        for host in hosts:
            assert ng.is_blocked_host(host), host

    def test_a_subdomain_of_a_paid_provider_is_blocked(self):
        """New regional endpoints ship without touching the list."""
        assert ng.is_blocked_host("eu.api.anthropic.com")
        assert ng.is_blocked_host("generativelanguage.googleapis.com.")

    def test_a_host_that_merely_looks_like_one_is_not_blocked(self):
        for host in ["notanthropic.com", "openai.com.evil.test",
                     "anthropic.com.phish.example"]:
            assert not ng.is_blocked_host(host), host

    def test_the_block_is_not_an_oserror(self):
        """Libraries catch `OSError` and retry; a catchable block is no block."""
        assert not issubclass(ng.LiveProviderBlocked, OSError)


class TestLocalTrafficIsAllowed:
    def test_a_local_socket_still_connects(self):
        """The guard blocks paid hosts, not the network.

        Local HTTP fixtures are how this suite tests servers, so a guard that
        blocked loopback would make half the suite unrunnable.
        """
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            assert client.connect_ex(("127.0.0.1", port)) == 0
        finally:
            client.close()
            listener.close()

    def test_loopback_hosts_are_recognised_as_local(self):
        for host in ("127.0.0.1", "localhost", "::1", "app.localhost", ""):
            assert ng.is_local_host(host), host

    def test_a_local_address_carries_a_host_the_guard_can_read(self):
        assert ng.host_from_address(("127.0.0.1", 8000)) == "127.0.0.1"

    def test_a_unix_socket_path_is_not_a_host(self):
        """A str address is an AF_UNIX path — local IPC, never a provider."""
        assert ng.host_from_address("/tmp/some.sock") is None

    def test_an_ipv6_loopback_is_local_too(self):
        assert ng.is_local_host("[::1]")


class TestBlocksAreRecordedNotJustRaised:
    """The raise is the easy half. The record is what catches a swallowed block.

    `pool_manager` handles provider errors on purpose, so a blocked call can be
    caught, retried, and forgotten — leaving a green test that asserted nothing.
    The teardown check reads this record.

    """

    def test_a_block_appends_to_the_record(self):
        before = len(ng.attempts())
        with ng.expected_block(), pytest.raises(ng.LiveProviderBlocked):
            socket.create_connection(("api.openai.com", 443))

        assert len(ng.attempts()) == before + 1
        assert ng.attempts()[-1].host == "api.openai.com"

    def test_the_record_says_how_the_call_got_there(self):
        with ng.expected_block(), pytest.raises(ng.LiveProviderBlocked):
            socket.create_connection(("api.openai.com", 443))

        frames = "\n".join(ng.attempts()[-1].frames)
        assert "test_network_guard" in frames, frames

    def test_the_teardown_check_fails_on_an_unexpected_block(self):
        """The check itself, without manufacturing a real unguarded call.

        A genuinely unexpected block would also fail this test's own teardown,
        which would hide the thing being checked; the rule is a pure function, so
        it is exercised on a record rather than by leaking one.
        """
        attempt = ng.Attempt(host="api.openai.com", port=443,
                             entry="socket.create_connection",
                             frames=("tests/test_x.py:10 in test_x",))

        with pytest.raises(ng.BlockedHostError) as exc:
            ng.unblocked([attempt])

        message = str(exc.value)
        assert "fake_provider" in message, (
            "the failure must point at the kit, or it reads as a mysterious "
            "network error")
        assert "api.openai.com:443" in message
        assert "test_x.py:10" in message, "the call site is not reported"

    def test_the_teardown_check_passes_when_nothing_was_blocked(self):
        ng.assert_no_blocked_attempts(len(ng.attempts()))      # does not raise
        ng.unblocked([])

    def test_an_expected_block_does_not_fail_the_teardown(self):
        """Otherwise a test of the guard would fail for guarding correctly."""
        mark = len(ng.attempts())
        with ng.expected_block(), pytest.raises(ng.LiveProviderBlocked):
            socket.create_connection(("api.openai.com", 443))

        ng.assert_no_blocked_attempts(mark)

    def test_a_block_before_the_mark_is_not_blamed_on_this_test(self):
        """The check is scoped to one test, not to the whole session."""
        earlier = ng.Attempt(host="api.anthropic.com", port=443,
                             entry="socket.create_connection")
        later = ng.Attempt(host="api.openai.com", port=443,
                           entry="socket.create_connection", expected=True)

        ng.unblocked([earlier, later], before=1)                # no raise
        with pytest.raises(ng.BlockedHostError):
            ng.unblocked([earlier, later], before=0)


class TestTheGuardIsInstalledForEveryTest:
    def test_it_is_installed_by_the_autouse_fixture(self):
        assert ng.is_installed(), (
            "no network guard: a test here that reached a provider would have "
            "passed instead of failing")

    def test_it_tears_down_cleanly(self):
        """Install/restore must be symmetric, or a later test runs unguarded."""
        restore = ng.install()
        restore()
        assert ng.is_installed(), "the autouse guard was removed by a nested one"


class TestTheLiveGate:
    """A test that really calls a provider is opt-in, and the marker alone is
    not the opt-in — otherwise adding a decorator is enough to disable the guard
    for the whole file."""

    class _Item:
        """The two methods `skip_unallowed_live` actually uses."""

        def __init__(self, *names):
            self._names = list(names)

        def get_closest_marker(self, name):
            return name if name in self._names else None

        def add_marker(self, marker, **kwargs):
            self._names.append(str(marker))

    def test_live_tests_are_off_by_default(self):
        assert ng.live_tests_allowed({}) is False
        assert ng.live_tests_allowed({ng.LIVE_ENV_VAR: ""}) is False
        assert ng.live_tests_allowed({ng.LIVE_ENV_VAR: "maybe"}) is False

    def test_the_environment_variable_turns_them_on(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            assert ng.live_tests_allowed({ng.LIVE_ENV_VAR: value}) is True, value

    def test_live_tests_are_skipped_when_not_opted_in(self):
        live, ordinary = self._Item("live"), self._Item()

        assert ng.skip_unallowed_live([live, ordinary], env={}) == 1
        assert live.get_closest_marker("skip") is not None
        assert ordinary.get_closest_marker("skip") is None

    def test_nothing_is_skipped_when_the_run_is_opted_in(self):
        items = [self._Item("live")]

        assert ng.skip_unallowed_live(items, env={ng.LIVE_ENV_VAR: "1"}) == 0
        assert items[0].get_closest_marker("skip") is None

    def test_the_marker_alone_does_not_lift_the_guard(self):
        """A decorator must not be enough to spend money."""
        assert ng.guard_exempt(self._Item("live"), env={}) is False
        assert ng.guard_exempt(self._Item(), env={ng.LIVE_ENV_VAR: "1"}) is False

    def test_a_live_test_in_an_opted_in_run_is_unguarded(self):
        assert ng.guard_exempt(self._Item("live"),
                               env={ng.LIVE_ENV_VAR: "1"}) is True

    def test_a_plain_object_is_not_mistaken_for_a_live_test(self):
        assert ng.is_live(object()) is False


def test_the_environment_really_is_off_by_default():
    """Belt and braces: if a developer exports the variable, this says so."""
    if ng.LIVE_ENV_VAR in os.environ:
        pytest.skip(f"{ng.LIVE_ENV_VAR} is set; live tests are enabled")
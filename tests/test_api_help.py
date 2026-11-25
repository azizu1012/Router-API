"""The API catalog must not describe endpoints that do not exist.

A hand-written catalog rots the moment a route is renamed, and it rots silently:
the dashboard keeps rendering a curl for a 404 and nobody finds out until a user
copies it. So the first test here compares every path in the catalog against the
app's own route table.

The generated /openapi.json cannot serve this purpose. The handlers parse
`request: Request` rather than declaring Pydantic models, so every generated
schema shows two nullable string headers and no request body at all — it lists
that /v1/messages exists without saying what to send it.

Two contracts have their own class here because both have already broken once in
theory: the examples must point at the origin the reader is on (not at a
hardcoded localhost), and every string must exist in all three languages the
dashboard ships.
"""

import json
import re

import pytest

from src.server.openai_server.api_help import (
    CATALOG,
    LANGS,
    NOTES,
    build_catalog,
    help_payload,
)

ORIGIN = "https://api.example.com"


@pytest.fixture(scope="module")
def app_paths():
    from src.server.openai_server.routes.app_init import app
    return set(app.openapi().get("paths", {}))


def _raw_paths():
    for group in CATALOG:
        for ep in group["endpoints"]:
            yield group["id"], ep["path"], ep.get("aliases", [])


def _built_endpoints(origin=ORIGIN, lang="vi"):
    for group in build_catalog(origin, lang):
        for ep in group["endpoints"]:
            yield group["id"], ep


class TestCatalogMatchesTheApp:
    def test_every_catalog_path_is_a_real_route(self, app_paths):
        missing = [path for _, path, _ in _raw_paths() if path not in app_paths]
        assert not missing, f"catalog lists routes the app does not have: {missing}"

    def test_every_alias_is_a_real_route(self, app_paths):
        missing = [
            alias for _, _, aliases in _raw_paths()
            for alias in aliases if alias not in app_paths
        ]
        assert not missing, f"catalog lists aliases the app does not have: {missing}"

    def test_no_route_is_documented_twice(self):
        seen = {}
        dupes = []
        for group_id, path, aliases in _raw_paths():
            for p in (path, *aliases):
                if p in seen:
                    dupes.append(f"{p} in {seen[p]} and {group_id}")
                seen[p] = group_id
        assert not dupes, dupes


class TestEveryEntryIsUsable:
    def test_every_endpoint_has_a_curl(self):
        for _, ep in _built_endpoints():
            assert ep.get("curl"), f"{ep['path']} has no curl"

    def test_every_curl_is_a_runnable_shape(self):
        """Balanced quotes and a real method. A truncated example is worse than none."""
        for _, ep in _built_endpoints():
            curl = ep["curl"]
            assert curl.startswith("curl -X "), f"{ep['path']}: {curl[:40]}"
            assert curl.count("'") % 2 == 0, f"{ep['path']}: unbalanced quotes"
            assert curl.count('"') % 2 == 0, f"{ep['path']}: unbalanced quotes"

    def test_every_endpoint_carries_a_note(self):
        for _, ep in _built_endpoints():
            assert ep.get("note"), f"{ep['path']} has no note"

    def test_every_endpoint_declares_a_method(self):
        for group in CATALOG:
            for ep in group["endpoints"]:
                assert ep["method"] in ("GET", "POST"), ep["method"]

    def test_every_group_has_a_summary(self):
        for group in CATALOG:
            assert group.get("summary", {}).get("vi"), group["id"]
            assert group.get("id"), "group without an id"

    def test_the_responses_group_shows_the_search_mapping(self):
        """The whole point of the mapping is that a reader can see it works."""
        ep = next(ep for gid, ep in _built_endpoints() if gid == "openai-responses")
        assert "web_search" in ep["note"], ep["note"]
        assert ep.get("curl_search"), "no example that actually requests search"
        assert "web_search" in ep["curl_search"]

    def test_the_search_group_says_which_engine_costs_nothing(self):
        notes = [ep["note"] for gid, ep in _built_endpoints() if gid == "search"]
        assert "duckduckgo" in " ".join(notes).lower()

    def test_the_anthropic_group_shows_streaming(self):
        ep = next(ep for gid, ep in _built_endpoints() if gid == "anthropic")
        assert ep.get("curl_stream"), "no streaming example for Anthropic"


class TestTheExamplesFollowTheOrigin:
    """A curl a reader cannot paste is the same as no curl at all."""

    def test_every_curl_uses_the_supplied_origin(self):
        for _, ep in _built_endpoints(ORIGIN):
            examples = [ep["curl"]] + [ep[k] for k in ("curl_stream", "curl_search") if ep.get(k)]
            for curl in examples:
                assert f"'{ORIGIN}" in curl, f"{ep['path']}: {curl.splitlines()[0]}"

    def test_no_example_falls_back_to_localhost(self):
        blob = str(build_catalog(ORIGIN, "en"))
        assert "127.0.0.1" not in blob
        assert "localhost" not in blob

    def test_the_origin_variants_are_driven_by_the_argument(self):
        http_catalog = str(build_catalog("http://box.local:9000", "vi"))
        https_catalog = str(build_catalog("https://box.local", "vi"))
        assert "http://box.local:9000" in http_catalog
        assert "https://box.local" in https_catalog

    @pytest.mark.parametrize("header_value,expected", [
        ("api.example.com", "https://api.example.com"),
        ("api.example.com:8443", "https://api.example.com:8443"),
        # A scheme inside the host header is not a host — reject, don't repair.
        ("https://api.example.com", "https://fallback.example.com"),
        ("evil.example.com\r\nX-Injected: 1", "https://fallback.example.com"),
        ("", "https://fallback.example.com"),
    ])
    def test_public_origin_uses_forwarded_headers_only_when_they_look_like_a_host(
            self, header_value, expected):
        """The host lands inside a shell-quoted example, so it is validated, not escaped."""
        from src.server.openai_server.api_help import public_origin

        class _URL:
            scheme = "http"
            netloc = "fallback.example.com"

        class _Headers(dict):
            pass

        headers = _Headers({"x-forwarded-proto": "https", "x-forwarded-host": header_value})
        request = type("R", (), {"headers": headers, "url": _URL()})()
        assert public_origin(request) == expected

    def test_public_origin_falls_back_to_the_request_itself(self):
        from src.server.openai_server.api_help import public_origin

        class _URL:
            scheme = "https"
            netloc = "router.internal:9443"

        class _Headers(dict):
            pass

        request = type("R", (), {"headers": _Headers({"host": "router.internal:9443"}),
                                 "url": _URL()})()
        assert public_origin(request) == "https://router.internal:9443"


class TestTranslations:
    def test_every_string_exists_in_every_language(self):
        """`t()`-style fallbacks hide a missing translation; this test does not."""
        missing = []

        def walk(node, where):
            if isinstance(node, dict) and set(node) == set(LANGS):
                for lang in LANGS:
                    if not node[lang].strip():
                        missing.append(f"{where}[{lang}]")
            elif isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{where}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{where}[{i}]")

        walk(CATALOG, "CATALOG")
        walk(NOTES, "NOTES")
        assert not missing, missing

    def test_switching_language_switches_the_prose(self):
        vi = build_catalog(ORIGIN, "vi")
        en = build_catalog(ORIGIN, "en")
        ja = build_catalog(ORIGIN, "ja")
        for i, group in enumerate(vi):
            assert group["summary"] != en[i]["summary"]
            assert group["summary"] != ja[i]["summary"]

    def test_the_sample_prompt_follows_the_language(self):
        vi = str(build_catalog(ORIGIN, "vi"))
        en = str(build_catalog(ORIGIN, "en"))
        ja = str(build_catalog(ORIGIN, "ja"))
        assert "__sample__" not in vi and "__sample__" not in en and "__sample__" not in ja
        assert "Xin chào" in vi
        assert "Hello" in en
        assert "こんにちは" in ja

    def test_an_unknown_language_falls_back_to_vietnamese(self):
        assert help_payload(ORIGIN, "kr")["lang"] == "vi"
        assert help_payload(ORIGIN, "kr")["groups"] == help_payload(ORIGIN, "vi")["groups"]


class TestPayload:
    def test_help_payload_has_the_expected_keys(self):
        payload = help_payload()
        assert set(payload) == {"base_url", "lang", "groups", "notes"}
        assert payload["groups"] and payload["notes"]

    def test_help_payload_reports_the_origin_it_used(self):
        assert help_payload(ORIGIN)["base_url"] == ORIGIN

    def test_notes_all_have_a_title_and_body(self):
        for note in NOTES:
            assert note.get("title", {}).get("vi"), note
            assert len(note.get("body", {}).get("vi", "")) > 30, note["title"]["vi"]

    def test_the_payload_is_json_serialisable(self):
        """It is returned straight from a FastAPI route."""
        json.dumps(help_payload(ORIGIN, "ja"))

    def test_the_catalog_does_not_leak_a_real_credential(self):
        """Placeholders only. The secret scanner misses keys without an sk- prefix."""
        blob = str(help_payload(ORIGIN, "vi"))
        assert "<your-token>" in blob
        assert "<your-api-key>" in blob
        # no long opaque strings that could be a pasted key
        for suspicious in re.findall(r"sk-[A-Za-z0-9_\-]{8,}", blob):
            assert "sk-<" in suspicious, f"possible real token in the catalog: {suspicious}"
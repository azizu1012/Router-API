"""The API catalog must not describe endpoints that do not exist.

A hand-written catalog rots the moment a route is renamed, and it rots silently:
the dashboard keeps rendering a curl for a 404 and nobody finds out until a user
copies it. So the first test here compares every path in the catalog against the
app's own route table.

The generated /openapi.json cannot serve this purpose. The handlers parse
`request: Request` rather than declaring Pydantic models, so every generated
schema shows two nullable string headers and no request body at all — it lists
that /v1/messages exists without saying what to send it.
"""

import re

import pytest

from src.server.openai_server.api_help import CATALOG, NOTES, help_payload


@pytest.fixture(scope="module")
def app_paths():
    from src.server.openai_server.routes.app_init import app
    return set(app.openapi().get("paths", {}))


def _catalog_paths():
    for group in CATALOG:
        for ep in group["endpoints"]:
            yield group["id"], ep["path"], ep.get("aliases", [])


class TestCatalogMatchesTheApp:
    def test_every_catalog_path_is_a_real_route(self, app_paths):
        missing = [
            path for _, path, _ in _catalog_paths()
            if path not in app_paths
        ]
        assert not missing, f"catalog lists routes the app does not have: {missing}"

    def test_every_alias_is_a_real_route(self, app_paths):
        missing = [
            alias for _, _, aliases in _catalog_paths()
            for alias in aliases if alias not in app_paths
        ]
        assert not missing, f"catalog lists aliases the app does not have: {missing}"

    def test_no_route_is_documented_twice(self):
        seen = {}
        dupes = []
        for group_id, path, aliases in _catalog_paths():
            for p in (path, *aliases):
                if p in seen:
                    dupes.append(f"{p} in {seen[p]} and {group_id}")
                seen[p] = group_id
        assert not dupes, dupes


class TestEveryEntryIsUsable:
    def test_every_endpoint_has_a_curl(self):
        for group in CATALOG:
            for ep in group["endpoints"]:
                assert ep.get("curl"), f"{ep['path']} has no curl"

    def test_every_curl_is_a_runnable_shape(self):
        """Balanced quotes and a real method. A truncated example is worse than none."""
        for group in CATALOG:
            for ep in group["endpoints"]:
                curl = ep["curl"]
                assert curl.startswith("curl -X "), f"{ep['path']}: {curl[:40]}"
                assert curl.count("'") % 2 == 0, f"{ep['path']}: unbalanced quotes"
                assert curl.count('"') % 2 == 0, f"{ep['path']}: unbalanced quotes"

    def test_every_endpoint_carries_a_note(self):
        for group in CATALOG:
            for ep in group["endpoints"]:
                assert ep.get("note"), f"{ep['path']} has no note"

    def test_every_endpoint_declares_a_method(self):
        for group in CATALOG:
            for ep in group["endpoints"]:
                assert ep["method"] in ("GET", "POST"), ep["method"]

    def test_every_group_has_a_summary(self):
        for group in CATALOG:
            assert group.get("summary"), group["id"]
            assert group.get("id"), "group without an id"

    def test_the_responses_group_shows_the_search_mapping(self):
        """The whole point of the mapping is that a reader can see it works."""
        group = next(g for g in CATALOG if g["id"] == "openai-responses")
        ep = group["endpoints"][0]
        assert "web_search" in ep["note"], ep["note"]
        assert ep.get("curl_search"), "no example that actually requests search"
        assert "web_search" in ep["curl_search"]

    def test_the_search_group_says_which_engine_costs_nothing(self):
        group = next(g for g in CATALOG if g["id"] == "search")
        body = " ".join(e.get("note", "") for e in group["endpoints"])
        assert "duckduckgo" in body.lower()
        assert "google_grounding" in body.lower()

    def test_the_anthropic_group_shows_streaming(self):
        group = next(g for g in CATALOG if g["id"] == "anthropic")
        ep = group["endpoints"][0]
        assert ep.get("curl_stream"), "no streaming example for Anthropic"


class TestPayload:
    def test_help_payload_has_both_keys(self):
        payload = help_payload()
        assert set(payload) == {"groups", "notes"}
        assert payload["groups"] and payload["notes"]

    def test_notes_all_have_a_title_and_body(self):
        for note in NOTES:
            assert note.get("title"), note
            assert len(note.get("body", "")) > 30, note["title"]

    def test_the_payload_is_json_serialisable(self):
        """It is returned straight from a FastAPI route."""
        import json
        json.dumps(help_payload())

    def test_the_catalog_does_not_leak_a_real_credential(self):
        """Placeholders only. The secret scanner misses keys without an sk- prefix."""
        blob = str(help_payload())
        assert "<your-token>" in blob
        assert "<your-api-key>" in blob
        # no long opaque strings that could be a pasted key
        for suspicious in re.findall(r"sk-[A-Za-z0-9_\-]{8,}", blob):
            assert "sk-<" in suspicious, f"possible real token in the catalog: {suspicious}"

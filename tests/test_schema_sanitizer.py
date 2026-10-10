"""JSON Schema sanitising for the Gemini API.

This module had no direct test, and every pass originally ran only at the root
of the schema. A nested node therefore reached the API unsanitised, and Gemini
rejects the whole request with INVALID_ARGUMENT naming a path inside the tool's
own schema::

    GenerateContentRequest.tools[0].function_declarations[3]
      .parameters.properties[query].properties[where].items.items: missing field

That arrived from a real Claude Code session sending 95 tools; the same prompt
with no tools worked, so nothing about the request looked wrong from outside.
"""

import pytest

from src.core.providers.gemini.schema_sanitizer import _sanitize_schema_for_gemini


def clean(schema):
    return _sanitize_schema_for_gemini(schema)


class TestEveryPassRunsAtEveryDepth:
    """The bug: a pass applied at the root never reached anything below it."""

    def test_const_is_converted_in_a_nested_property(self):
        out = clean({"type": "object",
                     "properties": {"q": {"const": "hello"}}})

        assert out["properties"]["q"].get("enum") == ["hello"]
        assert "const" not in out["properties"]["q"]

    def test_const_is_converted_two_levels_down(self):
        out = clean({"type": "object",
                     "properties": {"a": {"type": "object", "properties": {
                         "b": {"const": 7}}}}})

        assert out["properties"]["a"]["properties"]["b"].get("enum") == ["7"]

    def test_object_type_is_inferred_on_a_nested_node(self):
        out = clean({"type": "object",
                     "properties": {"a": {"properties": {"b": {"type": "string"}}}}})

        assert out["properties"]["a"]["type"] == "object"

    def test_a_type_array_is_flattened_inside_properties(self):
        out = clean({"type": "object",
                     "properties": {"a": {"type": ["string", "null"]}}})

        assert out["properties"]["a"]["type"] == "string"

    def test_unsupported_keywords_are_stripped_from_a_child(self):
        out = clean({"type": "object",
                     "properties": {"a": {"type": "string", "const": None,
                                           "x-vendor": "x", "uniqueItems": True}}})

        node = out["properties"]["a"]
        assert "x-vendor" not in node
        assert "uniqueItems" not in node

    def test_required_is_cleaned_inside_a_child(self):
        out = clean({"type": "object",
                     "properties": {"a": {"type": "object",
                                          "properties": {"b": {"type": "string"}},
                                          "required": ["b", "ghost"]}}})

        assert out["properties"]["a"]["required"] == ["b"]


class TestArrayItems:
    """`items` must be one schema object; Gemini reads anything else as empty."""

    def test_a_tuple_form_items_collapses_to_its_first_entry(self):
        """JSON Schema draft-4 allows `"items": [...]`. Gemini does not."""
        out = clean({"type": "object", "properties": {"a": {
            "type": "array",
            "items": [{"type": "string"}, {"type": "number"}]}}})

        assert out["properties"]["a"]["items"] == {"type": "string"}

    def test_an_empty_items_object_gets_a_type(self):
        out = clean({"type": "object", "properties": {"a": {
            "type": "array", "items": {}}}})

        assert out["properties"]["a"]["items"] == {"type": "string"}

    def test_an_empty_items_array_is_dropped(self):
        out = clean({"type": "object", "properties": {"a": {
            "type": "array", "items": []}}})

        assert "items" not in out["properties"]["a"]

    def test_the_reported_shape_is_accepted(self):
        """The exact nesting from the 400: properties -> query -> where -> items."""
        schema = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "object",
                    "properties": {
                        "where": {"type": "array", "items": {}},
                    },
                },
            },
        }

        out = clean(schema)

        node = out["properties"]["query"]["properties"]["where"]
        assert isinstance(node["items"], dict)
        assert node["items"].get("type") == "string"

    def test_a_nested_items_object_survives_when_already_valid(self):
        out = clean({"type": "object", "properties": {"a": {
            "type": "array", "items": {"type": "object",
                                       "properties": {"b": {"type": "string"}}}}}})

        assert out["properties"]["a"]["items"]["type"] == "object"
        assert out["properties"]["a"]["items"]["properties"]["b"]["type"] == "string"


class TestTheInputIsNotMutated:
    def test_the_caller_keeps_its_own_schema(self):
        schema = {"type": "object", "properties": {"a": {"const": "x"}}}
        before = repr(schema)

        clean(schema)

        assert repr(schema) == before


class TestDegenerateInput:
    @pytest.mark.parametrize("bad", [None, "not a dict", 42, []])
    def test_a_non_dict_passes_through(self, bad):
        assert _sanitize_schema_for_gemini(bad) == bad
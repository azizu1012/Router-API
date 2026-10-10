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


class TestUnionsAreFlattenedNotDeleted:
    """anyOf/oneOf carry the type. Deleting them leaves a typeless node.

    google/adk-python#1807: Pydantic writes `Optional[X]` as
    `{"anyOf": [X, {"type": "null"}]}` and the API answers `missing field`.
    """

    def test_any_of_supplies_the_type(self):
        out = clean({"type": "object", "properties": {"path": {
            "anyOf": [{"type": "string"},
                      {"type": "array", "items": {"type": "string"}}]}}})

        node = out["properties"]["path"]
        assert node["type"] == "string"
        assert "anyOf" not in node

    def test_optional_collapses_to_the_non_null_branch(self):
        out = clean({"type": "object", "properties": {"a": {
            "anyOf": [{"type": "integer"}, {"type": "null"}]}}})

        assert out["properties"]["a"]["type"] == "integer"

    def test_one_of_collapses_too(self):
        out = clean({"type": "object", "properties": {"m": {
            "oneOf": [{"type": "string"}, {"type": "integer"}]}}})

        assert out["properties"]["m"]["type"] == "string"
        assert "oneOf" not in out["properties"]["m"]

    def test_an_explicit_type_wins_over_the_union(self):
        out = clean({"type": "object", "properties": {"a": {
            "type": "boolean", "anyOf": [{"type": "string"}]}}})

        assert out["properties"]["a"]["type"] == "boolean"


class TestReferencesDoNotSurviveAsDangling:
    """`definitions` is stripped, so a surviving $ref points at nothing."""

    def test_a_ref_node_still_gets_a_type(self):
        out = clean({"type": "object",
                     "properties": {"a": {"$ref": "#/$defs/Alpha"},
                                    "b": {"type": "string"}},
                     "definitions": {"Alpha": {"type": "object"}}})

        assert "type" in out["properties"]["a"]
        assert "$ref" not in out["properties"]["a"]

    def test_definitions_are_removed(self):
        out = clean({"type": "object",
                     "properties": {"a": {"type": "string"}},
                     "definitions": {"Alpha": {"type": "object"}}})

        assert "definitions" not in out


class TestEveryNodeEndsUpWithAType:
    """The rule the API states in its own 400 body."""

    def test_an_empty_object_becomes_a_string(self):
        out = clean({"type": "object", "properties": {
            "opts": {"type": "object", "properties": {"deep": {}}}}})

        assert out["properties"]["opts"]["properties"]["deep"]["type"] == "string"

    def test_a_description_only_node_becomes_a_string(self):
        out = clean({"type": "object",
                     "properties": {"freeform": {"description": "anything"}}})

        assert out["properties"]["freeform"]["type"] == "string"

    def test_the_properties_map_itself_is_not_given_a_type(self):
        """`properties` maps names to schemas; treating it as a node would
        invent a type on the field called "properties"."""
        out = clean({"type": "object", "properties": {"a": {"type": "string"}}})

        assert "type" not in out["properties"]


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
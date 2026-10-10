"""Gemini JSON Schema compatibility.

Lives under providers/ because it exists solely to satisfy google-genai: every
rule here encodes a limitation of the Gemini Schema model (no const, no union
type, no allOf, ...). Keeping it next to the SDK it targets is what stops
providers/ from having to import a translation layer.

Do not move this back into src/logical_HQ_translator/ for "cohesion". That is
where it started, and the fix in the other direction was to bring it here:
tests/test_layering.py fails if anything under src/core/providers/ reaches up
into the translator or the proxy layer. See docs/bug-logs.md Bug #6.
"""
# JSON Schema keywords NOT supported by google-genai SDK Schema model
# Always remove these before passing to SDK to avoid Pydantic extra_forbidden
UNSUPPORTED_SCHEMA_FIELDS = frozenset({
    "$schema", "$id", "$anchor", "$dynamicRef",
    "definitions", "additionalProperties",
    "propertyNames", "contains", "uniqueItems", "const",
    "if", "then", "else", "not",
    "dependentRequired", "dependentSchemas", "prefixItems",
    "contentMediaType", "contentEncoding",
    "readOnly", "writeOnly", "deprecated",
    "examples",  # plural — SDK chỉ supports singular "example"
    "exclusiveMinimum", "exclusiveMaximum",
    "$comment",
    "allOf",
})
OPENAI_EXTRA_SCHEMA_FIELDS = UNSUPPORTED_SCHEMA_FIELDS


def _convert_const_to_enum(obj: dict) -> None:
    """Convert const to enum (Gemini doesn't support const)."""
    if obj.get("const") is not None and "enum" not in obj:
        obj["enum"] = [obj.pop("const")]


def _convert_enum_values_to_strings(obj: dict) -> None:
    """Gemini requires string enum values + explicit type:string."""
    if "enum" in obj and isinstance(obj["enum"], list):
        obj["enum"] = [str(v) for v in obj["enum"]]
        if "type" not in obj:
            obj["type"] = "string"


def _flatten_type_array(obj: dict) -> None:
    """Flatten e.g. ['string', 'null'] → 'string'."""
    if isinstance(obj.get("type"), list):
        non_null = [t for t in obj["type"] if t != "null"]
        obj["type"] = non_null[0] if non_null else "string"


def _ensure_object_type(obj: dict) -> None:
    """Infer type=object when properties exists (Gemini requirement)."""
    if "properties" in obj and "type" not in obj:
        obj["type"] = "object"


def _clean_required(obj: dict) -> None:
    """Remove required fields not in properties; delete if empty."""
    if "required" in obj and isinstance(obj.get("required"), list) and "properties" in obj:
        valid = [f for f in obj["required"] if f in obj["properties"]]
        if valid:
            obj["required"] = valid
        else:
            del obj["required"]


def _normalize_array_items(obj: dict) -> None:
    """Make ``items`` a single schema node, which is all Gemini accepts.

    JSON Schema also permits the draft-4 tuple form, ``"items": [...]``, and
    clients emit a bare ``"items": {}``. Gemini reads the array as the item
    schema and rejects the whole request with::

        ...items.items: missing field

    An array of schemas collapses to its first entry, since that is the one
    Gemini can represent; an empty one is dropped rather than guessed at.
    """
    items = obj.get("items")
    if isinstance(items, list):
        first = next((i for i in items if isinstance(i, dict)), None)
        if first is None:
            obj.pop("items", None)
        else:
            obj["items"] = first
    elif isinstance(items, dict) and not items:
        # An empty object carries no type, which is the field Gemini says is
        # missing. A string is the least committal placeholder.
        obj["items"] = {"type": "string"}


def _strip_unsupported(obj: dict) -> None:
    """Remove unsupported JSON Schema keywords from one node (mutates in-place)."""
    if not isinstance(obj, dict):
        return
    for k in list(obj.keys()):
        if k in OPENAI_EXTRA_SCHEMA_FIELDS:
            del obj[k]
        elif k.startswith("x-"):
            del obj[k]


def _walk(node, visit) -> None:
    """Apply `visit` to every schema node, depth-first."""
    if isinstance(node, list):
        for item in node:
            _walk(item, visit)
        return
    if not isinstance(node, dict):
        return
    visit(node)
    for value in list(node.values()):
        if isinstance(value, (dict, list)):
            _walk(value, visit)


def _sanitize_schema_for_gemini(schema: dict) -> dict:
    """Clean JSON Schema for Gemini API compatibility. Returns new dict, does NOT mutate input.

    Every pass runs at every depth. Running them only at the root left nested
    schemas untouched, and a tool whose schema nests two arrays deep was then
    rejected by the API with INVALID_ARGUMENT -- a 400 that named a path inside
    the tool's own schema rather than anything the caller could act on.
    """
    if not isinstance(schema, dict):
        return schema

    import copy
    cleaned = copy.deepcopy(schema)

    def _fix(node: dict) -> None:
        _convert_const_to_enum(node)
        _convert_enum_values_to_strings(node)
        _flatten_type_array(node)
        _ensure_object_type(node)
        _strip_unsupported(node)
        _clean_required(node)
        _normalize_array_items(node)

    _walk(cleaned, _fix)

    return cleaned

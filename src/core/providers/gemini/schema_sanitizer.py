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


def _strip_unsupported(obj: dict) -> None:
    """Recursively remove unsupported JSON Schema keywords (mutates in-place)."""
    if not isinstance(obj, dict):
        return
    for k in list(obj.keys()):
        if k in OPENAI_EXTRA_SCHEMA_FIELDS:
            del obj[k]
        elif k.startswith("x-"):
            del obj[k]
        elif isinstance(obj[k], dict):
            _strip_unsupported(obj[k])
        elif isinstance(obj[k], list):
            for item in obj[k]:
                if isinstance(item, dict):
                    _strip_unsupported(item)


def _sanitize_schema_for_gemini(schema: dict) -> dict:
    """Clean JSON Schema for Gemini API compatibility. Returns new dict, does NOT mutate input."""
    if not isinstance(schema, dict):
        return schema

    import copy
    cleaned = copy.deepcopy(schema)

    _convert_const_to_enum(cleaned)
    _convert_enum_values_to_strings(cleaned)
    _flatten_type_array(cleaned)
    _ensure_object_type(cleaned)
    _strip_unsupported(cleaned)
    _clean_required(cleaned)

    return cleaned

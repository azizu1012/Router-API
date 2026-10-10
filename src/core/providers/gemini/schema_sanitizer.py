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
    "allOf", "anyOf", "oneOf", "$ref", "$defs",
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

    JSON Schema also permits the draft-4 tuple form, ``"items": [...]``, a
    boolean schema, ``"items": true``, and clients emit a bare ``"items": {}``.
    Gemini reads the first as an empty item schema and rejects the request with::

        ...items.items: missing field

    An array of schemas collapses to its first entry, since that is the one
    Gemini can represent; an empty one is dropped rather than guessed at. A
    boolean carries no shape to preserve, so it becomes a string.
    """
    items = obj.get("items")
    if isinstance(items, bool):
        obj["items"] = {"type": "string"}
    elif isinstance(items, list):
        first = next((i for i in items if isinstance(i, dict)), None)
        if first is None:
            obj.pop("items", None)
        else:
            obj["items"] = first
    elif isinstance(items, dict) and not items:
        # An empty object carries no type, which is the field Gemini says is
        # missing. A string is the least committal placeholder.
        obj["items"] = {"type": "string"}


def _ensure_array_items(obj: dict) -> None:
    """Give a ``type: array`` node an ``items``, which Gemini treats as required.

    Gemini reports the omission as ``...items.items: missing field``, naming the
    items of the *item*, so the path reads one level deeper than the node that is
    actually at fault. A tool declaring ``where`` as an array of arrays with the
    inner shape unstated -- ``{"type": "array", "items": {"type": "array"}}`` --
    produced exactly that, two properties down.

    Runs last, so it also covers an ``items`` that only became an array after
    ``$ref`` was dropped.
    """
    if obj.get("type") != "array":
        return
    items = obj.get("items")
    if not isinstance(items, dict):
        # A boolean or a dropped $ref carries no shape; {"type": "string"} is the
        # one that always validates.
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


def _flatten_union(obj: dict) -> None:
    """Collapse ``anyOf`` / ``oneOf`` to the branch that carries a type.

    Deleting the keyword outright leaves a node with no ``type``, and Gemini
    answers ``missing field`` -- documented as google/adk-python#1807. A
    ``$ref`` is dropped the same way, but here it points at ``definitions``,
    which the unsupported pass removes, so dropping the ref alone would trade one
    rejection for another.

    Pydantic writes ``Optional[X]`` as ``{"anyOf": [X, {"type": "null"}]}``,
    which is the case that shows up in practice: the null branch is skipped and
    X wins.
    """
    for union in ("anyOf", "oneOf"):
        branches = obj.get(union)
        if not isinstance(branches, list) or not branches:
            continue
        chosen = next(
            (b for b in branches
             if isinstance(b, dict) and "type" in b and b.get("type") != "null"),
            None,
        )
        if chosen is not None:
            for key, value in chosen.items():
                obj.setdefault(key, value)
        obj.pop(union, None)

    obj.pop("$ref", None)

    # A bare `true` / `false` where a schema belongs is legal JSON Schema and
    # meaningless to Gemini, wherever it appears: items, a property, a branch.
    props = obj.get("properties")
    if isinstance(props, dict):
        for name, value in list(props.items()):
            if isinstance(value, bool):
                props[name] = {"type": "string"}
    for key in ("anyOf", "oneOf", "allOf"):
        branches = obj.get(key)
        if isinstance(branches, list):
            obj[key] = [{"type": "string"} if isinstance(b, bool) else b
                         for b in branches]


def _ensure_a_type(obj: dict) -> None:
    """Last resort: a node with nothing to infer a type from gets ``string``.

    Runs after the other passes so it only sees nodes they could not resolve,
    such as ``{}`` or a bare ``{"description": ...}``.
    """
    if "type" not in obj and "properties" not in obj and "enum" not in obj:
        obj["type"] = "string"


def _walk(node, visit) -> None:
    """Apply `visit` to every schema node, depth-first.

    ``properties`` maps a field name to a schema, so the dict itself is not a
    node and its keys must not be treated as one. Walking into it directly is
    how a ``{"description": ...}`` sibling ends up being given a bogus type.
    """
    if isinstance(node, list):
        for item in node:
            _walk(item, visit)
        return
    if not isinstance(node, dict):
        return
    visit(node)
    props = node.get("properties")
    if isinstance(props, dict):
        for sub in props.values():
            _walk(sub, visit)
    for key, value in node.items():
        if key in ("properties", "definitions", "$defs"):
            continue
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
        _flatten_union(node)
        _convert_const_to_enum(node)
        _convert_enum_values_to_strings(node)
        _flatten_type_array(node)
        _ensure_object_type(node)
        _strip_unsupported(node)
        _clean_required(node)
        _normalize_array_items(node)
        _ensure_a_type(node)
        _ensure_array_items(node)

    _walk(cleaned, _fix)

    return cleaned

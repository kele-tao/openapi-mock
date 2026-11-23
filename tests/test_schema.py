"""Schema algebra: allOf merging, unions, complexity and pattern synthesis."""

from __future__ import annotations

import re

from openapi_mock.schema import (
    complexity_score,
    infer_value_type,
    is_cycle_marker,
    merge_all_of,
    pattern_examples,
    resolve_union,
    schema_label,
    schema_type,
)


def test_merge_all_of_combines_properties_and_required() -> None:
    merged = merge_all_of(
        {
            "allOf": [
                {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}},
                {"type": "object", "required": ["b"], "properties": {"b": {"type": "integer"}}},
            ]
        }
    )
    assert set(merged["properties"]) == {"a", "b"}
    assert merged["required"] == ["a", "b"]
    assert merged["type"] == "object"


def test_merge_all_of_intersects_bounds() -> None:
    merged = merge_all_of(
        {
            "allOf": [
                {"type": "integer", "minimum": 5, "maximum": 100},
                {"type": "integer", "minimum": 10, "maximum": 50},
            ]
        }
    )
    assert merged["minimum"] == 10
    assert merged["maximum"] == 50


def test_merge_all_of_intersects_enums() -> None:
    merged = merge_all_of({"allOf": [{"enum": ["a", "b", "c"]}, {"enum": ["b", "c", "d"]}]})
    assert merged["enum"] == ["b", "c"]


def test_merge_all_of_without_branches_is_identity() -> None:
    schema = {"type": "string"}
    assert merge_all_of(schema) == schema


def test_merge_all_of_ignores_cycle_markers() -> None:
    merged = merge_all_of(
        {
            "allOf": [
                {"type": "object", "properties": {"a": {"type": "string"}}},
                {"x-openapi-mock-cycle": "#/components/schemas/Loop"},
            ]
        }
    )
    assert "a" in merged["properties"]


def test_schema_type_prefers_explicit_type() -> None:
    assert schema_type({"type": "integer"}) == "integer"
    assert schema_type({"type": ["string", "null"]}) == "string"
    assert schema_type({"type": ["null"]}) == "null"


def test_schema_type_infers_from_structure() -> None:
    assert schema_type({"properties": {"a": {}}}) == "object"
    assert schema_type({"items": {"type": "string"}}) == "array"
    assert schema_type({"format": "uuid"}) == "string"
    assert schema_type({"minimum": 1}) == "number"
    assert schema_type({"enum": [1, 2]}) == "integer"
    assert schema_type({}) is None


def test_schema_type_of_union_uses_first_branch() -> None:
    assert schema_type({"oneOf": [{"type": "string"}, {"type": "object"}]}) == "string"


def test_complexity_grows_with_structure() -> None:
    simple = {"type": "string"}
    nested = {
        "type": "object",
        "properties": {
            "a": {"type": "string", "pattern": "^x$"},
            "b": {"type": "object", "properties": {"c": {"type": "integer"}}},
        },
    }
    assert complexity_score(nested) > complexity_score(simple) > 0


def test_complexity_of_non_schema_is_zero() -> None:
    assert complexity_score(None) == 0
    assert complexity_score("nope") == 0


def test_schema_label_is_human_readable() -> None:
    assert schema_label({"oneOf": [{}, {}]}) == "oneOf:2"
    assert schema_label({"type": "array", "items": {"type": "string"}}) == "array<string>"
    assert schema_label({"type": "object", "properties": {"a": {}, "b": {}}}) == "object:2"
    assert schema_label({}) == "any"
    assert schema_label({"x-openapi-mock-cycle": "#/x"}) == "recursive"


def test_resolve_union_returns_requested_branch() -> None:
    schema = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    assert resolve_union(schema, 0) == {"type": "string"}
    assert resolve_union(schema, 1) == {"type": "integer"}
    assert resolve_union(schema, 5) == {"type": "integer"}


def test_resolve_union_without_union_returns_none() -> None:
    assert resolve_union({"type": "string"}) is None


def test_pattern_examples_honour_the_pattern() -> None:
    for pattern in (r"^PET-[A-Z]{2}-[0-9]{4}$", r"^TOY-[0-9]{3}$", r"^[a-z]{4}$"):
        examples = pattern_examples(pattern)
        assert examples, pattern
        for example in examples:
            assert re.fullmatch(pattern, example), (pattern, example)


def test_pattern_examples_vary_the_digits() -> None:
    examples = pattern_examples(r"^[0-9]{4}$")
    assert len(set(examples)) > 1


def test_pattern_examples_return_empty_for_unsupported_construct() -> None:
    assert pattern_examples(r"^(foo|bar)+$") == []


def test_infer_value_type_covers_json_types() -> None:
    assert infer_value_type(True) == "boolean"
    assert infer_value_type(1) == "integer"
    assert infer_value_type(1.5) == "number"
    assert infer_value_type("s") == "string"
    assert infer_value_type([]) == "array"
    assert infer_value_type({}) == "object"
    assert infer_value_type(None) == "null"


def test_cycle_marker_detection() -> None:
    assert is_cycle_marker({"x-openapi-mock-cycle": "#/x"})
    assert not is_cycle_marker({"type": "string"})

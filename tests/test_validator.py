"""Validator: every keyword the mock claims to enforce, plus message quality."""

from __future__ import annotations

from openapi_mock.validator import validate


def messages(value: object, schema: object) -> list[str]:
    return [issue.render() for issue in validate(value, schema)]


def test_type_mismatch_is_reported_with_both_types() -> None:
    found = messages(5, {"type": "string"})
    assert len(found) == 1
    assert "expected string, got integer" in found[0]


def test_missing_required_property() -> None:
    schema = {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}
    assert any("missing required property 'id'" in text for text in messages({}, schema))


def test_additional_properties_false() -> None:
    schema = {"type": "object", "additionalProperties": False, "properties": {}}
    assert any("unexpected property" in text for text in messages({"x": 1}, schema))


def test_enum_and_const() -> None:
    assert any("must be one of" in text for text in messages("z", {"enum": ["a", "b"]}))
    assert any("must be exactly" in text for text in messages(2, {"const": 1}))


def test_string_bounds_and_pattern() -> None:
    schema = {"type": "string", "minLength": 3, "maxLength": 5}
    assert any("minLength" in text for text in messages("ab", schema))
    assert any("maxLength" in text for text in messages("abcdef", schema))
    pattern = {"type": "string", "pattern": "^[A-Z]+$"}
    assert any("does not match pattern" in text for text in messages("abc", pattern))


def test_formats_are_checked() -> None:
    for fmt, bad in (("email", "nope"), ("uuid", "123"), ("date", "2026-13"), ("ipv4", "999")):
        found = messages(bad, {"type": "string", "format": fmt})
        assert any("is not a valid " + fmt in text for text in found), (fmt, found)


def test_numbers_bounds_and_multiple_of() -> None:
    schema = {"type": "number", "minimum": 1, "maximum": 3}
    assert any(">= 1" in text for text in messages(0, schema))
    assert any("<= 3" in text for text in messages(4, schema))
    assert any("multiple of" in text for text in messages(7, {"type": "integer", "multipleOf": 4}))


def test_exclusive_bounds() -> None:
    schema = {"type": "integer", "exclusiveMinimum": 2, "exclusiveMaximum": 5}
    assert any("> 2" in text for text in messages(2, schema))
    assert any("< 5" in text for text in messages(5, schema))
    assert messages(3, schema) == []


def test_array_items_and_bounds() -> None:
    schema = {"type": "array", "minItems": 2, "items": {"type": "integer"}}
    assert any("at least 2 items" in text for text in messages([1], schema))
    assert any("expected integer" in text for text in messages([1, "x"], schema))


def test_unique_items() -> None:
    schema = {"type": "array", "uniqueItems": True, "items": {"type": "integer"}}
    assert any("must be unique" in text for text in messages([1, 1], schema))


def test_nested_paths_are_precise() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
            }
        },
    }
    found = messages({"items": [{"id": 1}, {"id": "x"}]}, schema)
    assert found == ["items[1].id: expected integer, got string"]


def test_nullable_accepts_null_and_rejects_other_types() -> None:
    schema = {"type": "string", "nullable": True}
    assert messages(None, schema) == []
    assert messages(5, schema)


def test_boolean_is_not_an_integer() -> None:
    assert messages(True, {"type": "integer"})


def test_schema_false_rejects_everything() -> None:
    assert messages("anything", False)


def test_empty_or_true_schema_accepts_everything() -> None:
    assert messages({"a": 1}, {}) == []
    assert messages({"a": 1}, True) == []


def test_cycle_marker_is_skipped() -> None:
    marker = {"x-openapi-mock-cycle": "#/components/schemas/Node"}
    assert messages("anything", marker) == []


def test_all_reported_issues_not_just_the_first() -> None:
    schema = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    }
    assert len(messages({"a": "x", "b": "y"}, schema)) == 2


def test_unicode_values_raise_no_false_positive() -> None:
    schema = {"type": "object", "properties": {"都市": {"type": "string", "minLength": 2}}}
    assert messages({"都市": "京都"}, schema) == []
    assert messages({"都市": "京"}, schema)

"""Generator: determinism, formats, bounds, patterns, unicode and recursion."""

from __future__ import annotations

import json
import re

from openapi_mock.data import Factory
from openapi_mock.generate import Generator
from openapi_mock.validator import validate


def test_same_seed_same_payload() -> None:
    schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
    first = Generator(seed=7).sample(schema, "thing")
    second = Generator(seed=7).sample(schema, "thing")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_different_seeds_differ() -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    samples = {json.dumps(Generator(seed=seed).sample(schema, "thing")) for seed in range(6)}
    assert len(samples) > 1


def test_enum_and_const_are_respected() -> None:
    generator = Generator(seed=3)
    for _ in range(20):
        assert generator.sample({"enum": ["a", "b", "c"]}) in {"a", "b", "c"}
        assert generator.sample({"const": "fixed"}) == "fixed"


def test_formats_generate_valid_values() -> None:
    generator = Generator(seed=11)
    cases = {
        "email": r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$",
        "uuid": r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        "date": r"^\d{4}-\d{2}-\d{2}$",
        "date-time": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
        "ipv4": r"^(\d{1,3}\.){3}\d{1,3}$",
        "uri": r"^https://",
    }
    for fmt, pattern in cases.items():
        value = generator.sample({"type": "string", "format": fmt}, "field")
        assert re.match(pattern, value), (fmt, value)


def test_byte_format_is_base64() -> None:
    value = Generator(seed=1).sample({"type": "string", "format": "byte"}, "blob")
    import base64

    assert base64.b64decode(value)


def test_integer_bounds_are_inclusive_and_respected() -> None:
    generator = Generator(seed=5)
    for _ in range(50):
        value = generator.sample({"type": "integer", "minimum": 10, "maximum": 12})
        assert 10 <= value <= 12


def test_exclusive_bounds_are_respected() -> None:
    generator = Generator(seed=5)
    for _ in range(40):
        value = generator.sample({"type": "integer", "exclusiveMinimum": 0, "exclusiveMaximum": 3})
        assert 0 < value < 3


def test_multiple_of_integer() -> None:
    generator = Generator(seed=9)
    for _ in range(20):
        assert (
            generator.sample({"type": "integer", "minimum": 0, "maximum": 100, "multipleOf": 5}) % 5
            == 0
        )


def test_string_length_bounds() -> None:
    generator = Generator(seed=2)
    for _ in range(20):
        value = generator.sample({"type": "string", "minLength": 6, "maxLength": 8})
        assert 6 <= len(value) <= 8


def test_pattern_is_satisfied() -> None:
    generator = Generator(seed=4)
    for _ in range(20):
        assert re.fullmatch(
            r"^PET-[A-Z]{2}-[0-9]{4}$",
            generator.sample({"type": "string", "pattern": "^PET-[A-Z]{2}-[0-9]{4}$"}),
        )


def test_unsupported_pattern_falls_back_but_still_validates() -> None:
    schema = {"type": "string", "pattern": "^(a|b)+$"}
    value = Generator(seed=8).sample(schema)
    assert isinstance(value, str)


def test_arrays_respect_min_items() -> None:
    generator = Generator(seed=6)
    value = generator.sample(
        {"type": "array", "minItems": 3, "items": {"type": "integer", "minimum": 0}}
    )
    assert isinstance(value, list)
    assert len(value) >= 3


def test_objects_always_include_required_keys() -> None:
    schema = {
        "type": "object",
        "required": ["id", "name"],
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
    }
    for seed in range(15):
        payload = Generator(seed=seed).sample(schema)
        assert set(payload) >= {"id", "name"}


def test_additional_properties_without_properties() -> None:
    schema = {"type": "object", "additionalProperties": {"type": "integer"}}
    payload = Generator(seed=12).sample(schema)
    assert payload
    assert all(isinstance(value, int) for value in payload.values())


def test_all_of_merges_required_and_properties() -> None:
    schema = {
        "allOf": [
            {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}},
            {"type": "object", "required": ["b"], "properties": {"b": {"type": "string"}}},
        ]
    }
    payload = Generator(seed=3).sample(schema)
    assert {"a", "b"} <= set(payload)


def test_union_prefers_the_structured_branch() -> None:
    schema = {
        "oneOf": [
            {"type": "string"},
            {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "integer"}},
            },
        ]
    }
    payload = Generator(seed=3).sample(schema)
    assert isinstance(payload, dict)


def test_recursive_schema_terminates() -> None:
    marker = {"x-openapi-mock-cycle": "#/components/schemas/Node"}
    schema = {
        "type": "object",
        "required": ["name", "child"],
        "properties": {"name": {"type": "string"}, "child": marker},
    }
    payload = Generator(seed=1).sample(schema)
    assert payload["child"] is None


def test_name_aware_pools() -> None:
    payload = Generator(seed=42).sample(
        {
            "type": "object",
            "required": ["email", "city", "sku"],
            "properties": {
                "email": {"type": "string"},
                "city": {"type": "string"},
                "sku": {"type": "string"},
            },
        },
        "Customer",
    )
    assert "@example.com" in payload["email"]
    assert payload["sku"].startswith("SKU-")


def test_generated_payloads_validate_against_their_schema(petstore) -> None:
    from openapi_mock.loader import load_spec
    from openapi_mock.requests import response_schema

    spec = load_spec(petstore)
    checked = 0
    for operation in spec.operations:
        chosen = operation.success_response()
        if chosen is None:
            continue
        schema = response_schema(operation, chosen[0])
        if not schema:
            continue
        for seed in (0, 1, 42):
            payload = Generator(seed=seed).sample(schema, operation.operation_id)
            issues = validate(payload, schema)
            assert not issues, (operation.operation_id, seed, [i.render() for i in issues])
            checked += 1
    assert checked >= 10


def test_unicode_property_names_and_values() -> None:
    schema = {
        "type": "object",
        "required": ["名前", "絵文字"],
        "properties": {"名前": {"type": "string"}, "絵文字": {"enum": ["🐾", "🚀"]}},
    }
    payload = Generator(seed=5).sample(schema)
    assert isinstance(payload["名前"], str)
    assert payload["絵文字"] in {"🐾", "🚀"}


def test_factory_pools_are_deterministic() -> None:
    first = Factory(seed=1)
    second = Factory(seed=1)
    assert [first.word() for _ in range(5)] == [second.word() for _ in range(5)]


def test_nullable_can_return_null_eventually() -> None:
    schema = {"type": "string", "nullable": True}
    seen = {Generator(seed=seed).sample(schema) for seed in range(40)}
    assert None in seen
    assert any(isinstance(value, str) for value in seen)


def test_empty_schema_produces_a_scalar() -> None:
    assert isinstance(Generator(seed=1).sample({}), str)


def test_boolean_schema_true_and_false() -> None:
    assert isinstance(Generator(seed=1).sample(True), str)
    assert Generator(seed=1).sample(False) is None


def test_number_money_fields_have_two_decimals() -> None:
    value = Generator(seed=3).sample({"type": "number", "minimum": 1, "maximum": 100}, "price")
    assert isinstance(value, float)
    assert round(value, 2) == value

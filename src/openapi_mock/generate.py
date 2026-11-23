"""Schema-driven fake data generation.

The generator walks a schema depth-first and returns a value that is supposed to
satisfy it. It is kept in lockstep with the validator by a test that feeds
generated payloads straight back into validation.
"""

from __future__ import annotations

import re
import zlib
from typing import Any

from .data import Factory, format_generator, pool_for_property
from .schema import (
    complexity_score,
    is_cycle_marker,
    is_nullable,
    merge_all_of,
    pattern_examples,
    resolve_union,
    schema_type,
)

MAX_DEPTH = 6
OPTIONAL_INCLUDE_RATE = 0.8
MONEY_NAMES = ("price", "amount", "total", "cost", "balance", "fee", "subtotal")


def _seed_from(seed: int, *parts: object) -> int:
    digest = zlib.crc32(("|".join([str(seed), *[str(p) for p in parts]])).encode("utf-8"))
    return int(digest)


class Generator:
    """Deterministic fake values for one spec and one seed."""

    def __init__(self, seed: int = 0, max_depth: int = MAX_DEPTH) -> None:
        self.seed = seed
        self.max_depth = max_depth
        self._tick = 0

    def sample(self, schema: Any, name: str = "", depth: int = 0, hint: str = "") -> Any:
        """Return one value for schema. Same seed + same walk gives same value.

        The hint is the enclosing operation or model name (getPet, Customer...).
        It never changes structure, only realism: a property called name becomes a
        pet name inside a pet schema and a person name inside a customer schema.
        """
        if depth == 0:
            self._tick += 1
        factory = Factory(_seed_from(self.seed, depth, name, self._tick))
        return self._sample(schema, name, depth, factory, hint or name)

    def _sample(self, schema: Any, name: str, depth: int, factory: Factory, hint: str = "") -> Any:
        if schema is True or schema is None:
            return factory.word()
        if schema is False:
            return None
        if not isinstance(schema, dict):
            return None
        if is_cycle_marker(schema) or depth > self.max_depth:
            return None
        if "const" in schema:
            return schema["const"]
        enum = schema.get("enum")
        if isinstance(enum, list) and enum:
            return enum[factory.rng.randrange(len(enum))]
        if "example" in schema:
            return schema["example"]
        schema = merge_all_of(schema)
        union = resolve_union(schema, _richest_branch(schema))
        if union is not None:
            return self._sample(union, name, depth + 1, factory, hint)
        if is_nullable(schema) and factory.rng.random() < 0.1:
            return None
        kind = schema_type(schema)
        if kind == "object":
            return self._object(schema, name, depth, factory, hint)
        if kind == "array":
            return self._array(schema, name, depth, factory, hint)
        if kind == "string":
            return self._string(schema, name, factory, hint)
        if kind == "integer":
            return int(self._number(schema, name, factory, integer=True))
        if kind == "number":
            return self._number(schema, name, factory, integer=False)
        if kind == "boolean":
            return factory.rng.random() < 0.5
        if kind == "null":
            return None
        return factory.word()

    def _object(
        self, schema: dict[str, Any], name: str, depth: int, factory: Factory, hint: str = ""
    ) -> dict[str, Any]:
        properties = schema.get("properties")
        required = set(schema.get("required") or [])
        result: dict[str, Any] = {}
        if isinstance(properties, dict):
            for prop_name, prop_schema in properties.items():
                optional = prop_name not in required
                if optional and factory.rng.random() > OPTIONAL_INCLUDE_RATE:
                    continue
                result[prop_name] = self._sample(prop_schema, prop_name, depth + 1, factory, hint)
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            if not properties:
                for _ in range(factory.rng.randrange(2, 4)):
                    key = factory.word()
                    result[key] = self._sample(additional, key, depth + 1, factory, hint)
            else:
                key = "extra_" + factory.word()
                result[key] = self._sample(additional, key, depth + 1, factory, hint)
        return result

    def _array(
        self, schema: dict[str, Any], name: str, depth: int, factory: Factory, hint: str = ""
    ) -> list[Any]:
        items = schema.get("items")
        minimum = int(schema.get("minItems", 2))
        maximum = schema.get("maxItems")
        maximum = int(maximum) if isinstance(maximum, (int, float)) else minimum + 2
        if maximum < minimum:
            maximum = minimum
        count = factory.rng.randint(minimum, min(maximum, minimum + 3))
        return [self._sample(items, name, depth + 1, factory, hint) for _ in range(count)]

    def _string(self, schema: dict[str, Any], name: str, factory: Factory, hint: str = "") -> str:
        pattern = schema.get("pattern") if isinstance(schema.get("pattern"), str) else None
        fmt = schema.get("format")
        minimum = int(schema.get("minLength", 0))
        maximum = schema.get("maxLength")
        max_len = int(maximum) if isinstance(maximum, (int, float)) else None

        pool_value = pool_for_property(name, factory, hint, max_len)
        if pool_value is not None:
            text = str(pool_value)
            if _satisfies(text, pattern, minimum, max_len):
                return text
        if pattern is not None:
            options = pattern_examples(pattern)
            if options:
                return options[factory.rng.randrange(len(options))]
        if isinstance(fmt, str):
            producer = format_generator(factory, fmt)
            if producer is not None:
                value = str(producer())
                return value[:max_len] if max_len is not None else value
        text = factory.word() + " " + factory.word()
        if len(text) < minimum:
            text = text + "x" * (minimum - len(text))
        if max_len is not None and len(text) > max_len:
            text = text[:max_len]
        return text

    def _number(
        self, schema: dict[str, Any], name: str, factory: Factory, integer: bool
    ) -> float | int:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_min = schema.get("exclusiveMinimum")
        exclusive_max = schema.get("exclusiveMaximum")
        if isinstance(exclusive_min, bool):
            exclusive_min = None
        if isinstance(exclusive_max, bool):
            exclusive_max = None
        low = float(minimum) if isinstance(minimum, (int, float)) else 1.0
        high = float(maximum) if isinstance(maximum, (int, float)) else low + 1000.0
        if isinstance(exclusive_min, (int, float)):
            low = max(low, float(exclusive_min) + (1 if integer else 0.01))
        if isinstance(exclusive_max, (int, float)):
            high = min(high, float(exclusive_max) - (1 if integer else 0.01))
        if high < low:
            high = low
        multiple = schema.get("multipleOf")
        if integer:
            value = factory.integer(int(low), int(high))
            if isinstance(multiple, (int, float)) and multiple > 0:
                step = int(multiple)
                value = (int(low) // step) * step
                if value < low:
                    value += step
                if value > high:
                    value = int(low)
            return value
        if any(needle in name.lower() for needle in MONEY_NAMES):
            return factory.money(low, high)
        return round(low + factory.rng.random() * (high - low), 2)


def _richest_branch(schema: dict[str, Any]) -> int:
    """Index of the most structured oneOf/anyOf branch (object over scalar).

    A mock that always answers with the string arm of a union looks synthetic and
    hides half the client code, so we pick the branch with the deepest schema.
    """
    for keyword in ("oneOf", "anyOf"):
        options = schema.get(keyword)
        if not isinstance(options, list) or not options:
            continue
        best_index = 0
        best_score = -1
        for index, option in enumerate(options):
            score = complexity_score(option)
            if score > best_score:
                best_score = score
                best_index = index
        return best_index
    return 0


def _satisfies(text: str, pattern: str | None, minimum: int, maximum: int | None) -> bool:
    if len(text) < minimum:
        return False
    if maximum is not None and len(text) > maximum:
        return False
    if pattern is not None:
        try:
            return re.fullmatch(pattern, text) is not None
        except re.error:
            return True
    return True


def default_value(schema: Any, name: str = "") -> Any:
    """A stable value for headers and path defaults (no per-call state)."""
    return Generator(seed=0).sample(schema, name)

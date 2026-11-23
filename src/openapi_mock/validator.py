"""A JSON-Schema subset validator tuned for OpenAPI request checking.

Only the keywords OpenAPI 3.0/3.1 actually constrain are enforced. Each issue
carries a JSON-pointer-ish path and a sentence a frontend engineer can act on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .schema import (
    infer_value_type,
    is_cycle_marker,
    is_nullable,
    merge_all_of,
    schema_type,
)

FORMAT_CHECKS: dict[str, re.Pattern[str]] = {
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "date-time": re.compile(
        r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$"
    ),
    "uuid": re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    ),
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"),
    "idn-email": re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"),
    "uri": re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^\s]+$"),
    "url": re.compile(r"^https?://[^\s]+$"),
    "ipv4": re.compile(r"^(\d{1,3}\.){3}\d{1,3}$"),
    "ipv6": re.compile(r"^[0-9a-fA-F:]+$"),
    "byte": re.compile(r"^[A-Za-z0-9+/]*={0,2}$"),
    "hostname": re.compile(r"^[A-Za-z0-9.-]+$"),
}


@dataclass(frozen=True)
class Issue:
    path: str
    message: str

    def render(self) -> str:
        return self.path + ": " + self.message


def _join(path: str, token: str | int) -> str:
    if isinstance(token, int):
        return path + "[" + str(token) + "]"
    return path + "." + token if path else token


def _type_matches(kind: str, value: Any) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "null":
        return value is None
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def validate(value: Any, schema: Any, path: str = "", depth: int = 0) -> list[Issue]:
    """Validate value against schema; returns every issue, not just the first."""
    issues: list[Issue] = []
    if schema is False:
        return [Issue(path or "$", "no value is allowed here (schema is false)")]
    if schema is True or schema is None or not schema:
        return issues
    if not isinstance(schema, dict) or depth > 24:
        return issues
    if is_cycle_marker(schema):
        return issues
    schema = merge_all_of(schema)
    if "const" in schema and value != schema["const"]:
        issues.append(Issue(path or "$", "must be exactly " + _short(schema["const"])))
        return issues
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        issues.append(Issue(path or "$", "must be one of " + ", ".join(_short(v) for v in enum)))
    if is_nullable(schema) and value is None:
        return issues

    kind = schema_type(schema)
    if kind is not None and not _type_matches(kind, value):
        issues.append(Issue(path or "$", "expected " + kind + ", got " + infer_value_type(value)))
        return issues

    if isinstance(value, dict):
        issues.extend(_validate_object(value, schema, path, depth))
    elif isinstance(value, list):
        issues.extend(_validate_array(value, schema, path, depth))
    elif isinstance(value, str):
        issues.extend(_validate_string(value, schema, path))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        issues.extend(_validate_number(value, schema, path))
    return issues


def _validate_object(
    value: dict[str, Any], schema: dict[str, Any], path: str, depth: int
) -> list[Issue]:
    issues: list[Issue] = []
    required = schema.get("required") or []
    for name in required:
        if name not in value:
            issues.append(Issue(path or "$", "missing required property '" + str(name) + "'"))
    properties = schema.get("properties")
    additional = schema.get("additionalProperties")
    minimum = schema.get("minProperties")
    maximum = schema.get("maxProperties")
    if isinstance(minimum, int) and len(value) < minimum:
        issues.append(Issue(path or "$", "needs at least " + str(minimum) + " properties"))
    if isinstance(maximum, int) and len(value) > maximum:
        issues.append(Issue(path or "$", "allows at most " + str(maximum) + " properties"))
    for name, item in value.items():
        if isinstance(properties, dict) and name in properties:
            issues.extend(validate(item, properties[name], _join(path, name), depth + 1))
        elif additional is False:
            issues.append(
                Issue(_join(path, name), "unexpected property (additionalProperties: false)")
            )
        elif isinstance(additional, dict):
            issues.extend(validate(item, additional, _join(path, name), depth + 1))
    return issues


def _validate_array(value: list[Any], schema: dict[str, Any], path: str, depth: int) -> list[Issue]:
    issues: list[Issue] = []
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")
    if isinstance(minimum, int) and len(value) < minimum:
        issues.append(Issue(path or "$", "needs at least " + str(minimum) + " items"))
    if isinstance(maximum, int) and len(value) > maximum:
        issues.append(Issue(path or "$", "allows at most " + str(maximum) + " items"))
    if schema.get("uniqueItems") is True:
        seen: list[str] = []
        for item in value:
            marker = _short(item)
            if marker in seen:
                issues.append(Issue(path or "$", "items must be unique, duplicate " + marker))
                break
            seen.append(marker)
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            issues.extend(validate(item, items, _join(path, index), depth + 1))
    return issues


def _validate_string(value: str, schema: dict[str, Any], path: str) -> list[Issue]:
    issues: list[Issue] = []
    minimum = schema.get("minLength")
    maximum = schema.get("maxLength")
    if isinstance(minimum, int) and len(value) < minimum:
        issues.append(Issue(path or "$", "shorter than minLength " + str(minimum)))
    if isinstance(maximum, int) and len(value) > maximum:
        issues.append(Issue(path or "$", "longer than maxLength " + str(maximum)))
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            matched = re.search(pattern, value) is not None
        except re.error:
            matched = True  # an invalid pattern is a spec bug, not a client bug
        if not matched:
            issues.append(Issue(path or "$", "does not match pattern " + pattern))
    fmt = schema.get("format")
    if isinstance(fmt, str):
        check = FORMAT_CHECKS.get(fmt)
        if check is not None and not check.search(value):
            issues.append(Issue(path or "$", "is not a valid " + fmt))
    return issues


def _validate_number(value: float, schema: dict[str, Any], path: str) -> list[Issue]:
    issues: list[Issue] = []
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    exclusive_min = schema.get("exclusiveMinimum")
    exclusive_max = schema.get("exclusiveMaximum")
    if isinstance(exclusive_min, bool):
        exclusive_min = minimum if exclusive_min else None
    if isinstance(exclusive_max, bool):
        exclusive_max = maximum if exclusive_max else None
    if isinstance(minimum, (int, float)) and value < minimum:
        issues.append(Issue(path or "$", "must be >= " + str(minimum)))
    if isinstance(maximum, (int, float)) and value > maximum:
        issues.append(Issue(path or "$", "must be <= " + str(maximum)))
    if isinstance(exclusive_min, (int, float)) and value <= exclusive_min:
        issues.append(Issue(path or "$", "must be > " + str(exclusive_min)))
    if isinstance(exclusive_max, (int, float)) and value >= exclusive_max:
        issues.append(Issue(path or "$", "must be < " + str(exclusive_max)))
    multiple = schema.get("multipleOf")
    if isinstance(multiple, (int, float)) and multiple > 0:
        quotient = value / multiple
        if abs(quotient - round(quotient)) > 1e-9:
            issues.append(Issue(path or "$", "must be a multiple of " + str(multiple)))
    return issues


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 48 else text[:45] + "..."

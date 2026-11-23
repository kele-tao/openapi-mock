"""Schema algebra: $ref cycles, allOf merging, type inference and complexity.

Everything here is pure: given a (sub)schema dictionary it returns a new value.
That keeps the generator, the validator and the summary command honest about the
exact same interpretation of the document.
"""

from __future__ import annotations

import re
from typing import Any

from .loader import CYCLE_KEY

JSON_TYPES = ("object", "array", "string", "integer", "number", "boolean", "null")


def is_cycle_marker(node: Any) -> bool:
    return isinstance(node, dict) and CYCLE_KEY in node


def merge_all_of(schema: dict[str, Any]) -> dict[str, Any]:
    """Flatten allOf into a single schema.

    Objects merge property by property (later branches win), required lists are
    unioned and bounds take the intersection (max of minimums, min of maximums).
    Non-object branches are overlaid in order, which is what mainstream validators
    do in practice.
    """
    branches = schema.get("allOf")
    if not isinstance(branches, list):
        return schema
    merged: dict[str, Any] = {k: v for k, v in schema.items() if k != "allOf"}
    for branch in branches:
        if not isinstance(branch, dict) or is_cycle_marker(branch):
            continue
        flat = merge_all_of(branch)
        merged = _overlay(merged, flat)
    if "required" in merged:
        merged["required"] = list(dict.fromkeys(merged["required"]))
    return merged


def _overlay(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    result = dict(left)
    for key, value in right.items():
        if key == "properties" and isinstance(value, dict):
            props = dict(result.get("properties") or {})
            for name, sub in value.items():
                existing = props.get(name)
                if isinstance(existing, dict) and isinstance(sub, dict):
                    props[name] = _overlay(existing, sub)
                else:
                    props[name] = sub
            result["properties"] = props
        elif key == "required" and isinstance(value, list):
            existing = list(result.get("required") or [])
            result["required"] = existing + [item for item in value if item not in existing]
        elif key in ("minimum", "minLength", "minItems", "minProperties") and isinstance(
            value, (int, float)
        ):
            result[key] = max(value, result.get(key, value))
        elif key in ("maximum", "maxLength", "maxItems", "maxProperties") and isinstance(
            value, (int, float)
        ):
            result[key] = min(value, result.get(key, value))
        elif key == "enum" and isinstance(value, list) and isinstance(result.get("enum"), list):
            result["enum"] = [item for item in result["enum"] if item in value]
        else:
            result[key] = value
    return result


def schema_type(schema: Any) -> str | None:
    """Best-effort JSON type of a schema; None means 'any'."""
    if not isinstance(schema, dict):
        return None
    if is_cycle_marker(schema):
        return "object"
    merged = merge_all_of(schema)
    raw = merged.get("type")
    if isinstance(raw, str):
        return raw if raw in JSON_TYPES else None
    if isinstance(raw, list):
        concrete = [item for item in raw if item in JSON_TYPES and item != "null"]
        if len(concrete) == 1:
            return concrete[0]
        if not concrete:
            return "null"
        return concrete[0]
    if "properties" in merged or "additionalProperties" in merged:
        return "object"
    if "items" in merged:
        return "array"
    enum = merged.get("enum")
    if isinstance(enum, list) and enum:
        return infer_value_type(enum[0])
    if "const" in merged:
        return infer_value_type(merged["const"])
    if "pattern" in merged or "format" in merged or "minLength" in merged:
        return "string"
    if "minimum" in merged or "maximum" in merged or "multipleOf" in merged:
        return "number"
    for union in ("oneOf", "anyOf"):
        options = merged.get(union)
        if isinstance(options, list) and options:
            return schema_type(options[0])
    return None


def is_nullable(schema: dict[str, Any]) -> bool:
    if schema.get("nullable") is True:
        return True
    raw = schema.get("type")
    return isinstance(raw, list) and "null" in raw


def infer_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def resolve_union(schema: dict[str, Any], index: int = 0) -> dict[str, Any] | None:
    """Pick a concrete branch out of oneOf/anyOf (clamped, deterministic)."""
    for keyword in ("oneOf", "anyOf"):
        options = schema.get(keyword)
        if isinstance(options, list) and options:
            usable = [
                option
                for option in options
                if isinstance(option, dict) and not is_cycle_marker(option)
            ]
            if not usable:
                return None
            return usable[index % len(usable)]
    return None


def complexity_score(schema: Any, _depth: int = 0) -> int:
    """A monotone, explainable complexity number used by the summary command.

    Points: 1 per visited schema node, +2 per object property, +3 per union branch,
    1 per 2 levels of nesting, +2 for a pattern, +1 for every bound keyword.
    """
    if not isinstance(schema, dict) or _depth > 12:
        return 0
    score = 1 + _depth // 2
    bounds = ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems")
    score += sum(1 for keyword in bounds if keyword in schema)
    if "pattern" in schema:
        score += 2
    properties = schema.get("properties")
    if isinstance(properties, dict):
        score += 2 * len(properties)
        for sub in properties.values():
            score += complexity_score(sub, _depth + 1)
    items = schema.get("items")
    if isinstance(items, dict):
        score += complexity_score(items, _depth + 1)
    for keyword in ("allOf", "oneOf", "anyOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            score += 3 * len(branches)
            for branch in branches:
                score += complexity_score(branch, _depth + 1)
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        score += complexity_score(additional, _depth + 1)
    return score


def schema_label(schema: Any) -> str:
    """Short human label for a schema, used by the endpoint table."""
    if not isinstance(schema, dict) or not schema:
        return "any"
    if is_cycle_marker(schema):
        return "recursive"
    merged = merge_all_of(schema)
    for keyword in ("oneOf", "anyOf", "allOf"):
        options = merged.get(keyword)
        if isinstance(options, list) and options:
            return keyword + ":" + str(len(options))
    kind = schema_type(merged)
    if kind is None:
        return "any"
    if kind == "array":
        return kind + "<" + schema_label(merged.get("items")) + ">"
    if kind == "object" and isinstance(merged.get("properties"), dict):
        return "object:" + str(len(merged["properties"]))
    return kind


def pattern_examples(pattern: str, limit: int = 8) -> list[str]:
    """Generate strings for the common pattern idioms used in API specs.

    This is a deliberate, documented subset (character classes, literals, \\d,
    \\w and simple quantifiers). Unsupported constructs make the generator fall
    back, and every candidate is verified with re.fullmatch, so we never emit a
    value that violates the pattern we advertise.
    """
    candidates: list[str] = []
    anchor = pattern
    if anchor.startswith("^"):
        anchor = anchor[1:]
    if anchor.endswith("$"):
        anchor = anchor[:-1]
    tokens = _tokenize(anchor)
    if tokens is None:
        return []
    for seed_char in ("a", "A", "0", "x", "Z", "9", "b", "k"):
        built = _expand(tokens, seed_char)
        if built is not None and re.fullmatch(pattern, built) and built not in candidates:
            candidates.append(built)
            if len(candidates) >= limit:
                break
    return candidates


def _tokenize(pattern: str) -> list[tuple[str, str, int]] | None:
    """Split into (kind, value, quantifier) where kind is 'lit' or 'class'."""
    out: list[tuple[str, str, int]] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            if index + 1 >= len(pattern):
                return None
            escaped = pattern[index + 1]
            kind = "class" if escaped in "dwsS" else "lit"
            out.append((kind, escaped, 1))
            index += 2
        elif char == "[":
            close = pattern.find("]", index + 1)
            if close == -1:
                return None
            body = pattern[index + 1 : close]
            out.append(("class", body, 1))
            index = close + 1
        elif char in "()|.?*+{}^$":
            return None
        else:
            out.append(("lit", char, 1))
            index += 1
        if index < len(pattern):
            follow = pattern[index]
            if follow in "?*+":
                kind, value, _ = out[-1]
                out[-1] = (kind, value, {"?": 0, "*": 0, "+": 1}[follow])
                index += 1
            elif follow == "{":
                close = pattern.find("}", index)
                if close == -1:
                    return None
                body = pattern[index + 1 : close]
                low = body.split(",")[0]
                if not low.isdigit():
                    return None
                kind, value, _ = out[-1]
                out[-1] = (kind, value, int(low))
                index = close + 1
    return out


def _expand(tokens: list[tuple[str, str, int]], seed: str) -> str | None:
    """Build one string; repeated classes cycle through their alphabet.

    Cycling matters for realism: a four-digit class must produce 4821, not 0000.
    The cycle starts at the candidate seed, so output stays deterministic.
    """
    pieces: list[str] = []
    cursor = seed
    for kind, value, count in tokens:
        if kind == "lit":
            pieces.append(value * max(count, 0))
            continue
        alphabet = _class_alphabet(value, cursor)
        if alphabet is None:
            return None
        if count <= 1:
            pieces.append(alphabet[0])
            cursor = alphabet[0]
            continue
        built: list[str] = []
        position = alphabet.index(cursor) if cursor in alphabet else 0
        for step in range(count):
            char = alphabet[(position + step) % len(alphabet)]
            built.append(char)
        pieces.append("".join(built))
        cursor = built[-1]
    return "".join(pieces)


def _class_alphabet(spec: str, seed: str) -> list[str] | None:
    """Every character a class can produce, rotated so the seed comes first."""
    digits = list("1234567890")
    lower = list("abcdefghijklmnopqrstuvwxyz")
    upper = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    if spec == "d":
        pool = digits
    elif spec == "w":
        pool = digits + upper + lower + ["_"]
    elif spec == "s":
        pool = [" "]
    elif spec == "S":
        pool = list("abcdefghijklmnopqrstuvwxyz")
    elif len(spec) == 1:
        return [spec]
    elif len(spec) == 3 and spec[1] == "-":
        low, high = spec[0], spec[2]
        pool = [chr(code) for code in range(ord(low), ord(high) + 1)]
    else:
        pool = [char for char in spec if char not in "\\^"]
    if not pool:
        return None
    if seed in pool:
        index = pool.index(seed)
        pool = pool[index:] + pool[:index]
    return pool

"""Rendering for the summary and validate commands.

Tables are laid out by hand because the whole point is that the bytes are stable:
same spec in, same table out, no dependency on a terminal-width library.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from . import colors
from .loader import Operation, Spec
from .requests import Violation
from .schema import complexity_score, schema_label


def _widths(rows: Sequence[Sequence[str]], headers: Sequence[str]) -> list[int]:
    widths = [len(cell) for cell in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    return widths


def table(headers: Sequence[str], rows: Sequence[Sequence[str]], indent: int = 2) -> str:
    """Render a plain ASCII table with no trailing whitespace."""
    widths = _widths(rows, headers)
    pad = " " * indent
    out: list[str] = []
    out.append(pad + "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip())
    out.append(pad + "  ".join("-" * w for w in widths))
    for row in rows:
        out.append(pad + "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip())
    return "\n".join(out)


def auth_label(operation: Operation, spec: Spec) -> str:
    """Which security scheme guards this operation, resolved through globals."""
    security = operation.security
    if security is None:
        security = spec.document.get("security") or []
    if not security:
        return "-"
    names: list[str] = []
    for requirement in security:
        if isinstance(requirement, dict):
            names.extend(str(name) for name in requirement)
    if not names:
        return "-"
    return ",".join(dict.fromkeys(names))


def response_label(operation: Operation) -> str:
    chosen = operation.success_response()
    if chosen is None:
        return "204"
    status, response = chosen
    schema = _response_schema_of(response)
    return status + " " + schema_label(schema)


def _response_schema_of(response: dict[str, Any]) -> Any:
    content = response.get("content")
    if not isinstance(content, dict):
        return {}
    for media in ("application/json", "application/*+json"):
        entry = content.get(media)
        if isinstance(entry, dict) and isinstance(entry.get("schema"), dict):
            return entry["schema"]
    for entry in content.values():
        if isinstance(entry, dict) and isinstance(entry.get("schema"), dict):
            return entry["schema"]
    return {}


def operation_complexity(operation: Operation) -> int:
    """Sum the request and response schema complexity of one endpoint."""
    score = 0
    body = operation.request_body
    if isinstance(body, dict):
        content = body.get("content")
        if isinstance(content, dict):
            for entry in content.values():
                if isinstance(entry, dict) and isinstance(entry.get("schema"), dict):
                    score += complexity_score(entry["schema"])
    chosen = operation.success_response()
    if chosen is not None:
        score += complexity_score(_response_schema_of(chosen[1]))
    return score


def summary_report(
    spec: Spec, *, as_json: bool = False, show_warnings: bool = True
) -> tuple[str, int]:
    """Build the endpoint table. Returns (text, number of deprecated operations)."""
    rows: list[list[str]] = []
    for operation in spec.operations:
        rows.append(
            [
                operation.method.upper(),
                operation.path,
                operation.operation_id,
                auth_label(operation, spec),
                response_label(operation),
                str(operation_complexity(operation)),
            ]
        )
    headers = ["METHOD", "PATH", "OPERATION ID", "AUTH", "RESPONSE", "COMPLEXITY"]
    flagged = [
        operation
        for operation in spec.operations
        if operation.deprecated or not auth_label(operation, spec) == "-"
    ]
    if as_json:
        payload = {
            "spec": spec.path,
            "title": spec.title,
            "version": spec.version,
            "openapi": spec.openapi_version,
            "operations": [
                {
                    "method": operation.method.upper(),
                    "path": operation.path,
                    "operation_id": operation.operation_id,
                    "auth": auth_label(operation, spec),
                    "response": response_label(operation),
                    "complexity": operation_complexity(operation),
                    "deprecated": operation.deprecated,
                    "summary": operation.summary,
                }
                for operation in spec.operations
            ],
            "warnings": list(spec.warnings),
        }
        return json.dumps(payload, indent=2, sort_keys=False), len(
            [op for op in spec.operations if op.deprecated]
        )

    lines: list[str] = []
    lines.append(
        colors.bold(spec.title)
        + colors.dim(
            "  " + "v" + spec.version + "  OpenAPI " + spec.openapi_version + "  " + spec.path
        )
    )
    lines.append("")
    lines.append(table(headers, rows))
    lines.append("")
    deprecated = [op for op in spec.operations if op.deprecated]
    secured = [op for op in flagged if auth_label(op, spec) != "-"]
    lines.append(
        colors.dim(
            "  "
            + str(len(spec.operations))
            + " operations  |  "
            + str(len(secured))
            + " need auth  |  "
            + str(len(deprecated))
            + " deprecated  |  "
            + str(len(spec.security_schemes))
            + " security schemes  |  total complexity "
            + str(sum(operation_complexity(op) for op in spec.operations))
        )
    )
    if show_warnings and spec.warnings:
        lines.append("")
        lines.append(colors.yellow("  notes"))
        for warning in spec.warnings:
            lines.append("    " + colors.dim("-") + " " + warning)
    return "\n".join(lines), len(deprecated)


def validate_report(
    results: Iterable[tuple[int, str, str, list[Violation]]], *, as_json: bool = False
) -> tuple[str, int]:
    """Render replay findings. Returns (text, exit_code)."""
    rows = list(results)
    total = sum(len(violations) for *_, violations in rows)
    if as_json:
        payload = {
            "requests": len(rows),
            "violations": total,
            "findings": [
                {
                    "line": line,
                    "method": method,
                    "path": path,
                    "kind": violation.kind,
                    "detail": violation.detail,
                }
                for line, method, path, violations in rows
                for violation in violations
            ],
        }
        return json.dumps(payload, indent=2), 0 if total == 0 else 1
    lines: list[str] = []
    for line, method, path, violations in rows:
        if not violations:
            lines.append(
                colors.green("  ok   ")
                + colors.method(method)
                + " "
                + path
                + colors.dim("  line " + str(line))
            )
            continue
        lines.append(
            colors.red("  FAIL ")
            + colors.method(method)
            + " "
            + path
            + colors.dim("  line " + str(line))
        )
        for violation in violations:
            lines.append(
                "         "
                + colors.red(violation.kind.ljust(6))
                + colors.dim(" ")
                + violation.detail
            )
    lines.append("")
    if total == 0:
        lines.append(colors.green("  " + str(len(rows)) + " requests, 0 contract violations"))
    else:
        lines.append(
            colors.red("  " + str(len(rows)) + " requests, " + str(total) + " contract violations")
        )
    return "\n".join(lines), 0 if total == 0 else 1

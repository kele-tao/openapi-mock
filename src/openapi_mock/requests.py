"""Record and replay format, plus the replay checker behind the validate command.

The request log is newline-delimited JSON: one request per line, append-only, so a
crash never corrupts earlier records and plain grep works. A record carries what
the client sent plus the status the server answered; replay deliberately ignores
that status and re-checks the request against the spec, which is what catches a
client drifting away from its contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .errors import UsageError
from .loader import Operation, Spec
from .validator import Issue, validate

# Headers every HTTP client sends; checking them would only produce noise.
IGNORED_HEADERS = frozenset(
    {
        "host",
        "user-agent",
        "accept",
        "accept-encoding",
        "accept-language",
        "content-type",
        "content-length",
        "connection",
        "cache-control",
        "pragma",
        "postman-token",
        "sec-fetch-mode",
        "sec-fetch-site",
        "sec-fetch-dest",
    }
)


@dataclass
class Record:
    """One request as stored on disk."""

    method: str
    path: str
    query: dict[str, list[str]] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None
    status: int | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "method": self.method.upper(),
            "path": self.path,
            "query": {key: list(values) for key, values in self.query.items()},
            "headers": dict(self.headers),
        }
        if self.body is not None:
            payload["body"] = self.body
        if self.status is not None:
            payload["status"] = self.status
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Record:
        if not isinstance(payload, dict):
            raise ValueError("record must be a JSON object")
        method = payload.get("method")
        path = payload.get("path")
        if not isinstance(method, str) or not isinstance(path, str):
            raise ValueError("record needs string 'method' and 'path'")
        if not path.startswith("/"):
            raise ValueError("record 'path' must start with '/', got " + repr(path))
        raw_query = payload.get("query") or {}
        query: dict[str, list[str]] = {}
        if isinstance(raw_query, dict):
            for key, values in raw_query.items():
                if isinstance(values, list):
                    query[str(key)] = [str(item) for item in values]
                else:
                    query[str(key)] = [str(values)]
        raw_headers = payload.get("headers") or {}
        headers = (
            {str(key): str(value) for key, value in raw_headers.items()}
            if isinstance(raw_headers, dict)
            else {}
        )
        raw_status = payload.get("status")
        return cls(
            method=method,
            path=path,
            query=query,
            headers=headers,
            body=payload.get("body"),
            status=raw_status if isinstance(raw_status, int) else None,
        )


@dataclass(frozen=True)
class Violation:
    """One contract violation found while replaying a recorded request."""

    line: int
    method: str
    path: str
    kind: str
    detail: str

    def render(self) -> str:
        return (
            "line "
            + str(self.line)
            + " "
            + self.method.upper()
            + " "
            + self.path
            + " ["
            + self.kind
            + "] "
            + self.detail
        )


def read_log(path: str | Path) -> list[tuple[int, Record]]:
    """Parse a JSONL request log; a bad line becomes a UsageError naming the line."""
    log_path = Path(path)
    if not log_path.exists():
        raise UsageError(
            f"request log not found: {path}", hint="serve --record FILE writes one for you"
        )
    if log_path.is_dir():
        raise UsageError(f"request log path is a directory, not a file: {path}")
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise UsageError(f"{log_path.name}: file is not valid UTF-8 text") from exc
    except OSError as exc:
        raise UsageError(f"cannot read request log {path}: {exc.strerror}") from exc
    records: list[tuple[int, Record]] = []
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise UsageError(
                f"{log_path.name}:{number}: invalid JSON ({exc.msg})",
                hint="the log must be JSONL as written by serve --record",
            ) from exc
        try:
            records.append((number, Record.from_json(payload)))
        except ValueError as exc:
            raise UsageError(f"{log_path.name}:{number}: {exc}") from exc
    if not records:
        raise UsageError(
            f"{log_path.name}: no request records found",
            hint="each line must be one JSON object with 'method' and 'path'",
        )
    return records


def coerce_param(raw: str, schema: dict[str, Any]) -> tuple[Any, str | None]:
    """Turn a wire value into the type the schema declares.

    Serialisation follows OpenAPI style=form, explode=true for arrays:
    '?ids=1&ids=2' is how real clients send repeated parameters.
    """
    kind = schema.get("type")
    if kind == "integer":
        try:
            return int(raw), None
        except ValueError:
            return raw, "expected integer, got " + repr(raw)
    if kind == "number":
        try:
            return float(raw), None
        except ValueError:
            return raw, "expected number, got " + repr(raw)
    if kind == "boolean":
        if raw.lower() in ("true", "1"):
            return True, None
        if raw.lower() in ("false", "0"):
            return False, None
        return raw, "expected boolean, got " + repr(raw)
    return raw, None


def check_record(spec: Spec, record: Record, line: int = 0) -> list[Violation]:
    """Replay one recorded request against the spec and list what it broke."""
    hit = spec.match(record.method, record.path)
    if hit is None:
        return [
            Violation(
                line,
                record.method,
                record.path,
                "route",
                "no " + record.method.upper() + " operation matches this path",
            )
        ]
    operation, path_values = hit
    found: list[tuple[str, str]] = []
    found.extend(_check_path_values(operation, path_values))
    found.extend(_check_query(operation, record))
    found.extend(_check_headers(operation, record))
    found.extend(_check_body(operation, record))
    return [Violation(line, record.method, record.path, kind, detail) for kind, detail in found]


def _check_path_values(operation: Operation, path_values: dict[str, str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for name, raw in path_values.items():
        param = _param(operation, "path", name)
        if param is None:
            continue
        schema = param.get("schema") or {}
        value, error = coerce_param(raw, schema)
        if error is not None:
            out.append(("path", name + ": " + error))
            continue
        out.extend(("path", issue.render()) for issue in validate(value, schema, "path." + name))
    return out


def _check_query(operation: Operation, record: Record) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    declared = {str(param.get("name")) for param in operation.params_in("query")}
    for name in record.query:
        if name not in declared and name not in ("_", "cacheBust"):
            out.append(("query", "unknown parameter '" + name + "'"))
    for param in operation.params_in("query"):
        name = str(param.get("name"))
        values = record.query.get(name)
        if values is None:
            if param.get("required") is True:
                out.append(("query", "missing required parameter '" + name + "'"))
            continue
        schema = param.get("schema") or {}
        if schema.get("type") == "array":
            target = schema.get("items") or {}
            converted = []
            for value in values:
                item, error = coerce_param(value, target)
                if error is not None:
                    out.append(("query", name + ": " + error))
                converted.append(item)
            out.extend(
                ("query", issue.render()) for issue in validate(converted, schema, "query." + name)
            )
        else:
            value, error = coerce_param(values[-1], schema)
            if error is not None:
                out.append(("query", name + ": " + error))
                continue
            out.extend(
                ("query", issue.render()) for issue in validate(value, schema, "query." + name)
            )
    return out


def _check_headers(operation: Operation, record: Record) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    sent = {name.lower(): value for name, value in record.headers.items()}
    declared = {str(param.get("name")).lower() for param in operation.params_in("header")}
    for name in sent:
        if name in declared or name in IGNORED_HEADERS:
            continue
        if name.startswith("x-"):
            out.append(("header", "unknown parameter '" + name + "'"))
    for param in operation.params_in("header"):
        name = str(param.get("name"))
        value = sent.get(name.lower())
        if value is None:
            if param.get("required") is True:
                out.append(("header", "missing required header '" + name + "'"))
            continue
        schema = param.get("schema") or {}
        converted, error = coerce_param(value, schema)
        if error is not None:
            out.append(("header", name + ": " + error))
            continue
        out.extend(("header", issue.render()) for issue in validate(converted, schema, name))
    return out


def _check_body(operation: Operation, record: Record) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    required = bool(operation.request_body and operation.request_body.get("required"))
    if record.body is None:
        if required:
            out.append(("body", "missing required request body"))
        return out
    schema = request_body_schema(operation)
    if schema is None:
        if operation.request_body is not None:
            out.append(("body", "operation declares no JSON body; unexpected payload"))
        else:
            out.append(("body", "operation takes no request body"))
        return out
    out.extend(("body", issue.render()) for issue in validate(record.body, schema, "body"))
    return out


def _param(
    operation: Operation, location: str, name: str, casefold: bool = False
) -> dict[str, Any] | None:
    for param in operation.params_in(location):
        candidate = str(param.get("name"))
        matched = candidate.lower() == name.lower() if casefold else candidate == name
        if matched:
            return param
    return None


def request_body_schema(operation: Operation) -> dict[str, Any] | None:
    """Pick the JSON schema of a request body (application/json wins)."""
    body = operation.request_body
    if not isinstance(body, dict):
        return None
    content = body.get("content")
    if not isinstance(content, dict) or not content:
        return None
    for media in ("application/json", "application/*+json", "text/json"):
        entry = content.get(media)
        if isinstance(entry, dict) and isinstance(entry.get("schema"), dict):
            return entry["schema"]
    for media, entry in content.items():
        if "json" in media and isinstance(entry, dict) and isinstance(entry.get("schema"), dict):
            return entry["schema"]
    return None


def response_schema(operation: Operation, status: str) -> dict[str, Any] | None:
    response = operation.responses.get(status)
    if not isinstance(response, dict):
        response = operation.responses.get("default")
    if not isinstance(response, dict):
        return None
    content = response.get("content")
    if not isinstance(content, dict) or not content:
        return None
    for media in ("application/json", "application/*+json"):
        entry = content.get(media)
        if isinstance(entry, dict) and isinstance(entry.get("schema"), dict):
            return entry["schema"]
    for media, entry in content.items():
        if "json" in media and isinstance(entry, dict) and isinstance(entry.get("schema"), dict):
            return entry["schema"]
    return None


def parse_url(target: str) -> tuple[str, dict[str, list[str]]]:
    """Split a request target into its path and parsed query parameters."""
    parts = urlsplit(target)
    query: dict[str, list[str]] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query.setdefault(key, []).append(value)
    return parts.path, query


__all__ = [
    "Issue",
    "Record",
    "Violation",
    "check_record",
    "coerce_param",
    "parse_url",
    "read_log",
    "request_body_schema",
    "response_schema",
]

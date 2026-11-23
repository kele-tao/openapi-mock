"""Spec loading: JSON/YAML parsing, OpenAPI 3.x checks and $ref resolution.

The loader is deliberately strict about the *document envelope* (so a Swagger 2.0
file or an HTML error page produces a one-line explanation instead of a traceback)
and deliberately permissive inside schemas (unknown keywords are kept and ignored
by the generator, exactly like a JSON Schema implementation would).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any

from .errors import SpecError

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
CYCLE_KEY = "x-openapi-mock-cycle"
SUPPORTED_MAJORS = ("3.0", "3.1")

# Keywords we keep but cannot enforce; surfaced in summary output as notes.
UNSUPPORTED_SCHEMA_KEYWORDS = ("not", "if", "then", "else", "dependentSchemas")
PARAM_LOCATIONS = ("path", "query", "header", "cookie")


@dataclass(frozen=True)
class Operation:
    """One method + path pair, with every external $ref already inlined."""

    method: str
    path: str
    operation_id: str
    summary: str = ""
    description: str = ""
    deprecated: bool = False
    tags: tuple[str, ...] = ()
    parameters: tuple[dict[str, Any], ...] = ()
    request_body: dict[str, Any] | None = None
    responses: dict[str, dict[str, Any]] = field(default_factory=dict)
    security: tuple[dict[str, Any], ...] | None = None
    pattern: re.Pattern[str] = field(default_factory=lambda: re.compile(r"$^"))
    param_names: tuple[str, ...] = ()

    @property
    def display(self) -> str:
        return f"{self.method.upper()} {self.path}"

    def params_in(self, location: str) -> tuple[dict[str, Any], ...]:
        return tuple(p for p in self.parameters if p.get("in") == location)

    def success_response(self) -> tuple[str, dict[str, Any]] | None:
        """Pick the response the mock should serve, preferring an explicit 2xx."""
        preferred = ("200", "201", "202", "203", "206", "204", "205")
        for code in preferred:
            if code in self.responses:
                return code, self.responses[code]
        wildcard = [c for c in self.responses if c.lower() == "2xx"]
        if wildcard:
            return "200", self.responses[wildcard[0]]
        numeric = sorted(c for c in self.responses if c.isdigit() and c.startswith("2"))
        if numeric:
            return numeric[0], self.responses[numeric[0]]
        if "default" in self.responses:
            return "200", self.responses["default"]
        return None


@dataclass(frozen=True)
class Spec:
    """A fully resolved OpenAPI document plus the operations it declares."""

    path: str
    document: dict[str, Any]
    title: str
    version: str
    openapi_version: str
    operations: tuple[Operation, ...]
    security_schemes: dict[str, Any]
    warnings: tuple[str, ...] = ()
    servers: tuple[str, ...] = ()

    def operation(self, method: str, path: str) -> Operation | None:
        for op in self.operations:
            if op.method == method.lower() and op.path == path:
                return op
        return None

    def match(self, method: str, concrete_path: str) -> tuple[Operation, dict[str, str]] | None:
        for op in self.operations:
            if op.method != method.lower():
                continue
            hit = op.pattern.fullmatch(concrete_path)
            if hit:
                return op, {k: v for k, v in hit.groupdict().items()}
        return None


def _read_json_pointer(document: Any, pointer: str, ref: str) -> Any:
    if pointer in ("", "/"):
        return document
    parts = pointer.lstrip("/").split("/")
    cursor = document
    for part in parts:
        token = part.replace("~1", "/").replace("~0", "~")
        if isinstance(cursor, dict):
            if token not in cursor:
                raise SpecError(
                    f"cannot resolve $ref '{ref}': '{token}' does not exist",
                    hint="check the component name; ~0 escapes ~ and ~1 escapes /",
                )
            cursor = cursor[token]
        elif isinstance(cursor, list):
            try:
                cursor = cursor[int(token)]
            except (ValueError, IndexError) as exc:
                raise SpecError(
                    f"cannot resolve $ref '{ref}': array index '{token}' is out of range"
                ) from exc
        else:
            raise SpecError(f"cannot resolve $ref '{ref}': '{token}' is not reachable")
    return cursor


class Resolver:
    """Inline local and relative-file $refs, detecting cycles.

    Path resolution is memoised: on Windows every realpath call costs ~0.8 ms and a
    spec with 30 refs to one sibling file would otherwise pay it 30 times.
    """

    def __init__(self, root_path: pathlib.Path, document: dict[str, Any]) -> None:
        self.root_path = root_path
        self._root_dir = root_path.parent
        self._documents: dict[str, Any] = {self._key(root_path): document}
        self._paths: dict[str, pathlib.Path] = {}

    @staticmethod
    def _key(path: pathlib.Path) -> str:
        return os.path.normcase(str(path))

    def _resolve_path(self, base_dir: pathlib.Path, file_part: str) -> pathlib.Path:
        cache_key = str(base_dir) + "|" + file_part
        cached = self._paths.get(cache_key)
        if cached is not None:
            return cached
        candidate = pathlib.Path(base_dir, file_part)
        try:
            resolved = candidate.resolve()
        except OSError as exc:  # pragma: no cover - exotic paths only
            raise SpecError(f"bad $ref path '{file_part}': {exc}") from exc
        self._paths[cache_key] = resolved
        return resolved

    def load_document(self, path: pathlib.Path) -> Any:
        key = self._key(path)
        cached = self._documents.get(key)
        if cached is not None:
            return cached
        if not path.exists():
            raise SpecError(
                f"referenced spec file not found: {path}",
                hint="relative $ref paths resolve against the file that contains them",
            )
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SpecError(f"cannot read referenced file {path}: {exc.strerror}") from exc
        document = parse_document(raw, path.name)
        self._documents[key] = document
        return document

    def lookup(self, ref: str, base_dir: pathlib.Path) -> tuple[Any, pathlib.Path]:
        if ref.startswith(("http://", "https://")):
            raise SpecError(
                f"remote $ref is not supported (offline by design): {ref}",
                hint="download the file and reference it relatively, e.g. './common.json#/X'",
            )
        file_part, _, fragment = ref.partition("#")
        if not file_part:
            document = self._documents[self._key(self.root_path)]
            return _read_json_pointer(document, fragment, ref), self._root_dir
        target_path = self._resolve_path(base_dir, file_part)
        document = self.load_document(target_path)
        value = _read_json_pointer(document, fragment, ref) if fragment else document
        return value, target_path.parent

    def resolve(self, node: Any, base_dir: pathlib.Path, stack: frozenset[tuple[str, str]]) -> Any:
        if isinstance(node, list):
            return [self.resolve(item, base_dir, stack) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str):
            key = (str(base_dir), ref)
            if key in stack:
                return {CYCLE_KEY: ref}
            target, target_dir = self.lookup(ref, base_dir)
            resolved = self.resolve(target, target_dir, stack | {key})
            siblings = {value: None for value in node if value != "$ref"}
            if siblings and isinstance(resolved, dict):
                merged = dict(resolved)
                for name in siblings:
                    merged.setdefault(name, self.resolve(node[name], base_dir, stack))
                resolved = merged
            return resolved
        return {key: self.resolve(value, base_dir, stack) for key, value in node.items()}


def parse_document(raw: str, name: str) -> Any:
    """Parse JSON, or YAML when the extension or the shape says so."""
    stripped = raw.lstrip()
    if not stripped:
        raise SpecError(
            f"{name}: file is empty",
            hint="an OpenAPI document needs at least 'openapi' and 'paths'",
        )
    yaml_like = name.lower().endswith((".yaml", ".yml"))
    if not yaml_like and stripped[0] in "{[":
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SpecError(
                f"{name}: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}",
                hint="format the file, or rename it to .yaml if it is really YAML",
            ) from exc
    return parse_yaml(raw, name)


def parse_yaml(raw: str, name: str) -> Any:
    try:
        import yaml  # noqa: PLC0415 - optional extra, imported lazily
    except ImportError as exc:
        raise SpecError(
            f"{name}: this looks like YAML but PyYAML is not installed",
            hint="pip install 'openapi-mock[yaml]' or convert the spec to JSON",
        ) from exc
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1} column {mark.column + 1}" if mark else ""
        problem = getattr(exc, "problem", None) or "invalid YAML"
        raise SpecError(f"{name}: invalid YAML{where}: {problem}") from exc


def compile_path(template: str) -> tuple[re.Pattern[str], tuple[str, ...]]:
    """Turn '/pets/{petId}/toys' into a regex with named groups."""
    names: list[str] = []
    chunks: list[str] = []
    for segment in template.split("/"):
        if not segment:
            continue
        match = re.fullmatch(r"\{([A-Za-z0-9_.-]+)\}", segment)
        if match:
            names.append(match.group(1))
            chunks.append(r"(?P<" + match.group(1) + r">[^/]+)")
        else:
            chunks.append(re.escape(segment))
    pattern = re.compile("/" + "/".join(chunks) + r"/?")
    return pattern, tuple(names)


def _default_operation_id(method: str, path: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
    return f"{method}_{cleaned}".lower()


def _merge_parameters(path_level: list[Any], op_level: list[Any]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in [*path_level, *op_level]:
        if not isinstance(raw, dict):
            continue
        key = (str(raw.get("in", "")), str(raw.get("name", "")))
        merged[key] = raw  # operation-level wins because it is applied last
    return list(merged.values())


def load_spec(path: str | os.PathLike[str]) -> Spec:
    """Load and validate an OpenAPI 3.0/3.1 document from JSON or YAML."""
    spec_path = pathlib.Path(path)
    if not spec_path.exists():
        raise SpecError(
            f"spec file not found: {path}",
            hint="pass a path to an OpenAPI 3.x .json/.yaml file",
        )
    if spec_path.is_dir():
        raise SpecError(f"spec path is a directory, not a file: {path}")
    try:
        raw = spec_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SpecError(f"{spec_path.name}: file is not valid UTF-8 text") from exc
    except OSError as exc:
        raise SpecError(f"cannot read {spec_path.name}: {exc.strerror}") from exc

    document = parse_document(raw, spec_path.name)
    if not isinstance(document, dict):
        raise SpecError(
            f"{spec_path.name}: the document root must be an object, got {type(document).__name__}"
        )
    _check_envelope(document, spec_path.name)
    warnings: list[str] = []

    resolved_path = spec_path.resolve()
    resolver = Resolver(resolved_path, document)
    resolved = resolver.resolve(document, resolved_path.parent, frozenset())
    paths = resolved.get("paths") or {}
    if not isinstance(paths, dict):
        raise SpecError(f"{spec_path.name}: 'paths' must be an object")

    operations: list[Operation] = []
    seen_ids: dict[str, str] = {}
    for route, path_item in paths.items():
        if not isinstance(route, str) or not route.startswith("/"):
            warnings.append(f"skipping path '{route}': paths must start with '/'")
            continue
        if not isinstance(path_item, dict):
            warnings.append(f"skipping path '{route}': path item must be an object")
            continue
        pattern, param_names = compile_path(route)
        path_params = path_item.get("parameters") or []
        for method in HTTP_METHODS:
            raw_op = path_item.get(method)
            if not isinstance(raw_op, dict):
                continue
            op_id = str(raw_op.get("operationId") or _default_operation_id(method, route))
            if op_id in seen_ids:
                warnings.append(
                    f"duplicate operationId '{op_id}' "
                    f"({seen_ids[op_id]} and {method.upper()} {route})"
                )
            seen_ids.setdefault(op_id, f"{method.upper()} {route}")
            parameters = _merge_parameters(path_params, raw_op.get("parameters") or [])
            declared = {p.get("name") for p in parameters if p.get("in") == "path"}
            for name in param_names:
                if name not in declared:
                    warnings.append(
                        f"{method.upper()} {route}: path parameter '{{{name}}}' has no "
                        "parameter definition"
                    )
            body = raw_op.get("requestBody")
            responses = raw_op.get("responses") or {}
            if not isinstance(responses, dict) or not responses:
                warnings.append(
                    f"{method.upper()} {route}: no responses declared, the mock returns 204"
                )
            op_security = raw_op.get("security")
            operations.append(
                Operation(
                    method=method,
                    path=route,
                    operation_id=op_id,
                    summary=str(raw_op.get("summary") or ""),
                    description=str(raw_op.get("description") or ""),
                    deprecated=bool(raw_op.get("deprecated")),
                    tags=tuple(str(t) for t in (raw_op.get("tags") or [])),
                    parameters=tuple(parameters),
                    request_body=body if isinstance(body, dict) else None,
                    responses=dict(responses),
                    security=tuple(op_security) if isinstance(op_security, list) else None,
                    pattern=pattern,
                    param_names=param_names,
                )
            )

    operations.sort(key=_route_priority)
    if not operations:
        warnings.append("the document declares no operations; the mock answers 404 for every route")

    schemes = (resolved.get("components") or {}).get("securitySchemes") or {}
    for name, scheme in schemes.items():
        kind = scheme.get("type") if isinstance(scheme, dict) else None
        if kind in ("oauth2", "openIdConnect"):
            warnings.append(
                f"security scheme '{name}' ({kind}) is reported in summary but not enforced"
            )
    warnings.extend(_scan_unsupported(resolved))
    info = resolved.get("info") if isinstance(resolved.get("info"), dict) else {}
    servers = [
        str(server["url"])
        for server in (resolved.get("servers") or [])
        if isinstance(server, dict) and server.get("url")
    ]
    return Spec(
        path=str(spec_path),
        document=resolved,
        title=str(info.get("title") or spec_path.stem),
        version=str(info.get("version") or "0.0.0"),
        openapi_version=str(document.get("openapi")),
        operations=tuple(operations),
        security_schemes=dict(schemes),
        warnings=tuple(list(dict.fromkeys(warnings))),
        servers=tuple(servers),
    )


def _check_envelope(document: dict[str, Any], name: str) -> str:
    if "swagger" in document:
        raise SpecError(
            f"{name}: Swagger {document.get('swagger')} is not supported",
            hint="openapi-mock targets OpenAPI 3.0/3.1; convert with 'swagger2openapi' first",
        )
    version = document.get("openapi")
    if version is None:
        raise SpecError(
            f"{name}: not an OpenAPI document (missing the 'openapi' field)",
            hint='expected {"openapi": "3.0.3", "info": {...}, "paths": {...}}',
        )
    if not isinstance(version, str) or not version.startswith(SUPPORTED_MAJORS):
        raise SpecError(
            f"{name}: unsupported OpenAPI version '{version}' (supported: 3.0.x, 3.1.x)"
        )
    if "paths" not in document and "webhooks" in document:
        raise SpecError(
            f"{name}: only webhooks are declared; openapi-mock needs 'paths' to serve routes"
        )
    return version


METHOD_ORDER = {"get": 0, "post": 1, "put": 2, "patch": 3, "delete": 4, "head": 5}


def _route_priority(op: Operation) -> tuple[int, str, int]:
    """Static routes before templated ones, then alphabetical path, then method.

    Matching still prefers static routes because the spec is pre-sorted by this
    key, which keeps the endpoint table and the router in the same order.
    """
    templated = sum(1 for part in op.path.split("/") if part.startswith("{"))
    return (templated, op.path, METHOD_ORDER.get(op.method, 9))


def _scan_unsupported(document: dict[str, Any]) -> list[str]:
    found: set[str] = set()

    def walk(node: Any, trail: str) -> None:
        if isinstance(node, dict):
            for keyword in UNSUPPORTED_SCHEMA_KEYWORDS:
                if keyword in node:
                    found.add(
                        f"schema keyword '{keyword}' at {trail or '/'} is ignored by validation"
                    )
            if "discriminator" in node and trail.startswith("#/components/schemas"):
                found.add("'discriminator' is reported but not used to pick oneOf branches")
            for key, value in node.items():
                walk(value, f"{trail}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{trail}/{index}")

    for section in ("components", "paths"):
        walk(document.get(section, {}), "#/" + section)
    return sorted(found)

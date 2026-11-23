"""The threaded stdlib mock server.

Deliberately built on http.server: no framework, no async runtime, one file a
reader can finish. That costs throughput a mock does not need (it serves one
developer at a time) and buys a dependency-free install.
"""

from __future__ import annotations

import json
import random
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import colors
from .generate import Generator
from .loader import Operation, Spec
from .requests import Record, Violation, check_record, parse_url, response_schema
from .validator import validate


@dataclass
class ServerConfig:
    spec: Spec
    seed: int = 0
    latency_ms: int = 0
    error_rate: float = 0.0
    stateful: bool = False
    cors: bool = False
    record_path: Path | None = None
    verbose: bool = True
    max_body_bytes: int = 1_048_576
    rng: random.Random = field(default_factory=lambda: random.Random(0))

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed * 7919 + 13)


def problem(
    status: int,
    title: str,
    detail: str,
    instance: str,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """RFC 7807 problem+json body; the same shape is used for every 4xx."""
    body: dict[str, Any] = {
        "type": "https://openapi-mock.dev/problems/" + title.lower().replace(" ", "-"),
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
    }
    if errors:
        body["errors"] = errors
    return body


class StateStore:
    """Objects created by POST so later GETs can return them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._objects: dict[str, list[dict[str, Any]]] = {}
        self._by_id: dict[tuple[str, str], dict[str, Any]] = {}

    def add(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            record = dict(payload)
            key = str(record.get("id") or uuid.uuid4().hex[:8])
            record["id"] = key
            self._objects.setdefault(path, []).insert(0, record)
            self._by_id[(path, key)] = record
            return record

    def items(self, path: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._objects.get(path, [])]

    def get(self, path: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._by_id.get((path, str(key)))
            return dict(found) if found else None


class MockHandler(BaseHTTPRequestHandler):
    """One handler instance per request; shared state lives on the server."""

    protocol_version = "HTTP/1.1"
    server_version = "openapi-mock/1.0"
    config: ServerConfig

    def log_message(self, format: str, *args: Any) -> None:
        return  # the structured access log below is the only output we want

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self._apply_cors()
        self.end_headers()

    def _apply_cors(self) -> None:
        if self.config.cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")

    def _send_json(self, status: int, payload: Any, extra: dict[str, str] | None = None) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        headers = dict(extra or {})
        content_type = headers.pop("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        for key, value in headers.items():
            self.send_header(key, value)
        self._apply_cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _send_problem(
        self,
        status: int,
        title: str,
        detail: str,
        errors: list[dict[str, str]] | None = None,
    ) -> None:
        body = problem(status, title, detail, self.path.split("?")[0], errors)
        self._send_json(status, body, {"Content-Type": "application/problem+json; charset=utf-8"})

    def _read_body(self) -> tuple[Any, str | None]:
        raw_length = self.headers.get("Content-Length")
        if not raw_length:
            return None, None
        try:
            length = int(raw_length)
        except ValueError:
            return None, "invalid Content-Length header"
        if length > self.config.max_body_bytes:
            return None, "request body exceeds " + str(self.config.max_body_bytes) + " bytes"
        raw = self.rfile.read(length)
        if not raw.strip():
            return None, None
        try:
            return json.loads(raw.decode("utf-8")), None
        except UnicodeDecodeError:
            return None, "request body is not UTF-8"
        except json.JSONDecodeError as exc:
            return None, "request body is not valid JSON: " + exc.msg

    def _dispatch(self, method: str) -> None:
        started = time.perf_counter()
        path, query = parse_url(self.path)
        if self.config.latency_ms > 0:
            # Sleep before answering: a delay applied after the response body is
            # already on the wire is not observable by the client.
            time.sleep(self.config.latency_ms / 1000.0)
        status = 500
        try:
            status = self._handle(method, path, query)
        except (BrokenPipeError, ConnectionResetError):
            status = 499
        finally:
            elapsed = (time.perf_counter() - started) * 1000.0
            self._log(method, self.path, status, elapsed)

    def _log(self, method: str, target: str, status: int, elapsed: float) -> None:
        if not self.config.verbose:
            return
        suffix = str(round(elapsed, 1)) + "ms"
        if self.config.latency_ms:
            suffix += "/" + str(self.config.latency_ms) + "ms"
        print(
            colors.dim("  ")
            + colors.method(method)
            + " "
            + target.ljust(34)
            + colors.status(status)
            + colors.dim("  " + suffix),
            flush=True,
        )

    def _handle(self, method: str, path: str, query: dict[str, list[str]]) -> int:
        spec = self.config.spec
        hit = spec.match(method, path)
        if hit is None and method == "HEAD":
            hit = spec.match("get", path)
        if hit is None:
            allowed = [verb for verb in ("GET", "POST", "PUT", "PATCH", "DELETE")]
            if any(spec.match(verb.lower(), path) is not None for verb in allowed):
                self._send_problem(
                    405, "Method Not Allowed", method + " is not defined for " + path
                )
                self._record(method, path, query, None, 405)
                return 405
            self._send_problem(404, "Not Found", "no operation matches " + path)
            self._record(method, path, query, None, 404)
            return 404
        operation, path_values = hit
        # HEAD is served by the GET operation, so validate against the GET method.
        effective = "GET" if method == "HEAD" else method

        body, body_error = self._read_body()
        if body_error is not None:
            self._send_problem(422, "Unprocessable Entity", body_error)
            self._record(method, path, query, body, 422)
            return 422

        violations = self._check_request(operation, effective, path, query, body)
        if violations:
            self._send_problem(
                422,
                "Unprocessable Entity",
                "request does not satisfy " + operation.operation_id,
                [{"location": item.kind, "detail": item.detail} for item in violations],
            )
            self._record(method, path, query, body, 422)
            return 422

        if self.config.error_rate > 0 and self.config.rng.random() < self.config.error_rate:
            self._send_problem(
                500,
                "Injected Failure",
                "the mock injected a fault (--error-rate " + str(self.config.error_rate) + ")",
            )
            self._record(method, path, query, body, 500)
            return 500

        status, payload = self._success(operation, method, path, path_values, body)
        self._send_json(status, payload, {"X-Mock-Seed": str(self.config.seed)})
        self._record(method, path, query, body, status)
        return status

    def _check_request(
        self,
        operation: Operation,
        method: str,
        path: str,
        query: dict[str, list[str]],
        body: Any,
    ) -> list[Violation]:
        record = Record(
            method=method,
            path=path,
            query=query,
            headers={str(k): str(v) for k, v in self.headers.items()},
            body=body,
        )
        return check_record(self.config.spec, record)

    def _success(
        self,
        operation: Operation,
        method: str,
        path: str,
        path_values: dict[str, str],
        body: Any,
    ) -> tuple[int, Any]:
        chosen = operation.success_response()
        if chosen is None:
            return 204, None
        status_text, _response = chosen
        status = int(status_text) if status_text.isdigit() else 200
        schema = response_schema(operation, status_text)
        if schema is None:
            return (204, None) if status in (204, 205) else (status, {})
        if self.config.stateful:
            store: StateStore = self.server.state_store
            served = self._stateful(method, path, path_values, schema, body, store)
            if served is not None:
                return status, served
        generator = Generator(seed=self.config.seed)
        payload = generator.sample(schema, operation.operation_id)
        if method in ("POST", "PUT", "PATCH") and isinstance(body, dict):
            payload = echo_request(payload, body)
        payload = echo_path_identifier(payload, schema, path_values)
        return status, payload

    def _stateful(
        self,
        method: str,
        path: str,
        path_values: dict[str, str],
        schema: dict[str, Any],
        body: Any,
        store: StateStore,
    ) -> Any:
        collection = collection_path(path)
        if method == "POST" and isinstance(body, dict):
            # Fill the skeleton first so generated readOnly fields (a uuid id,
            # timestamps) exist and satisfy the schema the client will GET later.
            skeleton = fill_created(body, schema, self.config.seed)
            return store.add(collection, skeleton)
        if method != "GET":
            return None
        if path_values:
            key = next(iter(path_values.values()))
            return store.get(collection, key)
        items = store.items(collection)
        if not items:
            return None
        if schema.get("type") == "array":
            return items
        properties = schema.get("properties")
        if isinstance(properties, dict) and "items" in properties:
            envelope = Generator(seed=self.config.seed).sample(schema, "stateful")
            if isinstance(envelope, dict):
                envelope["items"] = items
                return envelope
        return None

    def _record(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        body: Any,
        status: int,
    ) -> None:
        target = self.config.record_path
        if target is None:
            return
        record = Record(
            method=method,
            path=path,
            query=query,
            headers={
                key: value
                for key, value in self.headers.items()
                if key.lower() in ("authorization", "accept", "content-type", "x-request-id")
            },
            body=body,
            status=status,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")


def echo_path_identifier(payload: Any, schema: dict[str, Any], path_values: dict[str, str]) -> Any:
    """GET /pets/{petId} answers with id == petId, when the schema allows it.

    Returning a random id for a resource the client addressed by id is a classic
    mock-server tell. The value is only echoed when the response schema's 'id'
    property accepts it, so we never trade realism for an invalid payload.
    """
    if not path_values or not isinstance(payload, dict):
        return payload
    properties = schema.get("properties")
    if not isinstance(properties, dict) or "id" not in payload:
        return payload
    identifier = next(iter(path_values.values()))
    id_schema = properties.get("id")
    if not isinstance(id_schema, dict):
        return payload
    if validate(identifier, id_schema, "id"):
        return payload
    merged = dict(payload)
    merged["id"] = identifier
    return merged


def echo_request(payload: Any, body: dict[str, Any]) -> Any:
    """Write-through: a create/update returns the values the client just sent.

    Mock servers that ignore the request body force frontend engineers to distrust
    every response. We overlay the sent fields on the generated object instead, so
    POST /pets with name=Mochi answers with name=Mochi and fills the rest.
    """
    if not isinstance(payload, dict):
        return payload
    merged = dict(payload)
    for key, value in body.items():
        if key in merged:
            merged[key] = value
    return merged


def fill_created(created: dict[str, Any], schema: dict[str, Any], seed: int) -> dict[str, Any]:
    """Overlay the created object on a generated skeleton so no field is missing."""
    skeleton = Generator(seed=seed).sample(schema, "created")
    if not isinstance(skeleton, dict):
        return created
    skeleton.update(created)
    return skeleton


def collection_path(path: str) -> str:
    """'/pets/abc123' and '/pets' both address the '/pets' collection."""
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and looks_like_identifier(parts[-1]):
        return "/" + "/".join(parts[:-1])
    return "/" + "/".join(parts)


def looks_like_identifier(part: str) -> bool:
    if not part:
        return False
    if part.isdigit():
        return True
    if len(part) >= 8 and all(char in "0123456789abcdef-" for char in part.lower()):
        return True
    return False


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: ServerConfig) -> None:
        self.config = config
        self.state_store = StateStore()
        super().__init__(address, _make_handler(config))
        host, port = self.server_address[0], self.server_address[1]
        self.base_url = "http://" + str(host) + ":" + str(port)


def _make_handler(config: ServerConfig) -> type[MockHandler]:
    return type("BoundMockHandler", (MockHandler,), {"config": config})


def bind(host: str, port: int, config: ServerConfig) -> MockServer:
    """Create (but do not start) a server; port 0 asks the OS for a free port."""
    return MockServer((host, port), config)


def serve_forever(server: MockServer) -> None:
    server.serve_forever(poll_interval=0.05)


def run_in_thread(server: MockServer) -> threading.Thread:
    thread = threading.Thread(target=serve_forever, args=(server,), daemon=True)
    thread.start()
    wait_until_ready(server, timeout=5.0)
    return thread


def shutdown(server: MockServer) -> None:
    """Stop the accept loop and release the socket; safe to call twice."""
    server.shutdown()
    server.server_close()


def wait_until_ready(server: MockServer, timeout: float = 5.0) -> None:
    """Poll the bound socket so callers never race the accept loop."""
    deadline = time.monotonic() + timeout
    host, port = server.server_address[0], server.server_address[1]
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.01)
    raise RuntimeError("mock server did not become ready within " + str(timeout) + "s")


def http_get(url: str, timeout: float = 5.0) -> tuple[int, str, dict[str, str]]:
    """Minimal GET used by the CLI self-check, the tests and the benchmark."""
    request = urllib.request.Request(url, method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read().decode("utf-8")
            return response.status, body, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, body, dict(exc.headers.items())

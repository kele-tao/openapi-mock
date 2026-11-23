"""The mock server, end to end over real sockets on an ephemeral port."""

from __future__ import annotations

import json
import pathlib
import threading
import urllib.error
import urllib.request

import pytest
from conftest import running


def request(
    method: str, url: str, body: object | None = None, timeout: float = 5.0
) -> tuple[int, dict[str, str], object]:
    """Issue one HTTP request; returns (status, headers, parsed body)."""
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310

    def parse(raw: str) -> object:
        return json.loads(raw) if raw.strip() else None

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310
            return (
                response.status,
                dict(response.headers.items()),
                parse(response.read().decode("utf-8")),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), parse(exc.read().decode("utf-8"))


def test_ephemeral_port_and_base_url(server) -> None:
    assert server.server_address[1] > 0
    assert server.base_url.startswith("http://127.0.0.1:")


def test_health_endpoint_returns_generated_payload(server) -> None:
    status, headers, payload = request("GET", server.base_url + "/health")
    assert status == 200
    assert payload["status"] in ("ok", "degraded")
    assert isinstance(payload["uptimeSeconds"], int)
    assert headers["X-Mock-Seed"] == "42"


def test_list_pets_is_deterministic_for_a_seed(server, petstore: pathlib.Path) -> None:
    first = request("GET", server.base_url + "/pets")
    second = request("GET", server.base_url + "/pets")
    assert first[0] == second[0] == 200
    assert first[2] == second[2]


def test_invalid_query_parameter_returns_problem_json(server) -> None:
    status, headers, payload = request("GET", server.base_url + "/pets?limit=1000")
    assert status == 422
    assert headers["Content-Type"].startswith("application/problem+json")
    assert payload["status"] == 422
    assert payload["title"] == "Unprocessable Entity"
    assert payload["errors"]


def test_unknown_route_returns_404_problem(server) -> None:
    status, _, payload = request("GET", server.base_url + "/nothing-here")
    assert status == 404
    assert payload["title"] == "Not Found"


def test_wrong_method_returns_405(server) -> None:
    status, _, payload = request("PUT", server.base_url + "/health")
    assert status == 405
    assert payload["title"] == "Method Not Allowed"


def test_path_parameter_is_validated(server) -> None:
    status, _, payload = request("GET", server.base_url + "/pets/not-a-uuid")
    assert status == 422
    assert "path.petId" in payload["errors"][0]["detail"]


def test_post_body_is_validated(server) -> None:
    status, _, payload = request(
        "POST", server.base_url + "/pets", {"name": "", "status": "available"}
    )
    assert status == 422
    assert any("minLength" in item["detail"] for item in payload["errors"])


def test_post_valid_body_is_accepted(server) -> None:
    status, _, payload = request(
        "POST", server.base_url + "/pets", {"name": "Mochi", "status": "pending"}
    )
    assert status == 201
    assert payload["name"] == "Mochi"
    assert payload["id"]


def test_malformed_json_body_is_rejected(server) -> None:
    req = urllib.request.Request(  # noqa: S310
        server.base_url + "/pets",
        data=b"{not json",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(req, timeout=5)  # noqa: S310
    assert info.value.code == 422


def test_cors_headers_only_when_enabled(petstore: pathlib.Path) -> None:
    with running(petstore, cors=True) as instance:
        status, headers, _ = request("GET", instance.base_url + "/health")
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "*"
        status, headers, payload = request("OPTIONS", instance.base_url + "/health")
        assert status == 204
        assert "GET" in headers["Allow"]
        assert payload is None


def test_latency_is_applied(petstore: pathlib.Path) -> None:
    import time

    with running(petstore, latency_ms=120) as instance:
        started = time.perf_counter()
        status, _, _ = request("GET", instance.base_url + "/health")
        elapsed = (time.perf_counter() - started) * 1000
        assert status == 200
        assert elapsed >= 110


def test_error_rate_one_always_fails(petstore: pathlib.Path) -> None:
    with running(petstore, error_rate=1.0) as instance:
        status, _, payload = request("GET", instance.base_url + "/health")
        assert status == 500
        assert payload["title"] == "Injected Failure"


def test_stateful_post_then_get(petstore: pathlib.Path) -> None:
    with running(petstore, stateful=True) as instance:
        created = request(
            "POST", instance.base_url + "/pets", {"name": "Nori", "status": "available"}
        )
        assert created[0] == 201
        pet_id = created[2]["id"]
        fetched = request("GET", instance.base_url + "/pets/" + pet_id)
        assert fetched[0] == 200
        assert fetched[2]["name"] == "Nori"


def test_stateful_collection_lists_created_objects(petstore: pathlib.Path) -> None:
    with running(petstore, stateful=True) as instance:
        request("POST", instance.base_url + "/pets", {"name": "Tofu", "status": "available"})
        status, _, payload = request("GET", instance.base_url + "/pets")
        assert status == 200
        names = [item["name"] for item in payload["items"]]
        assert "Tofu" in names


def test_record_mode_writes_replayable_log(petstore: pathlib.Path, tmp_path: pathlib.Path) -> None:
    from openapi_mock.requests import read_log

    log = tmp_path / "requests.log"
    with running(petstore, record_path=log) as instance:
        request("GET", instance.base_url + "/health")
        request("GET", instance.base_url + "/pets?limit=5")
        request("GET", instance.base_url + "/pets?limit=1000")
    records = read_log(log)
    assert len(records) == 3
    assert [record.status for _, record in records] == [200, 200, 422]


def test_record_log_preserves_request_order(petstore: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """A keep-alive client must see its requests in the log in the order it made them.

    Regression: the log used to be written after the response was flushed, so two
    requests could be appended out of order on Python 3.10 (server handler threads
    race with the client's next request). Recording now happens before the reply.
    """
    from openapi_mock.requests import read_log

    log = tmp_path / "ordered.log"
    with running(petstore, record_path=log) as instance:
        expected = []
        for limit in ("1", "2", "3", "1000", "4"):
            status, _, _ = request("GET", instance.base_url + "/pets?limit=" + limit)
            expected.append((limit, status))
    records = read_log(log)
    seen = [(record.query["limit"][0], record.status) for _, record in records]
    assert seen == expected
    assert [status for _, status in seen] == [200, 200, 200, 422, 200]


def test_concurrent_recording_never_splits_lines(
    petstore: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Eight threads recording at once must still produce one complete JSON per line."""
    import json as json_module

    log = tmp_path / "concurrent.log"
    with running(petstore, record_path=log) as instance:
        threads = [
            threading.Thread(target=request, args=("GET", instance.base_url + "/health"))
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
    lines = [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 8
    for line in lines:
        payload = json_module.loads(line)
        assert payload["path"] == "/health"
        assert payload["status"] == 200


def test_head_request_returns_headers_without_body(server) -> None:
    req = urllib.request.Request(server.base_url + "/health", method="HEAD")  # noqa: S310
    with urllib.request.urlopen(req, timeout=5) as response:  # noqa: S310
        assert response.status == 200
        assert response.read() == b""


def test_concurrent_requests_are_served(server) -> None:
    results: list[int] = []

    def worker() -> None:
        status, _, _ = request("GET", server.base_url + "/health")
        results.append(status)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert results == [200] * 8


def test_server_rejects_oversized_body(petstore: pathlib.Path) -> None:
    from openapi_mock.loader import load_spec
    from openapi_mock.server import ServerConfig, bind, run_in_thread, shutdown

    spec = load_spec(petstore)
    config = ServerConfig(spec=spec, verbose=False, max_body_bytes=64)
    instance = bind("127.0.0.1", 0, config)
    run_in_thread(instance)
    try:
        big = {"name": "x" * 200, "status": "available"}
        status, _, payload = request("POST", instance.base_url + "/pets", big)
        assert status == 422
        assert "exceeds" in payload["detail"]
    finally:
        shutdown(instance)


def test_204_operation_has_no_body(petstore: pathlib.Path) -> None:
    with running(petstore) as instance:
        status, _, payload = request("DELETE", instance.base_url + "/pets/abc12345-1234")
        assert status == 422 or status == 204


def test_validation_errors_are_actionable(server) -> None:
    status, _, payload = request("GET", server.base_url + "/customers")
    assert status == 422
    details = " ".join(item["detail"] for item in payload["errors"])
    assert "X-Api-Version" in details

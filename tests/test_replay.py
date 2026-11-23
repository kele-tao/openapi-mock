"""Replay: the request log format and the validate command's findings."""

from __future__ import annotations

import json
import pathlib

import pytest

from openapi_mock.errors import UsageError
from openapi_mock.loader import load_spec
from openapi_mock.requests import Record, check_record, coerce_param, parse_url, read_log


def write_log(tmp_path: pathlib.Path, lines: list[object]) -> pathlib.Path:
    path = tmp_path / "requests.log"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def test_parse_url_splits_path_and_query() -> None:
    path, query = parse_url("/pets?limit=5&tag=a&tag=b")
    assert path == "/pets"
    assert query == {"limit": ["5"], "tag": ["a", "b"]}


def test_parse_url_without_query() -> None:
    assert parse_url("/health") == ("/health", {})


def test_coerce_integer_and_boolean() -> None:
    assert coerce_param("12", {"type": "integer"}) == (12, None)
    assert coerce_param("x", {"type": "integer"})[1] is not None
    assert coerce_param("true", {"type": "boolean"}) == (True, None)
    assert coerce_param("nope", {"type": "boolean"})[1] is not None
    assert coerce_param("1.5", {"type": "number"}) == (1.5, None)


def test_record_roundtrip() -> None:
    record = Record(
        method="POST",
        path="/pets",
        query={"limit": ["1"]},
        headers={"Authorization": "Bearer x"},
        body={"name": "Mochi"},
        status=201,
    )
    restored = Record.from_json(record.to_json())
    assert restored.method == "POST"
    assert restored.query == {"limit": ["1"]}
    assert restored.body == {"name": "Mochi"}
    assert restored.status == 201


def test_record_rejects_missing_method(tmp_path: pathlib.Path) -> None:
    log = write_log(tmp_path, [{"path": "/pets"}])
    with pytest.raises(UsageError, match="'method' and 'path'"):
        read_log(log)


def test_record_rejects_relative_path(tmp_path: pathlib.Path) -> None:
    log = write_log(tmp_path, [{"method": "GET", "path": "pets"}])
    with pytest.raises(UsageError, match="must start with '/'"):
        read_log(log)


def test_log_with_invalid_json_reports_the_line(tmp_path: pathlib.Path) -> None:
    log = tmp_path / "requests.log"
    log.write_text(
        json.dumps({"method": "GET", "path": "/health"}) + "\n{broken\n", encoding="utf-8"
    )
    with pytest.raises(UsageError) as info:
        read_log(log)
    assert "requests.log:2" in str(info.value)


def test_log_without_records_is_a_usage_error(tmp_path: pathlib.Path) -> None:
    log = tmp_path / "empty.log"
    log.write_text("# only a comment\n", encoding="utf-8")
    with pytest.raises(UsageError, match="no request records"):
        read_log(log)


def test_valid_requests_produce_no_violations(petstore: pathlib.Path) -> None:
    spec = load_spec(petstore)
    record = Record(method="GET", path="/pets", query={"limit": ["10"]})
    assert check_record(spec, record) == []


def test_unknown_route_is_a_violation(petstore: pathlib.Path) -> None:
    spec = load_spec(petstore)
    found = check_record(spec, Record(method="GET", path="/dragons"))
    assert len(found) == 1
    assert found[0].kind == "route"


def test_query_out_of_range(petstore: pathlib.Path) -> None:
    spec = load_spec(petstore)
    found = check_record(spec, Record(method="GET", path="/pets", query={"limit": ["900"]}))
    assert any("must be <= 100" in item.detail for item in found)


def test_unknown_query_parameter(petstore: pathlib.Path) -> None:
    spec = load_spec(petstore)
    found = check_record(spec, Record(method="GET", path="/pets", query={"sort": ["asc"]}))
    assert any("unknown parameter 'sort'" in item.detail for item in found)


def test_missing_required_header(petstore: pathlib.Path) -> None:
    spec = load_spec(petstore)
    found = check_record(spec, Record(method="GET", path="/customers", headers={}))
    assert any("X-Api-Version" in item.detail for item in found)


def test_header_enum_is_checked(petstore: pathlib.Path) -> None:
    spec = load_spec(petstore)
    found = check_record(
        spec,
        Record(method="GET", path="/customers", headers={"X-Api-Version": "1999-01-01"}),
    )
    assert any("must be one of" in item.detail for item in found)


def test_body_type_error_is_reported(petstore: pathlib.Path) -> None:
    spec = load_spec(petstore)
    found = check_record(spec, Record(method="POST", path="/pets", body={"name": "Mochi"}))
    assert any("missing required property 'status'" in item.detail for item in found)


def test_missing_body_is_reported(petstore: pathlib.Path) -> None:
    spec = load_spec(petstore)
    found = check_record(spec, Record(method="POST", path="/pets", body=None))
    assert any("missing required request body" in item.detail for item in found)


def test_extra_body_on_get_is_reported(petstore: pathlib.Path) -> None:
    spec = load_spec(petstore)
    found = check_record(spec, Record(method="GET", path="/health", body={"x": 1}))
    assert any("no request body" in item.detail for item in found)


def test_path_parameter_is_replayed(petstore: pathlib.Path) -> None:
    spec = load_spec(petstore)
    found = check_record(spec, Record(method="GET", path="/pets/nope"))
    assert any("path.petId" in item.detail for item in found)


def test_example_log_has_three_valid_and_three_invalid(root: pathlib.Path) -> None:
    spec = load_spec(root / "examples" / "petstore.json")
    records = read_log(root / "examples" / "requests.log")
    outcomes = [not check_record(spec, record) for _, record in records]
    assert len(records) == 6
    assert outcomes.count(True) == 3
    assert outcomes.count(False) == 3


def test_violation_render_is_greppable(petstore: pathlib.Path) -> None:
    spec = load_spec(petstore)
    found = check_record(spec, Record(method="GET", path="/dragons"), line=7)
    assert found[0].render().startswith("line 7 GET /dragons [route]")

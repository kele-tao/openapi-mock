"""Loader: envelopes, $refs, YAML, path compilation and error messages."""

from __future__ import annotations

import copy
import json
import pathlib

import pytest

from openapi_mock.errors import SpecError
from openapi_mock.loader import compile_path, load_spec, parse_document

MINIMAL = {
    "openapi": "3.0.3",
    "info": {"title": "Tiny", "version": "1.0.0"},
    "paths": {
        "/ping": {
            "get": {
                "operationId": "ping",
                "responses": {"200": {"description": "pong"}},
            }
        }
    },
}


def write(tmp_path: pathlib.Path, payload: object, name: str = "spec.json") -> pathlib.Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def test_loads_minimal_spec(tmp_path: pathlib.Path) -> None:
    spec = load_spec(write(tmp_path, MINIMAL))
    assert spec.title == "Tiny"
    assert spec.version == "1.0.0"
    assert len(spec.operations) == 1
    assert spec.operations[0].operation_id == "ping"
    assert spec.operations[0].method == "get"


def test_swagger_two_is_rejected_with_a_hint(tmp_path: pathlib.Path) -> None:
    spec = load_spec
    path = write(tmp_path, {"swagger": "2.0", "info": {}, "paths": {}})
    with pytest.raises(SpecError) as info:
        spec(path)
    assert "Swagger 2.0" in str(info.value)
    assert info.value.hint is not None


def test_missing_openapi_field_is_explained(tmp_path: pathlib.Path) -> None:
    path = write(tmp_path, {"info": {"title": "x"}, "paths": {}})
    with pytest.raises(SpecError, match="missing the 'openapi' field"):
        load_spec(path)


def test_missing_file_reports_the_path(tmp_path: pathlib.Path) -> None:
    with pytest.raises(SpecError, match="spec file not found"):
        load_spec(tmp_path / "nope.json")


def test_invalid_json_names_line_and_column(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"openapi": "3.0.0",,}', encoding="utf-8")
    with pytest.raises(SpecError) as info:
        load_spec(path)
    assert "invalid JSON at line" in str(info.value)


def test_empty_file_is_rejected(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(SpecError, match="file is empty"):
        load_spec(path)


def test_local_ref_and_relative_file_ref(tmp_path: pathlib.Path) -> None:
    (tmp_path / "shared.json").write_text(
        json.dumps(
            {
                "Thing": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "string", "format": "uuid"}},
                }
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "openapi": "3.1.0",
        "info": {"title": "Refs", "version": "2.0.0"},
        "paths": {
            "/things": {
                "get": {
                    "operationId": "listThings",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "./shared.json#/Thing"},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }
    spec = load_spec(write(tmp_path, payload))
    operation = spec.operations[0]
    chosen = operation.success_response()
    assert chosen is not None
    schema = chosen[1]["content"]["application/json"]["schema"]
    assert schema["items"]["properties"]["id"]["format"] == "uuid"


def test_remote_ref_is_refused_offline(tmp_path: pathlib.Path) -> None:
    payload = copy.deepcopy(MINIMAL)
    payload["components"] = {"schemas": {"X": {"$ref": "https://example.com/schema.json"}}}
    with pytest.raises(SpecError, match="remote \\$ref is not supported"):
        load_spec(write(tmp_path, payload))


def test_unknown_pointer_names_the_missing_token(tmp_path: pathlib.Path) -> None:
    payload = copy.deepcopy(MINIMAL)
    payload["components"] = {"schemas": {"Known": {"type": "string"}}}
    payload["paths"]["/ping"]["get"]["responses"]["200"]["content"] = {
        "application/json": {"schema": {"$ref": "#/components/schemas/Nope"}}
    }
    with pytest.raises(SpecError, match="'Nope' does not exist"):
        load_spec(write(tmp_path, payload))


def test_recursive_ref_does_not_hang(tmp_path: pathlib.Path) -> None:
    payload = {
        "openapi": "3.0.0",
        "info": {"title": "Recursive", "version": "1.0.0"},
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "child": {"$ref": "#/components/schemas/Node"},
                    },
                }
            }
        },
        "paths": {
            "/nodes": {
                "get": {
                    "operationId": "nodes",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Node"}
                                }
                            },
                        }
                    },
                }
            }
        },
    }
    spec = load_spec(write(tmp_path, payload))
    assert spec.operations[0].operation_id == "nodes"


def test_yaml_spec_loads(tmp_path: pathlib.Path) -> None:
    pytest.importorskip("yaml")
    path = tmp_path / "spec.yaml"
    path.write_text(
        "openapi: 3.0.3\n"
        "info:\n  title: Yaml API\n  version: 0.1.0\n"
        "paths:\n"
        "  /things:\n"
        "    get:\n"
        "      operationId: listThings\n"
        "      responses:\n"
        "        '200':\n"
        "          description: ok\n",
        encoding="utf-8",
    )
    spec = load_spec(path)
    assert spec.title == "Yaml API"
    assert spec.operations[0].path == "/things"


def test_parse_document_detects_yaml_by_extension() -> None:
    with pytest.raises(SpecError, match="invalid YAML"):
        parse_document("openapi: [3.0.3\n", "broken.yaml")


def test_compile_path_extracts_named_parameters() -> None:
    pattern, names = compile_path("/pets/{petId}/toys/{toyId}")
    assert names == ("petId", "toyId")
    hit = pattern.fullmatch("/pets/abc/toys/9")
    assert hit is not None
    assert hit.group("petId") == "abc"
    assert pattern.fullmatch("/pets/abc/toys") is None


def test_undeclared_path_parameter_produces_a_warning(tmp_path: pathlib.Path) -> None:
    payload = {
        "openapi": "3.0.0",
        "info": {"title": "Warn", "version": "1.0.0"},
        "paths": {
            "/pets/{petId}": {
                "get": {"operationId": "getPet", "responses": {"200": {"description": "ok"}}}
            }
        },
    }
    spec = load_spec(write(tmp_path, payload))
    assert any("has no parameter definition" in warning for warning in spec.warnings)


def test_static_route_wins_over_templated_route(petstore: pathlib.Path) -> None:
    spec = load_spec(petstore)
    hit = spec.match("get", "/health")
    assert hit is not None
    assert hit[0].operation_id == "health"


def test_yaml_missing_dependency_message(tmp_path: pathlib.Path, monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("no yaml")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(SpecError, match="PyYAML is not installed"):
        parse_document("openapi: 3.0.3\n", "spec.yaml")

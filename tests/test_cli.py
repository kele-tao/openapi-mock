"""CLI contract: exit codes, flags, JSON output and friendly errors."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from openapi_mock.cli import main


def run(argv: list[str], capsys) -> tuple[int, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_summary_exit_zero_for_clean_spec(petstore: pathlib.Path, capsys) -> None:
    code, output = run(["summary", str(petstore)], capsys)
    assert code == 0
    assert "OPERATION ID" in output
    assert "listPets" in output


def test_summary_fail_on_deprecated_flag(petstore: pathlib.Path, capsys) -> None:
    code, output = run(["summary", str(petstore), "--fail-on-deprecated"], capsys)
    assert code == 1
    assert "deprecated" in output.lower()


def test_summary_json_is_machine_readable(petstore: pathlib.Path, capsys) -> None:
    code, output = run(["summary", str(petstore), "--json"], capsys)
    assert code == 0
    payload = json.loads(output)
    assert payload["title"] == "Petstore Orders API"
    assert len(payload["operations"]) == 17
    assert {"method", "path", "complexity", "deprecated"} <= set(payload["operations"][0])


def test_summary_missing_spec_exits_two(capsys) -> None:
    code, output = run(["summary", "does-not-exist.json"], capsys)
    assert code == 2
    assert "spec file not found" in output
    assert "hint:" in output


def test_summary_reports_deprecated_endpoint(petstore: pathlib.Path, capsys) -> None:
    _, output = run(["summary", str(petstore)], capsys)
    assert "deprecated" in output
    assert "deletePet" in output


def test_validate_command_counts_violations(
    petstore: pathlib.Path, root: pathlib.Path, capsys
) -> None:
    code, output = run(
        [
            "validate",
            "--requests",
            str(root / "examples" / "requests.log"),
            "--spec",
            str(petstore),
        ],
        capsys,
    )
    assert code == 1
    assert "6 requests, 5 contract violations" in output


def test_validate_json_output(petstore: pathlib.Path, root: pathlib.Path, capsys) -> None:
    code, output = run(
        [
            "validate",
            "--requests",
            str(root / "examples" / "requests.log"),
            "--spec",
            str(petstore),
            "--json",
        ],
        capsys,
    )
    assert code == 1
    payload = json.loads(output)
    assert payload["requests"] == 6
    assert payload["violations"] == 5


def test_validate_requires_spec(root: pathlib.Path, capsys) -> None:
    code, output = run(["validate", "--requests", str(root / "examples" / "requests.log")], capsys)
    assert code == 2
    assert "needs --spec" in output


def test_validate_clean_log_exits_zero(
    tmp_path: pathlib.Path, petstore: pathlib.Path, capsys
) -> None:
    log = tmp_path / "clean.log"
    log.write_text(json.dumps({"method": "GET", "path": "/health"}) + "\n", encoding="utf-8")
    code, output = run(["validate", "--requests", str(log), "--spec", str(petstore)], capsys)
    assert code == 0
    assert "0 contract violations" in output


def test_sample_command_prints_json(petstore: pathlib.Path, capsys) -> None:
    code, output = run(["sample", str(petstore), "--operation", "getPet", "--seed", "42"], capsys)
    assert code == 0
    payload = json.loads(output)
    assert payload["name"]
    assert payload["status"] in ("available", "pending", "sold")


def test_sample_count_flag(petstore: pathlib.Path, capsys) -> None:
    code, output = run(["sample", str(petstore), "--operation", "health", "--count", "3"], capsys)
    assert code == 0
    decoder = json.JSONDecoder()
    index = 0
    found = 0
    while index < len(output):
        while index < len(output) and output[index].isspace():
            index += 1
        if index >= len(output):
            break
        _, index = decoder.raw_decode(output, index)
        found += 1
    assert found == 3


def test_sample_unknown_operation_is_usage_error(petstore: pathlib.Path, capsys) -> None:
    code, output = run(["sample", str(petstore), "--operation", "nope"], capsys)
    assert code == 2
    assert "unknown operationId" in output


def test_no_command_prints_help_and_exits_two(capsys) -> None:
    code, _ = run([], capsys)
    assert code == 2


def test_serve_rejects_bad_error_rate(petstore: pathlib.Path, capsys) -> None:
    code, output = run(["serve", str(petstore), "--error-rate", "5"], capsys)
    assert code == 2
    assert "--error-rate must be between 0 and 1" in output


def test_serve_rejects_negative_latency(petstore: pathlib.Path, capsys) -> None:
    code, output = run(["serve", str(petstore), "--latency", "-5"], capsys)
    assert code == 2
    assert "--latency must be >= 0" in output


def test_serve_rejects_bad_port(petstore: pathlib.Path, capsys) -> None:
    code, output = run(["serve", str(petstore), "--port", "99999"], capsys)
    assert code == 2
    assert "between 0 and 65535" in output


def test_version_flag(capsys) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    captured = capsys.readouterr()
    assert "openapi-mock 1.0.0" in captured.out


def test_module_entry_point_help() -> None:
    """python -m openapi_mock --help works from a clean interpreter."""
    from conftest import ROOT

    env = {"PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}
    completed = subprocess.run(
        [sys.executable, "-m", "openapi_mock", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**__import__("os").environ, **env},
        timeout=60,
    )
    assert completed.returncode == 0
    assert "serve" in completed.stdout


def test_color_never_disables_ansi(petstore: pathlib.Path, capsys) -> None:
    code, output = run(["--color", "never", "summary", str(petstore)], capsys)
    assert code == 0
    assert "\x1b[" not in output

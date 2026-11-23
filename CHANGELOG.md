# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-11-23

First public release.

### Added

- `openapi-mock serve`: threaded stdlib HTTP mock server driven by an OpenAPI
  3.0/3.1 document, with `--port` (including `0` for an ephemeral port and
  `--print-url`), `--seed`, `--latency`, `--error-rate`,
  `--cors`, `--stateful`, `--record` and `--quiet`.
- `openapi-mock summary`: endpoint table with method, path, operation id, auth
  scheme, response shape and a complexity score; `--json` and
  `--fail-on-deprecated` included.
- `openapi-mock validate`: replay a JSONL request log against a spec and report
  every contract violation with a path; exits 1 when anything is wrong.
- `openapi-mock sample`: print one or more generated payloads for an operation.
- Spec loader for JSON (and YAML via the `yaml` extra) with local and
  relative-file `$ref` resolution, memoised path resolution and cycle detection.
- `allOf` flattening, `oneOf`/`anyOf` branch selection, and a complexity
  score per schema.
- Schema-driven fake data with formats, enums, constants, patterns, bounds,
  arrays, `additionalProperties` and name-aware realistic pools.
- Request validation for path, query, header and JSON body parameters, answering
  RFC 7807 `application/problem+json` 422 responses.
- Benchmark harness (`benchmarks/run.py`) that produces the table in the README.
- 139 pytest tests, ruff-clean source, GitHub Actions CI on Python 3.10-3.12.

### Fixed

- `serve --record` now appends each request line *before* replying. Recording after
  the response let two keep-alive requests be logged out of order (reproducible on
  Python 3.10), which made a recorded log disagree with the order the client made
  its calls. A regression test asserts the log order now.

### Known limitations

- Auth schemes are reported by `summary` but not enforced by the server.
- `multipart/form-data` bodies are listed but not validated.
- `not`, `if`/`then`/`else` and `discriminator` are reported as
  notes and ignored by validation.
- `--stateful` storage is in-memory only.

See `docs/ROADMAP.md` for the full list and the next steps.
# CLI reference

```
openapi-mock [-h] [--version] [--color {auto,always,never}] COMMAND ...
```

Global flags:

| Flag | Meaning |
| --- | --- |
| `--version` | print `openapi-mock 1.0.0` and exit 0 |
| `--color auto\|always\|never` | colourise output; `auto` honours `NO_COLOR` and TTY detection |
| `-h, --help` | usage with examples |

## serve

```
openapi-mock serve SPEC [--port 8787] [--host 127.0.0.1] [--seed 0]
                        [--latency 0] [--error-rate 0.0] [--cors]
                        [--stateful] [--record FILE] [--quiet] [--print-url]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `SPEC` | required | OpenAPI 3.0/3.1 document, JSON or YAML |
| `--port` | `8787` | `0` asks the OS for a free port |
| `--host` | `127.0.0.1` | bind address; keep it on loopback |
| `--seed` | `0` | deterministic data seed, echoed as `X-Mock-Seed` |
| `--latency` | `0` | milliseconds to wait before answering |
| `--error-rate` | `0.0` | probability in `[0,1]` of answering 500 |
| `--cors` | off | send permissive CORS headers and answer `OPTIONS` |
| `--stateful` | off | POST objects are stored; later GETs return them |
| `--record` | off | append one JSON object per request to `FILE` |
| `--quiet` | off | suppress the access log |
| `--print-url` | off | print only the bound URL, then serve |

Exit codes: 0 on Ctrl-C or clean shutdown, 2 on a usage error such as
`--error-rate 5`, a negative `--latency` or a port that is already bound.

## summary

```
openapi-mock summary SPEC [--json] [--fail-on-deprecated]
```

Prints one row per operation:

```
  METHOD  PATH                               OPERATION ID           AUTH        RESPONSE             COMPLEXITY
  ------  ---------------------------------  ---------------------  ----------  -------------------  ----------
  GET     /pets                              listPets               -           200 object:3         177
```

- `AUTH` resolves operation security through the document-level default and
  lists every scheme required by any alternative requirement.
- `RESPONSE` is the status the mock serves plus a short schema label
  (`object:11`, `array<object:3>`, `oneOf:2`, `any`).
- `COMPLEXITY` is the sum of the request and response schema scores.
- `--fail-on-deprecated` exits 1 when the spec has deprecated operations.
- `--json` emits the same data with `warnings` included.

Exit codes: 0 normally, 1 with `--fail-on-deprecated` and deprecated operations
present, 2 for an unreadable or unsupported spec.

## validate

```
openapi-mock validate --requests FILE --spec SPEC [--json]
```

Replays every line of a JSONL request log against the spec. Each record is
`{"method": "GET", "path": "/pets", "query": {...}, "headers": {...}, "body": ...}`;
the `status` the server recorded is kept for reference but not trusted.

Exit codes: 0 all requests valid, 1 at least one violation, 2 malformed log or
missing `--spec`.

## sample

```
openapi-mock sample SPEC [--operation ID] [--seed 0] [--count 1]
```

Prints generated payloads as pretty JSON. Without `--operation` the first
operation of the spec is used (sorted the same way `summary` lists them).

Exit code 0, or 2 for an unknown operation id.

# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.x | yes |

## What this tool is

openapi-mock is a **development tool**. It binds to `127.0.0.1` by default, serves
fabricated data from an OpenAPI document you supply, and stores nothing on disk
except an optional `--record` log you explicitly ask for.

It is not hardened for hostile networks:

- `--host 0.0.0.0` exposes the mock to your network. Do not do that on an
  untrusted network.
- There is no authentication enforcement: the server never checks credentials.
  Any auth scheme you declare is reported by `summary` and otherwise ignored.
- Generated data is fake. Do not treat it as a source of secrets, and do not point
  production traffic at the mock.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private
vulnerability reporting ("Security" tab -> "Report a vulnerability") on this
repository, or email the maintainer listed in `pyproject.toml`.

Include the version, the command line, the spec that reproduces it, and what you
expected instead. We aim to acknowledge within three working days.

## Hardening checklist if you deploy this anywhere shared

- Keep the default `--host 127.0.0.1`.
- Pass `--quiet` and put the process behind a supervisor.
- Never commit `--record` output that contains real tokens: the log stores
  `Authorization` headers verbatim.
- Treat an OpenAPI document as code: it is parsed, not executed, but it decides
  what the mock answers.

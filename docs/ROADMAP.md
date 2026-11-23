# Roadmap

What shipped in v1.0, what was deliberately left out, and why.

## Shipped in v1.0

- OpenAPI 3.0 / 3.1 loading from JSON, plus YAML when PyYAML is installed.
- Local and relative-file `$ref` resolution with cycle detection and remote-ref
  refusal.
- `allOf` merging (properties, required, bounds, enum), `oneOf`/`anyOf`
  branch selection, recursive schemas terminated by a cycle marker.
- Seeded, schema-valid fake data: formats (date, date-time, uuid, email, uri,
  ipv4, byte, ...), enum, const, pattern, numeric and length bounds,
  `multipleOf`, arrays with `minItems`, `additionalProperties`, realistic
  pools for people, cities, companies, SKUs and products.
- Threaded mock server with path/query/header parameter validation, RFC 7807
  `problem+json` 422s, `--latency`, `--error-rate`, `--seed`,
  `--cors`, `--stateful`, `--record`, an access log and `--port 0`
  ephemeral binding.
- `validate`: replay a JSONL request log against the spec, report every contract
  violation with a path, exit 1 when anything is wrong.
- `summary`: endpoint table with operation ids, auth, response shape,
  complexity score, deprecation flags, plus JSON output.
- `sample`: print one generated payload for an operation (handy for fixtures).

## Deliberately cut from v1.0

These are honest omissions, not forgotten work.

1. **Authentication is reported, not enforced.** `summary` shows which scheme guards
   an endpoint and warns about `oauth2`/`openIdConnect`, but the server does not
   reject requests with a missing or wrong bearer token. Enforcing it correctly
   needs per-scheme logic (bearer, apiKey, basic, oauth scopes) and a clear way to
   turn it off for tests; that is a week of edge cases for a feature most mocks are
   expected *not* to have.
2. **The mock does not call out to `examples` in schemas it cannot parse.** When a
   pattern uses constructs outside the supported subset (alternation, groups,
   backreferences), the generator produces a plausible value instead of pretending
   it matched the regex. The README documents this.
3. **`multipart/form-data` bodies are not validated.** The spec is loaded and the
   endpoint is listed, but the mock treats the body as absent. Binary upload
   mocking needs a multipart parser and a temp-file story.
4. **No response headers from the spec.** Declared `headers` on a response are
   ignored except for `Content-Type`; the mock always sends its own.
5. **`not`/`if`/`then`/`else`/`discriminator` are reported as notes, not enforced.** They
   appear in `summary` output as warnings so nobody assumes otherwise.
6. **No WebSocket or SSE mocks.** Out of scope for an HTTP mock server.
7. **No persistent store.** `--stateful` is in-memory; restarting the process
   clears created objects. A `--store file.json` flag is the obvious next step.

## Next steps (in the order we would do them)

1. `--store path.json` for `--stateful`, so a fixture can survive a restart.
2. Enforce `apiKey` and `http bearer` schemes behind a `--auth enforce`
   flag (default stays permissive).
3. `--watch spec.json`: reload the document when it changes, keeping the port.
4. Response-selection flags: `--status 404` and per-endpoint overrides from a small
   YAML file, so a frontend engineer can test error states on demand.
5. Record a fixture *file* (a directory of per-operation JSON payloads) rather than
   a request log, for teams that want to commit mocked responses.
6. Raise the validator to a larger JSON Schema subset (`not`, `if`/`then`,
   `discriminator`) once a real spec forces it.

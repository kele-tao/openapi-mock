# Architecture

openapi-mock is five layers with one-way dependencies. Each layer is a module you
can read in a sitting, and the arrow never points backwards.

```
            +-----------------------------+
            |  cli.py   (argparse)        |   exit codes: 0 ok, 1 findings, 2 usage
            +--------------+--------------+
                           |
      +--------------------+--------------------+
      |                    |                    |
+-----v------+      +------v------+      +------v------+
| requests.py|      |  server.py  |      |  report.py  |
| record +   |      | http.server |      |  tables +   |
| replay     |      | threaded    |      | problem+json|
+-----+------+      +------+------+      +------+------+
      |                    |                    |
      +--------+-----------+--------------------+
               |
        +------v------+        +----------------+
        | validator.py|        |  generate.py   |
        | JSON-Schema |        |  schema -> data|
        | subset      |        |  seeded        |
        +------+------+        +--------+-------+
               |                      |
        +------v------+        +------v------+
        |  schema.py  |        |   data.py   |
        | allOf/oneOf |        |  pools +    |
        | complexity  |        |  formats    |
        +------+------+        +-------------+
               |
        +------v------+
        |  loader.py  |
        | JSON/YAML   |
        | $ref, paths |
        +-------------+
```

## 1. Loader (`loader.py`)

Reads the file, parses JSON (or YAML when PyYAML is installed), and rejects the
document envelope early: Swagger 2.0, a missing `openapi` field, a non-object root
and a file that is really an HTML error page all produce a one-line `SpecError`
with a hint instead of a traceback.

`$ref` resolution is a single recursive walk. Local pointers (`#/components/...`)
and relative files (`./common.json#/Address`) share one cache of loaded documents;
the walk carries a stack of `(base_dir, ref)` pairs, so a recursive schema such as
`Node.child -> Node` terminates with a cycle marker instead of recursing forever.
Remote refs are refused explicitly, because the tool is offline by design. Path
resolution is memoised: on Windows one `realpath` call costs about 0.8 ms, and a
30-ref spec would otherwise pay it 30 times. Adding that cache took spec load from
111 ms to 8 ms on the bundled example (see `benchmarks/results.json`).

After resolution, every path item becomes an `Operation`: method, path, merged
parameters (an operation-level parameter overrides a path-level one with the same
name and location, exactly as the spec says), request body, responses, security,
and a compiled regex for `{param}` matching.

## 2. Schema algebra (`schema.py`)

`allOf` is flattened by overlaying branches in order: object properties merge
recursively, `required` lists union, numeric bounds intersect, `enum` lists
intersect. `oneOf`/`anyOf` are not merged but selected: the generator picks the
branch with the highest complexity score, because answering a union with the scalar
arm forever hides half of a client's code paths.

Complexity is a small integer: 1 per schema node, +2 per object property, +3 per
union branch, +2 for a pattern, +1 per bound keyword, +1 per two levels of nesting.
`summary` prints it per endpoint so you can see which endpoint carries the weight.

## 3. Generation (`generate.py` and `data.py`)

A seeded walk over the schema. Every value comes from a `random.Random` instance
seeded with `crc32(seed, depth, property name, call index)`, so one seed reproduces
byte-identical payloads while different properties still vary.

Realism is name-driven: a property called `city` gets a city, `sku` gets
`SKU-B4821`, a property called `name` inside a `Pet` schema gets a pet name while
the same property inside `Customer` gets a person name. The context hint never
changes structure, only which pool is consulted. Every candidate is checked against
the declared `pattern`/`minLength`/`maxLength` before it is kept, so realism
never wins over validity.

Patterns are synthesised for the common idioms, verified with `re.fullmatch`, and
fall back to schema-formatted values when the pattern uses constructs outside the
supported subset.

## 4. Validation (`validator.py`)

A JSON-Schema subset validator that returns *all* issues with pointers, e.g.
`items[1].id: expected integer, got string`. It enforces type, `required`,
`additionalProperties`, `enum`, `const`, string bounds, `pattern`,
`format`, numeric bounds, `exclusiveMinimum`/`exclusiveMaximum`,
`multipleOf`, array bounds, `uniqueItems`, `minProperties`/
`maxProperties` and `nullable`.

`requests.py` adds the OpenAPI layer: path values are coerced and validated,
query parameters are deserialised per `style=form, explode=true`, unknown query
parameters and unknown `x-` headers are flagged, and request bodies are matched
to their JSON media type. The same code path serves live requests (answering
`422 application/problem+json`) and log replay (`openapi-mock validate`).

## 5. Server (`server.py`)

`ThreadingHTTPServer` with one handler class bound to a `ServerConfig`. Routing
is a linear scan of pre-sorted operations (static routes first, then longer paths),
which is more than fast enough for a mock and keeps the endpoint table and the
router in the same order.

Latency is applied *before* the response is written, because a delay after the body
is already on the wire is invisible to the client. Error injection uses its own RNG
so `--error-rate` does not change generated payloads for a given seed. With
`--stateful`, POST bodies are kept in a lock-protected in-memory store, filled
out with the generated skeleton so `readOnly` fields exist, and the path
identifier is echoed back on `GET /collection/{id}` so a client sees the object it
asked for.
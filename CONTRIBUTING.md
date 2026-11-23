# Contributing

Thanks for taking the time to contribute. This repository is small on purpose and
kept that way; a pull request that adds one honest feature with tests is worth more
than a large one that half-works.

## Development setup

```bash
git clone https://github.com/kele-tao/openapi-mock.git
cd openapi-mock
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
ruff check .
```

```bash
python -m openapi_mock summary examples/petstore.json
python benchmarks/run.py
```

## Ground rules

1. **Nothing fake.** Every number in the README is produced by a command in this
   repository. If you change behaviour that a number describes, re-run the command
   and update the number in the same pull request.
2. **Tests are behaviour, not internals.** Assert on what a user sees: CLI output,
   exit codes, HTTP status, response body. Do not assert on private helpers.
3. **No network in tests.** The suite must pass on a laptop in aeroplane mode. Use
   the `server` fixture, which binds port 0.
4. **Determinism.** Any new output must be byte-identical for the same input and
   seed. If you add randomness, it must flow through `openapi_mock.data.Factory`.
5. **Errors are part of the interface.** A user mistake must produce a one-line
   message with a hint and the documented exit code (2 for usage), never a
   traceback.
6. **Style.** `ruff check .` must pass (line length 100). Type hints on public
   functions. Standard library first — a new runtime dependency needs a paragraph in
   the pull request explaining why it earns its place.

## Adding a feature

- Add the behaviour, the CLI flag (if any) and a test that would fail without it.
- Update `README.md` if the user-facing surface changed, and
  `CHANGELOG.md` under `[Unreleased]`.
- If you deliberately do not support something, say so in `docs/ROADMAP.md`
  instead of leaving a half-implementation behind.

## Commit messages

Imperative mood, one line under 72 characters, body explaining the *why*:

```
Reject unknown query parameters in replay

The mock already answered 422 for them, so validate disagreeing with
serve was a bug in the test double, not a feature.
```

## Reporting bugs

Open an issue with the bug template. Include the spec (or a minimal one that
reproduces it), the exact command, the output, and `openapi-mock --version`.

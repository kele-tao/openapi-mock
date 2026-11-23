"""Command line surface: serve, summary, validate, replay.

Exit codes are part of the contract: 0 success, 1 findings (deprecated endpoints
in summary, contract violations in validate), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import threading
from collections.abc import Sequence

from . import __version__, colors
from .errors import MockError, UsageError
from .generate import Generator
from .loader import Spec, load_spec
from .report import summary_report, validate_report
from .requests import Violation, check_record, read_log, response_schema
from .server import ServerConfig, bind, run_in_thread, shutdown

EPILOG = """examples:
  openapi-mock summary petstore.json
  openapi-mock serve petstore.json --port 8787 --latency 50 --seed 42
  openapi-mock validate --requests requests.log --spec petstore.json
  openapi-mock sample petstore.json --operation listPets
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openapi-mock",
        description="Serve a contract-valid, deterministic mock API from an OpenAPI 3 document.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="openapi-mock " + __version__)
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colourise output (default: auto, honours NO_COLOR)",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    serve = sub.add_parser("serve", help="run the mock HTTP server")
    serve.add_argument("spec", help="OpenAPI 3.x document (.json, .yaml)")
    serve.add_argument("--port", type=int, default=8787, help="port, 0 picks a free one")
    serve.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    serve.add_argument("--seed", type=int, default=0, help="deterministic data seed")
    serve.add_argument("--latency", type=int, default=0, metavar="MS", help="added latency")
    serve.add_argument(
        "--error-rate",
        type=float,
        default=0.0,
        metavar="P",
        help="fraction of requests answered 500 (0-1)",
    )
    serve.add_argument("--cors", action="store_true", help="send permissive CORS headers")
    serve.add_argument(
        "--stateful",
        action="store_true",
        help="POST stores objects that later GETs return",
    )
    serve.add_argument("--record", metavar="FILE", help="append every request to FILE (JSONL)")
    serve.add_argument("--quiet", action="store_true", help="do not print the access log")
    serve.add_argument(
        "--print-url",
        action="store_true",
        help="print only the bound URL, then keep serving (script-friendly)",
    )

    summary = sub.add_parser("summary", help="print the endpoint table for a spec")
    summary.add_argument("spec")
    summary.add_argument("--json", action="store_true", help="machine-readable output")
    summary.add_argument(
        "--fail-on-deprecated",
        action="store_true",
        help="exit 1 when the spec contains deprecated operations",
    )

    validate = sub.add_parser("validate", help="replay a recorded request log against a spec")
    validate.add_argument("--requests", required=True, metavar="FILE", help="JSONL request log")
    validate.add_argument("--spec", metavar="FILE", help="spec to check against")
    validate.add_argument("--json", action="store_true", help="machine-readable output")

    sample = sub.add_parser("sample", help="print one generated payload for an operation")
    sample.add_argument("spec")
    sample.add_argument("--operation", help="operationId to sample (default: first)")
    sample.add_argument("--seed", type=int, default=0)
    sample.add_argument("--count", type=int, default=1, help="how many payloads to print")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    colors.configure(mode=getattr(args, "color", "auto"))
    if args.command is None:
        parser.print_help()
        return 2
    try:
        if args.command == "serve":
            return _serve(args)
        if args.command == "summary":
            return _summary(args)
        if args.command == "validate":
            return _validate(args)
        if args.command == "sample":
            return _sample(args)
    except MockError as exc:
        print(exc.render(), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(colors.dim("\nstopped"), file=sys.stderr)
        return 0
    return 2


def _load(path: str) -> Spec:
    spec = load_spec(path)
    if not spec.operations:
        raise UsageError(
            "the spec declares no operations to mock: " + path,
            hint="check the 'paths' object",
        )
    return spec


def _summary(args: argparse.Namespace) -> int:
    spec = _load(args.spec)
    text, deprecated = summary_report(spec, as_json=args.json)
    print(text)
    if args.fail_on_deprecated and deprecated:
        print(
            colors.red("  " + str(deprecated) + " deprecated operation(s) found"),
            file=sys.stderr,
        )
        return 1
    return 0


def _validate(args: argparse.Namespace) -> int:
    records = read_log(args.requests)
    spec_path = args.spec
    if not spec_path:
        raise UsageError(
            "validate needs --spec (the request log does not embed the spec path)",
            hint="openapi-mock validate --requests requests.log --spec petstore.json",
        )
    spec = _load(spec_path)
    results: list[tuple[int, str, str, list[Violation]]] = []
    for line, record in records:
        violations = check_record(spec, record)
        results.append((line, record.method, record.path, violations))
    text, code = validate_report(results, as_json=args.json)
    print(text)
    return code


def _sample(args: argparse.Namespace) -> int:
    spec = _load(args.spec)
    chosen = None
    if args.operation:
        for operation in spec.operations:
            if operation.operation_id == args.operation:
                chosen = operation
                break
        if chosen is None:
            raise UsageError(
                "unknown operationId: " + args.operation,
                hint="run 'openapi-mock summary " + args.spec + "' to list them",
            )
    else:
        chosen = spec.operations[0]
    picked = chosen.success_response()
    status_text = picked[0] if picked is not None else "200"
    schema = response_schema(chosen, status_text) or {}
    for index in range(max(1, args.count)):
        generator = Generator(seed=args.seed + index)
        payload = generator.sample(schema, chosen.operation_id) if schema else {}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _serve(args: argparse.Namespace) -> int:
    spec = _load(args.spec)
    if not 0.0 <= args.error_rate <= 1.0:
        raise UsageError("--error-rate must be between 0 and 1, got " + str(args.error_rate))
    if args.latency < 0:
        raise UsageError("--latency must be >= 0, got " + str(args.latency))
    if not 0 <= args.port <= 65535:
        raise UsageError("--port must be between 0 and 65535, got " + str(args.port))
    config = ServerConfig(
        spec=spec,
        seed=args.seed,
        latency_ms=args.latency,
        error_rate=args.error_rate,
        stateful=args.stateful,
        cors=args.cors,
        record_path=pathlib.Path(args.record) if args.record else None,
        verbose=not args.quiet and not args.print_url,
    )
    try:
        server = bind(args.host, args.port, config)
    except OSError as exc:
        raise UsageError(
            "cannot bind " + args.host + ":" + str(args.port) + ": " + (exc.strerror or str(exc)),
            hint="pick another port, or use --port 0 to let the OS choose",
        ) from exc
    url = server.base_url
    if args.print_url:
        print(url, flush=True)
        sys.stdout.flush()
    else:
        _banner(spec, config, url)
    stop = threading.Event()
    thread = run_in_thread(server)
    try:
        while not stop.wait(0.2):
            if not thread.is_alive():
                break
    except KeyboardInterrupt:
        pass
    finally:
        shutdown(server)
        if not args.print_url:
            print(colors.dim("\n  server stopped"))
    return 0


def _banner(spec: Spec, config: ServerConfig, url: str) -> None:
    print(colors.bold("openapi-mock") + colors.dim("  " + spec.title + " v" + spec.version))
    print(colors.dim("  " + str(len(spec.operations)) + " operations from " + spec.path))
    extras = []
    if config.seed:
        extras.append("seed " + str(config.seed))
    if config.latency_ms:
        extras.append("latency " + str(config.latency_ms) + "ms")
    if config.error_rate:
        extras.append("error-rate " + str(config.error_rate))
    if config.stateful:
        extras.append("stateful")
    if config.cors:
        extras.append("cors")
    if extras:
        print(colors.dim("  " + "  ".join(extras)))
    print("  " + colors.cyan(url))
    for key in list(spec.operations[:6]):
        print(
            "    "
            + colors.method(key.method)
            + " "
            + url
            + key.path
            + colors.dim("  -> " + key.operation_id)
        )
    if len(spec.operations) > 6:
        print(colors.dim("    ... " + str(len(spec.operations) - 6) + " more"))
    print(colors.dim("  ctrl-c to stop"))
    print()
    sys.stdout.flush()


__all__ = ["main", "build_parser"]

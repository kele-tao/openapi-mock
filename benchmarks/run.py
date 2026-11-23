#!/usr/bin/env python
"""Benchmark the mock: spec load, cold/hot latency and replay throughput.

Writes benchmarks/results.json and prints a Markdown table that the README copies
verbatim. Everything is measured locally with stdlib time.perf_counter - no
external tooling, no network, reproducible on any laptop.
"""

from __future__ import annotations

import json
import pathlib
import platform
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openapi_mock.loader import load_spec  # noqa: E402
from openapi_mock.requests import check_record, read_log  # noqa: E402
from openapi_mock.server import ServerConfig, bind, http_get, run_in_thread, shutdown  # noqa: E402

SPEC = ROOT / "examples" / "petstore.json"
REQUESTS = ROOT / "examples" / "requests.log"
ENDPOINTS = ["/health", "/pets?limit=5", "/orders?limit=3", "/pets/{id}", "/customers/{id}"]
ITERATIONS = 60


def bench_load(runs: int = 20) -> float:
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        load_spec(SPEC)
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


def bench_requests(base: str, paths: list[str], iterations: int = ITERATIONS) -> dict[str, float]:
    for path in paths:  # warm the connection pool and the code paths
        http_get(base + path)
    samples: list[float] = []
    for _ in range(iterations):
        for path in paths:
            started = time.perf_counter()
            status, _body, _headers = http_get(base + path)
            elapsed = (time.perf_counter() - started) * 1000.0
            if status >= 400:
                raise RuntimeError("benchmark request failed with " + str(status))
            samples.append(elapsed)
    samples.sort()
    return {
        "requests": len(samples),
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(samples[int(len(samples) * 0.95)], 2),
        "mean_ms": round(statistics.fmean(samples), 2),
        "max_ms": round(samples[-1], 2),
    }


def bench_replay(spec, runs: int = 30) -> dict[str, float]:
    records = read_log(REQUESTS)
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        for _line, record in records:
            check_record(spec, record)
        samples.append((time.perf_counter() - started) * 1000.0)
    samples.sort()
    per_request = statistics.median(samples) / len(records)
    return {
        "log_lines": len(records),
        "replays": runs,
        "median_ms": round(statistics.median(samples), 3),
        "per_request_us": round(per_request * 1000.0, 1),
    }


def bench_generation(spec) -> dict[str, float]:
    from openapi_mock.generate import Generator
    from openapi_mock.requests import response_schema

    chosen = [op for op in spec.operations if op.success_response()][:8]
    runs = 200
    started = time.perf_counter()
    for index in range(runs):
        generator = Generator(seed=index)
        for operation in chosen:
            picked = operation.success_response()
            if picked is None:
                continue
            schema = response_schema(operation, picked[0])
            if schema:
                generator.sample(schema, operation.operation_id)
    elapsed = (time.perf_counter() - started) * 1000.0
    payloads = runs * len(chosen)
    return {
        "payloads": payloads,
        "total_ms": round(elapsed, 2),
        "per_payload_ms": round(elapsed / payloads, 3),
    }


def main() -> int:
    spec = load_spec(SPEC)
    config = ServerConfig(spec=spec, seed=42, verbose=False)
    instance = bind("127.0.0.1", 0, config)
    run_in_thread(instance)
    try:
        concurrency = bench_requests(instance.base_url, ENDPOINTS[:3])
        single = bench_requests(instance.base_url, ["/health"], iterations=200)
        templated = bench_requests(
            instance.base_url, ["/pets/9f3c1c4e-0d1b-4c9e-9d8a-7f6e5d4c3b2a"], iterations=100
        )
    finally:
        shutdown(instance)
    results = {
        "python": platform.python_version(),
        "platform": platform.system(),
        "spec_load_ms": round(bench_load(), 2),
        "single_endpoint": single,
        "mixed_endpoints": concurrency,
        "replay": bench_replay(spec),
        "generation": bench_generation(spec),
        "operations": len(spec.operations),
    }
    results["templated_route"] = templated
    out = ROOT / "benchmarks" / "results.json"
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print("| Metric | Value |")
    print("| --- | --- |")
    print("| Spec load (17 operations, median of 20) | " + str(results["spec_load_ms"]) + " ms |")
    print("| Single endpoint, keep-alive (p50) | " + str(single["p50_ms"]) + " ms |")
    print("| Mixed endpoints, keep-alive (p95) | " + str(concurrency["p95_ms"]) + " ms |")
    print("| Requests in mixed run | " + str(concurrency["requests"]) + " |")
    print("| Replay check per request | " + str(results["replay"]["per_request_us"]) + " us |")
    generation = results["generation"]["per_payload_ms"]
    print("| Payload generation per response | " + str(generation) + " ms |")
    print("| Templated route (p50) | " + str(templated["p50_ms"]) + " ms |")
    print("| Python | " + results["python"] + " on " + results["platform"] + " |")
    print()
    print("wrote " + str(out.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

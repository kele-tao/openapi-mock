"""Shared fixtures: paths, an ephemeral server and a CLI runner."""

from __future__ import annotations

import contextlib
import pathlib
from collections.abc import Iterator

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


@pytest.fixture(scope="session")
def root() -> pathlib.Path:
    return ROOT


@pytest.fixture(scope="session")
def petstore() -> pathlib.Path:
    path = EXAMPLES / "petstore.json"
    assert path.exists(), "examples/petstore.json must ship with the repo"
    return path


@pytest.fixture()
def server(petstore: pathlib.Path) -> Iterator[object]:
    """A live mock server on an ephemeral port, torn down afterwards."""
    from openapi_mock.loader import load_spec
    from openapi_mock.server import ServerConfig, bind, run_in_thread, shutdown

    spec = load_spec(petstore)
    config = ServerConfig(spec=spec, seed=42, verbose=False)
    instance = bind("127.0.0.1", 0, config)
    run_in_thread(instance)
    try:
        yield instance
    finally:
        shutdown(instance)


@contextlib.contextmanager
def running(spec_path: pathlib.Path, **kwargs: object) -> Iterator[object]:
    """Inline server context manager for tests that need custom config."""
    from openapi_mock.loader import load_spec
    from openapi_mock.server import ServerConfig, bind, run_in_thread, shutdown

    spec = load_spec(spec_path)
    config = ServerConfig(spec=spec, verbose=False, **kwargs)
    instance = bind("127.0.0.1", 0, config)
    run_in_thread(instance)
    try:
        yield instance
    finally:
        shutdown(instance)

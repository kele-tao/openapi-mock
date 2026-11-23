"""Tiny ANSI helper so the CLI, the server log and the reports agree on colour.

Colour is decided once from argv/env by :func:`configure`; callers never check
supports_color themselves. Setting `NO_COLOR` disables it, `FORCE_COLOR` (or a
TTY) enables it.  `--no-color` in the CLI always wins.
"""

from __future__ import annotations

import os
import sys

_enabled = False


def configure(*, mode: str = "auto", stream: object | None = None) -> bool:
    """Set global colour state. mode is auto|always|never. Returns the state."""
    global _enabled
    if mode == "never":
        _enabled = False
    elif mode == "always":
        _enabled = True
    else:
        target = stream if stream is not None else sys.stdout
        target = getattr(target, "isatty", lambda: False)()
        _enabled = bool(target) and "NO_COLOR" not in os.environ
        if os.environ.get("FORCE_COLOR"):
            _enabled = True
    return _enabled


def enabled() -> bool:
    return _enabled


def _ansi(code: str, text: str) -> str:
    if not _enabled or not text:
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def bold(text: str) -> str:
    return _ansi("1", text)


def dim(text: str) -> str:
    return _ansi("2;37", text)


def red(text: str) -> str:
    return _ansi("31", text)


def green(text: str) -> str:
    return _ansi("32", text)


def yellow(text: str) -> str:
    return _ansi("33", text)


def blue(text: str) -> str:
    return _ansi("34", text)


def magenta(text: str) -> str:
    return _ansi("35", text)


def cyan(text: str) -> str:
    return _ansi("36", text)


def status(code: int) -> str:
    """Colour an HTTP status code the way the server log wants it."""
    text = str(code)
    if code >= 500:
        return red(text)
    if code >= 400:
        return yellow(text)
    if code >= 300:
        return cyan(text)
    return green(text)


def method(verb: str) -> str:
    colours = {
        "GET": green,
        "POST": blue,
        "PUT": yellow,
        "PATCH": magenta,
        "DELETE": red,
        "OPTIONS": dim,
        "HEAD": dim,
    }
    fn = colours.get(verb.upper(), bold)
    return fn(verb.upper().ljust(6))

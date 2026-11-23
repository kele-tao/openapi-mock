"""Error types that map onto the documented CLI exit codes.

Exit codes: 0 success, 1 findings/quality gate failed, 2 usage error.
"""

from __future__ import annotations

from . import colors


class MockError(Exception):
    """Base class for every error the CLI renders itself (no traceback)."""

    exit_code = 1
    label = "error"

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self) -> str:
        head = colors.red(colors.bold(f"{self.label}:")) + " " + self.message
        if self.hint:
            return head + "\n  " + colors.dim("hint: " + self.hint)
        return head


class SpecError(MockError):
    """The document is not a usable OpenAPI 3.x spec (a usage error: exit 2)."""

    label = "spec error"
    exit_code = 2


class ValidationError(MockError):
    """A generated/loaded value does not satisfy the schema."""

    label = "validation error"


class UsageError(MockError):
    """Bad command line usage."""

    label = "usage error"
    exit_code = 2

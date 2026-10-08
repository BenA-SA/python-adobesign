"""Output contract: JSON/table rendering, structured errors and exit codes.

Successful results go to stdout as JSON (models are dumped with Adobe's
camelCase field names, so keys match the REST API docs). Failures go to stderr
as ``{"error": {...}}`` with a fixed set of keys, and the process exits with
one of the :class:`ExitCode` values.
"""

from __future__ import annotations

import json
from enum import IntEnum
from typing import Any

import click
from pydantic import BaseModel

from adobesign import errors

FORMATS = ("json", "table")


class ExitCode(IntEnum):
    """Process exit codes; documented in ``adobesign --help`` and the README."""

    OK = 0
    API_ERROR = 1
    USAGE = 2
    AUTH = 3
    RATE_LIMITED = 4
    NOT_FOUND = 5


EXIT_CODE_HELP = (
    "Exit codes: 0 ok; 1 API/server/network error; 2 usage or validation "
    "error (including a missing --yes); 3 authentication/permission/"
    "credentials; 4 rate-limited (see retry_after); 5 not found."
)


class CliError(Exception):
    """A failure detected by the CLI itself (bad input, missing config...)."""

    def __init__(self, message: str, *, exit_code: ExitCode, error_type: str) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.error_type = error_type


def usage_error(message: str) -> CliError:
    return CliError(message, exit_code=ExitCode.USAGE, error_type="UsageError")


def credentials_error(message: str) -> CliError:
    return CliError(message, exit_code=ExitCode.AUTH, error_type="CredentialsError")


def to_jsonable(value: Any) -> Any:
    """Models → camelCase dicts; lists/dicts recursively; everything else as is."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    return value


def emit(value: Any, output_format: str) -> None:
    """Write a successful result to stdout."""
    data = to_jsonable(value)
    if output_format == "table":
        click.echo(render_table(data))
        return
    click.echo(json.dumps(data, indent=2, ensure_ascii=False))


def _cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def _rows_to_text(header: list[str], rows: list[list[str]]) -> str:
    widths = [
        max(len(row[index]) for row in [header, *rows]) for index in range(len(header))
    ]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        for row in [header, *rows]
    ]
    return "\n".join(line.rstrip() for line in lines)


def render_table(data: Any) -> str:
    """A plain-text table: one row per list item, or key/value rows."""
    if isinstance(data, dict):
        rows = [[str(key), _cell(value)] for key, value in data.items()]
        return _rows_to_text(["field", "value"], rows)
    if isinstance(data, list) and data and all(isinstance(i, dict) for i in data):
        columns = [key for key in data[0] if not isinstance(data[0][key], (dict, list))]
        rows = [[_cell(item.get(column)) for column in columns] for item in data]
        return _rows_to_text(columns, rows)
    if isinstance(data, list):
        return "\n".join(_cell(item) for item in data)
    return _cell(data)


def exit_code_for(exc: BaseException) -> ExitCode:
    """Map an exception onto the documented exit code."""
    if isinstance(exc, CliError):
        return exc.exit_code
    if isinstance(exc, errors.RateLimitedError):
        return ExitCode.RATE_LIMITED
    if isinstance(exc, errors.NotFoundError):
        return ExitCode.NOT_FOUND
    if isinstance(exc, (errors.BadRequestError, errors.WebhookPayloadError)):
        return ExitCode.USAGE
    auth_failures = (
        errors.AuthenticationError,
        errors.PermissionDeniedError,
        errors.MissingAccessPointError,
        errors.MissingRefreshTokenError,
        errors.OAuthStateMismatchError,
        errors.WebhookClientIdError,
    )
    if isinstance(exc, auth_failures):
        return ExitCode.AUTH
    return ExitCode.API_ERROR


def error_payload(exc: BaseException) -> dict[str, Any]:
    """The stable ``{"error": {...}}`` document written to stderr."""
    error_type = exc.error_type if isinstance(exc, CliError) else type(exc).__name__
    return {
        "error": {
            "type": error_type,
            "message": str(exc),
            "exit_code": int(exit_code_for(exc)),
            "status_code": getattr(exc, "status_code", None),
            "code": getattr(exc, "code", None),
            "api_message": getattr(exc, "api_message", None),
            "request_id": getattr(exc, "request_id", None),
            "retry_after": getattr(exc, "retry_after", None),
        }
    }


def fail(exc: BaseException) -> None:
    """Write the error document to stderr and exit with its code."""
    click.echo(json.dumps(error_payload(exc), indent=2), err=True)
    raise SystemExit(int(exit_code_for(exc)))

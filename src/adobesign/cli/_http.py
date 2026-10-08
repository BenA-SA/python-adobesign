"""HTTP plumbing for the CLI: dry-run capture, verbose logging, redaction.

``--dry-run`` swaps the real network transport for :class:`DryRunTransport`,
which records each request and answers with a placeholder success, so the
library's own code builds exactly the requests it would send while nothing
leaves the machine. Every request shown to a human or agent goes through
:func:`describe_request`, which redacts credentials in headers and bodies.
"""

from __future__ import annotations

import json
from email.parser import BytesParser
from email.policy import HTTP
from typing import Any
from urllib.parse import parse_qsl

import click
import httpx

from adobesign.auth import TokenSet

REDACTED = "***REDACTED***"
SECRET_HEADERS = frozenset({"authorization", "cookie", "x-api-key"})
SECRET_FIELDS = frozenset(
    {
        "access_token",
        "refresh_token",
        "client_secret",
        "code",
        "integration_key",
        "accessToken",
        "refreshToken",
    }
)
DRY_RUN_ACCESS_POINT = "https://api-access-point.dry-run.invalid/"
DRY_RUN_ID = "DRY-RUN-ID"
DRY_RUN_BODY = {
    "id": DRY_RUN_ID,
    "transientDocumentId": "DRY-RUN-TRANSIENT-DOCUMENT-ID",
    "apiAccessPoint": DRY_RUN_ACCESS_POINT,
    "webAccessPoint": DRY_RUN_ACCESS_POINT,
    "access_token": "dry-run",
    "token_type": "Bearer",
}


def redact(value: Any) -> Any:
    """Replace values of credential-bearing keys, recursively."""
    if isinstance(value, dict):
        return {
            key: REDACTED if key in SECRET_FIELDS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _redacted_headers(headers: httpx.Headers) -> dict[str, str]:
    shown: dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() in SECRET_HEADERS:
            scheme = value.split(" ", 1)[0] if " " in value else ""
            shown[name] = f"{scheme} {REDACTED}".strip()
            continue
        shown[name] = value
    return shown


def _multipart_summary(request: httpx.Request) -> dict[str, str]:
    raw = (
        f"Content-Type: {request.headers['Content-Type']}\r\n\r\n".encode()
        + request.content
    )
    parts: dict[str, str] = {}
    for part in BytesParser(policy=HTTP).parsebytes(raw).iter_parts():
        name = str(part.get_param("name", header="content-disposition"))
        payload = part.get_payload(decode=True)
        size = len(payload) if isinstance(payload, bytes) else 0
        filename = part.get_filename()
        if filename:
            parts[name] = f"<{size} bytes from {filename}>"
            continue
        parts[name] = payload.decode() if isinstance(payload, bytes) else ""
    return parts


def describe_body(request: httpx.Request) -> Any:
    """The request body as JSON-friendly data, credentials redacted."""
    content_type = request.headers.get("Content-Type", "")
    if not request.content:
        return None
    if content_type.startswith("multipart/form-data"):
        return {"multipart": _multipart_summary(request)}
    if content_type.startswith("application/x-www-form-urlencoded"):
        return {"form": redact(dict(parse_qsl(request.content.decode())))}
    try:
        return redact(json.loads(request.content))
    except ValueError:
        return f"<{len(request.content)} bytes>"


def describe_request(request: httpx.Request) -> dict[str, Any]:
    """Method, URL, redacted headers and body of one request."""
    request.read()
    return {
        "method": request.method,
        "url": str(request.url),
        "headers": _redacted_headers(request.headers),
        "body": describe_body(request),
    }


class DryRunTransport(httpx.BaseTransport):
    """Records requests and answers each with a placeholder 200/204."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(describe_request(request))
        if request.method in {"PUT", "DELETE"}:
            return httpx.Response(204, request=request)
        return httpx.Response(200, json=DRY_RUN_BODY, request=request)


class DryRunCredentials:
    """Stands in for real credentials so a dry run never refreshes tokens."""

    def __init__(self, api_access_point: str | None) -> None:
        self._api_access_point = api_access_point

    @property
    def api_access_point(self) -> str | None:
        return self._api_access_point

    def authorization_header(self) -> str:
        return f"Bearer {REDACTED}"

    def handle_unauthorized(self) -> bool:
        return False


def _log_request(request: httpx.Request) -> None:
    click.echo(
        json.dumps({"verbose": "request", **describe_request(request)}), err=True
    )


def _log_response(response: httpx.Response) -> None:
    click.echo(
        json.dumps(
            {
                "verbose": "response",
                "status_code": response.status_code,
                "url": str(response.request.url),
            }
        ),
        err=True,
    )


def build_http_client(
    *, dry_run: DryRunTransport | None, verbose: bool, timeout: float = 60.0
) -> httpx.Client:
    """An httpx client for the CLI: dry-run capture and/or redacted logging."""
    hooks: dict[str, list[Any]] = {"request": [], "response": []}
    if verbose:
        hooks["request"].append(_log_request)
        hooks["response"].append(_log_response)
    return httpx.Client(transport=dry_run, timeout=timeout, event_hooks=hooks)


def token_summary(tokens: TokenSet) -> dict[str, Any]:
    """Token metadata that is safe to print (never the tokens themselves)."""
    return {
        "has_access_token": bool(tokens.access_token),
        "has_refresh_token": bool(tokens.refresh_token),
        "expires_at": tokens.expires_at.isoformat() if tokens.expires_at else None,
        "api_access_point": tokens.api_access_point,
        "web_access_point": tokens.web_access_point,
    }

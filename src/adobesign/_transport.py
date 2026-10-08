"""HTTP plumbing shared by the API client and the OAuth helper.

Owns three concerns so the resource classes stay declarative:

* mapping a non-2xx response onto the matching :mod:`adobesign.errors` class;
* retrying throttled (429) and failed (5xx / network) requests with
  exponential backoff that honours ``Retry-After``;
* parsing a JSON body into a model, converting both invalid JSON and schema
  mismatches into :class:`~adobesign.errors.ResponseParseError`.

5xx responses and network failures are only retried for idempotent methods
by default: replaying a ``POST /agreements`` whose first attempt actually
succeeded server-side would send the agreement twice. A 429 is always safe to
retry because the request was rejected before any work was done.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any
from typing import Protocol
from typing import TypeVar

import httpx
from pydantic import BaseModel
from pydantic import ValidationError

from adobesign import errors

ModelT = TypeVar("ModelT", bound=BaseModel)

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS"})
REQUEST_ID_HEADERS = ("x-request-id", "x-adobesign-request-id", "x-amzn-requestid")

_STATUS_ERRORS: dict[int, type[errors.ApiError]] = {
    400: errors.BadRequestError,
    401: errors.AuthenticationError,
    403: errors.PermissionDeniedError,
    404: errors.NotFoundError,
    409: errors.ConflictError,
}


@dataclass(frozen=True)
class RetryPolicy:
    """How throttled and failed requests are retried.

    ``max_retries`` counts retries after the first attempt. The delay before
    retry *n* (0-based) is ``backoff_factor * 2**n`` capped at
    ``max_backoff``, with full jitter when ``jitter`` is true. A 429's
    ``Retry-After`` replaces the computed delay; a ``Retry-After`` longer
    than ``max_retry_after`` is not waited out and the error is raised.
    """

    max_retries: int = 3
    backoff_factor: float = 0.5
    max_backoff: float = 30.0
    max_retry_after: float = 120.0
    retry_statuses: frozenset[int] = field(
        default_factory=lambda: frozenset({429, 500, 502, 503, 504})
    )
    retry_non_idempotent: bool = False
    jitter: bool = True

    def delay(self, attempt: int, retry_after: float | None) -> float:
        """Seconds to sleep before retry number ``attempt`` (0-based)."""
        if retry_after is not None:
            return retry_after
        backoff: float = min(self.max_backoff, self.backoff_factor * 2**attempt)
        if self.jitter:
            return random.uniform(0, backoff)
        return backoff


class AuthProvider(Protocol):
    """What the transport needs from a credential."""

    def authorization_header(self) -> str:
        """Return the ``Authorization`` header value for the next request."""
        ...

    def handle_unauthorized(self) -> bool:
        """React to a 401; return true when the request should be replayed."""
        ...


Sleep = Callable[[float], None]


class Transport:
    """Sends requests with retries and maps failures onto typed errors."""

    def __init__(
        self,
        http: httpx.Client,
        *,
        retry: RetryPolicy,
        sleep: Sleep = time.sleep,
    ) -> None:
        self.http = http
        self.retry = retry
        self._sleep = sleep

    def send(
        self,
        method: str,
        url: str,
        *,
        auth: AuthProvider | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Mapping[str, str] | None = None,
        files: Any = None,
        accept: str = "application/json",
    ) -> httpx.Response:
        """Send one logical request, retrying per the policy, and return 2xx.

        Raises the matching :class:`~adobesign.errors.ApiError` subclass for a
        final non-2xx response and :class:`~adobesign.errors.TransportError`
        when no response arrived.
        """
        attempt = 0
        replayed_after_refresh = False
        while True:
            headers = {"Accept": accept}
            if auth is not None:
                headers["Authorization"] = auth.authorization_header()
            try:
                response = self.http.request(
                    method,
                    url,
                    params=_clean_params(params),
                    json=json,
                    data=data,
                    files=files,
                    headers=headers,
                )
            except httpx.TransportError as exc:
                failure = errors.TransportError(
                    method=method, url=url, reason=type(exc).__name__
                )
                if not self._may_retry_transport_failure(method, attempt):
                    raise failure from exc
                self._sleep(self.retry.delay(attempt, None))
                attempt += 1
                continue

            if response.is_success:
                return response

            if self._should_replay_after_refresh(
                response, auth, replayed_after_refresh
            ):
                replayed_after_refresh = True
                continue

            error = error_from_response(response)
            if not self._may_retry_error(error, method, attempt):
                raise error
            retry_after = getattr(error, "retry_after", None)
            self._sleep(self.retry.delay(attempt, retry_after))
            attempt += 1

    def _should_replay_after_refresh(
        self,
        response: httpx.Response,
        auth: AuthProvider | None,
        already_replayed: bool,
    ) -> bool:
        if response.status_code != 401 or auth is None or already_replayed:
            return False
        return auth.handle_unauthorized()

    def _may_retry_transport_failure(self, method: str, attempt: int) -> bool:
        if attempt >= self.retry.max_retries:
            return False
        return method in IDEMPOTENT_METHODS or self.retry.retry_non_idempotent

    def _may_retry_error(
        self, error: errors.ApiError, method: str, attempt: int
    ) -> bool:
        if attempt >= self.retry.max_retries:
            return False
        if error.status_code not in self.retry.retry_statuses:
            return False
        if isinstance(error, errors.RateLimitedError):
            return (
                error.retry_after is None
                or error.retry_after <= self.retry.max_retry_after
            )
        return method in IDEMPOTENT_METHODS or self.retry.retry_non_idempotent


def _clean_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Drop ``None`` values and render booleans the way Acrobat Sign expects."""
    if params is None:
        return None
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        cleaned[key] = str(value).lower() if isinstance(value, bool) else value
    return cleaned


def _json_body(response: httpx.Response) -> dict[str, Any]:
    """The response body as a dict, or ``{}`` when it is not a JSON object."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _request_id(response: httpx.Response) -> str | None:
    for header in REQUEST_ID_HEADERS:
        value: str | None = response.headers.get(header)
        if value:
            return value
    return None


def parse_retry_after(
    response: httpx.Response, body: Mapping[str, Any]
) -> float | None:
    """Seconds to wait from ``Retry-After`` (seconds or HTTP-date) or ``retryAfter``."""
    header = response.headers.get("retry-after")
    if header:
        return _retry_after_header_seconds(header.strip())
    body_value = body.get("retryAfter")
    if isinstance(body_value, (int, float)) and not isinstance(body_value, bool):
        return float(body_value)
    return None


def _retry_after_header_seconds(header: str) -> float | None:
    try:
        return max(0.0, float(header))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _is_oauth_error_body(body: Mapping[str, Any]) -> bool:
    """OAuth endpoints answer ``{"error": ..., "error_description": ...}``."""
    return isinstance(body.get("error"), str) and "code" not in body


def error_from_response(response: httpx.Response) -> errors.ApiError:
    """Build the typed error for a non-2xx response."""
    body = _json_body(response)
    common: dict[str, Any] = {
        "status_code": response.status_code,
        "request_id": _request_id(response),
        "method": response.request.method,
        "url": str(response.request.url.copy_with(query=None)),
    }
    if _is_oauth_error_body(body):
        return errors.OAuthError(
            error=body["error"],
            error_description=body.get("error_description"),
            **common,
        )
    common["code"] = body.get("code")
    common["api_message"] = body.get("message")
    if response.status_code == 429:
        return errors.RateLimitedError(
            retry_after=parse_retry_after(response, body), **common
        )
    if response.status_code >= 500:
        return errors.ServerError(**common)
    error_class = _STATUS_ERRORS.get(response.status_code, errors.ApiError)
    return error_class(**common)


def parse_model(response: httpx.Response, model: type[ModelT]) -> ModelT:
    """Validate a JSON response into ``model`` or raise ``ResponseParseError``.

    The error detail lists failing field locations and error types only, never
    input values, so it is safe to log.
    """
    url = str(response.request.url.copy_with(query=None))
    try:
        payload = response.json()
    except ValueError as exc:
        raise errors.ResponseParseError(
            status_code=response.status_code,
            url=url,
            model=model.__name__,
            detail="body is not valid JSON",
        ) from exc
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or '<root>'}: "
            f"{issue['type']}"
            for issue in exc.errors(include_input=False)
        )
        raise errors.ResponseParseError(
            status_code=response.status_code,
            url=url,
            model=model.__name__,
            detail=problems,
        ) from exc

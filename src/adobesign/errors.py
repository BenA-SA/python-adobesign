"""Exception hierarchy for the Acrobat Sign client.

Every failure raised by this package derives from :class:`AdobeSignError`, so
callers can catch the whole family in one ``except``. Each subclass names one
failure mode and carries the values that caused it as attributes; the message
is built from those attributes, and the same values are mirrored into
``context`` so they can be splatted straight into a structured log call::

    except AdobeSignError as exc:
        logger.warning("adobesign.call_failed", **exc.context)

No secret (access token, client secret, refresh token) is ever stored on an
exception.
"""

from __future__ import annotations

from typing import Any


class AdobeSignError(Exception):
    """Base class for every error raised by ``adobesign``.

    ``context`` holds the structured values that describe the failure.
    """

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context


class ApiError(AdobeSignError):
    """Acrobat Sign answered with a non-success HTTP status.

    ``code`` and ``api_message`` come from the documented error body
    (``{"code": "...", "message": "..."}``); ``request_id`` is read from the
    response headers when Acrobat Sign supplies one.
    """

    def __init__(
        self,
        *,
        status_code: int,
        code: str | None = None,
        api_message: str | None = None,
        request_id: str | None = None,
        method: str | None = None,
        url: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.api_message = api_message
        self.request_id = request_id
        self.method = method
        self.url = url
        super().__init__(
            self._build_message(),
            status_code=status_code,
            code=code,
            api_message=api_message,
            request_id=request_id,
            method=method,
            url=url,
        )

    def _build_message(self) -> str:
        summary = f"Acrobat Sign returned HTTP {self.status_code}"
        if self.code:
            summary += f" {self.code}"
        if self.api_message:
            summary += f": {self.api_message}"
        if self.method and self.url:
            summary += f" ({self.method} {self.url})"
        if self.request_id:
            summary += f" [request_id={self.request_id}]"
        return summary


class BadRequestError(ApiError):
    """HTTP 400: the request was malformed or failed validation."""


class AuthenticationError(ApiError):
    """HTTP 401: the access token or integration key was missing or invalid."""


class PermissionDeniedError(ApiError):
    """HTTP 403: the caller is authenticated but not allowed to do this."""


class NotFoundError(ApiError):
    """HTTP 404: the resource does not exist or is not visible to the caller."""


class ConflictError(ApiError):
    """HTTP 409: the resource is in a state that forbids the operation."""


class RateLimitedError(ApiError):
    """HTTP 429: Acrobat Sign throttled the caller.

    ``retry_after`` is the number of seconds Acrobat Sign asked the caller to
    wait, taken from the ``Retry-After`` header (or the ``retryAfter`` body
    field), or ``None`` when neither was supplied.
    """

    def __init__(self, *, retry_after: float | None = None, **kwargs: Any) -> None:
        self.retry_after = retry_after
        super().__init__(**kwargs)
        self.context["retry_after"] = retry_after

    def _build_message(self) -> str:
        message = super()._build_message()
        if self.retry_after is None:
            return message
        return f"{message} (retry after {self.retry_after:g}s)"


class ServerError(ApiError):
    """HTTP 5xx: Acrobat Sign failed to handle the request."""


class OAuthError(AuthenticationError):
    """The OAuth token endpoint or authorisation redirect reported an error.

    ``error`` and ``error_description`` are the standard OAuth 2.0 error
    fields. ``status_code`` is ``0`` when the error arrived on the
    authorisation redirect rather than in an HTTP response.
    """

    def __init__(
        self,
        *,
        error: str,
        error_description: str | None = None,
        status_code: int = 0,
        **kwargs: Any,
    ) -> None:
        self.error = error
        self.error_description = error_description
        super().__init__(
            status_code=status_code,
            code=error,
            api_message=error_description,
            **kwargs,
        )
        self.context.update(error=error, error_description=error_description)

    def _build_message(self) -> str:
        message = f"OAuth error {self.error}"
        if self.error_description:
            message += f": {self.error_description}"
        if self.status_code:
            message += f" (HTTP {self.status_code})"
        return message


class ResponseParseError(AdobeSignError):
    """A response body was not valid JSON or did not match the expected model."""

    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        model: str,
        detail: str,
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.model = model
        self.detail = detail
        super().__init__(
            f"Could not parse HTTP {status_code} response from {url} as "
            f"{model}: {detail}",
            status_code=status_code,
            url=url,
            model=model,
            detail=detail,
        )


class TransportError(AdobeSignError):
    """The HTTP request never produced a response (DNS, TLS, timeout, reset)."""

    def __init__(self, *, method: str, url: str, reason: str) -> None:
        self.method = method
        self.url = url
        self.reason = reason
        super().__init__(
            f"{method} {url} failed before a response arrived: {reason}",
            method=method,
            url=url,
            reason=reason,
        )


class MissingRefreshTokenError(AdobeSignError):
    """The access token has expired and there is no refresh token to renew it."""

    def __init__(self) -> None:
        super().__init__(
            "The OAuth access token has expired and no refresh token is available; "
            "send the user through the authorisation flow again."
        )


class WebhookClientIdError(AdobeSignError):
    """A webhook request's ``X-AdobeSign-ClientId`` was missing or unexpected.

    Per the Acrobat Sign webhook docs, an endpoint must not answer a request
    whose client id it does not recognise with a success status.
    """

    def __init__(self, *, received_client_id: str | None) -> None:
        self.received_client_id = received_client_id
        reason = (
            "missing"
            if received_client_id is None
            else f"{received_client_id!r} is not an expected client id"
        )
        super().__init__(
            f"Webhook X-AdobeSign-ClientId header {reason}",
            received_client_id=received_client_id,
        )


class WebhookPayloadError(AdobeSignError):
    """A webhook notification body was not valid JSON or not a notification."""

    def __init__(self, *, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Invalid webhook notification body: {detail}", detail=detail)


class OAuthStateMismatchError(AdobeSignError):
    """The OAuth redirect's ``state`` did not match the one the app issued.

    This is the CSRF check of the authorisation-code flow; the values are not
    included because the state is a per-session secret.
    """

    def __init__(self) -> None:
        super().__init__(
            "The OAuth redirect state does not match the expected state; "
            "refusing to exchange the authorisation code."
        )


class MissingAccessPointError(AdobeSignError):
    """OAuth tokens have no ``api_access_point`` to send a refresh to.

    Tokens from :meth:`~adobesign.auth.OAuthApp.exchange_code` always carry
    one; this happens with hand-built or partially persisted tokens.
    """

    def __init__(self) -> None:
        super().__init__(
            "The OAuth tokens have no api_access_point, so they cannot be "
            "refreshed; set it (e.g. ADOBESIGN_BASE_URI for the CLI) or log in "
            "again."
        )

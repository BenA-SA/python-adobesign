"""Exception messages are built from attributes and mirrored into ``context``."""

from __future__ import annotations

import pytest

from adobesign import AdobeSignError
from adobesign import ApiError
from adobesign import MissingAccessPointError
from adobesign import MissingRefreshTokenError
from adobesign import NotFoundError
from adobesign import OAuthError
from adobesign import OAuthStateMismatchError
from adobesign import RateLimitedError
from adobesign import ResponseParseError
from adobesign import TransportError
from adobesign import WebhookClientIdError
from adobesign import WebhookPayloadError


def test_api_error_message_and_context() -> None:
    error = NotFoundError(
        status_code=404,
        code="INVALID_AGREEMENT_ID",
        api_message="Agreement not found",
        request_id="req-1",
        method="GET",
        url="https://api.eu1.adobesign.com/api/rest/v6/agreements/x",
    )

    assert str(error) == (
        "Acrobat Sign returned HTTP 404 INVALID_AGREEMENT_ID: Agreement not found "
        "(GET https://api.eu1.adobesign.com/api/rest/v6/agreements/x) "
        "[request_id=req-1]"
    )
    assert error.context == {
        "status_code": 404,
        "code": "INVALID_AGREEMENT_ID",
        "api_message": "Agreement not found",
        "request_id": "req-1",
        "method": "GET",
        "url": "https://api.eu1.adobesign.com/api/rest/v6/agreements/x",
    }
    assert isinstance(error, ApiError)
    assert isinstance(error, AdobeSignError)


def test_minimal_api_error_message() -> None:
    assert str(ApiError(status_code=502)) == "Acrobat Sign returned HTTP 502"


def test_rate_limited_context_includes_retry_after() -> None:
    error = RateLimitedError(status_code=429, retry_after=2.5)

    assert error.context["retry_after"] == 2.5
    assert str(error) == "Acrobat Sign returned HTTP 429 (retry after 2.5s)"


def test_oauth_error_message_variants() -> None:
    assert str(OAuthError(error="invalid_client")) == "OAuth error invalid_client"
    error = OAuthError(error="invalid_grant", error_description="bad", status_code=400)
    assert str(error) == "OAuth error invalid_grant: bad (HTTP 400)"
    assert error.context["error"] == "invalid_grant"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ResponseParseError(status_code=200, url="u", model="M", detail="d"),
            "Could not parse HTTP 200 response from u as M: d",
        ),
        (
            TransportError(method="GET", url="u", reason="ConnectError"),
            "GET u failed before a response arrived: ConnectError",
        ),
        (
            WebhookPayloadError(detail="body is not valid JSON"),
            "Invalid webhook notification body: body is not valid JSON",
        ),
        (
            WebhookClientIdError(received_client_id=None),
            "Webhook X-AdobeSign-ClientId header missing",
        ),
    ],
)
def test_structured_error_messages(error: AdobeSignError, expected: str) -> None:
    assert str(error) == expected
    assert error.message == expected


def test_message_only_errors() -> None:
    assert "refresh token" in str(MissingRefreshTokenError())
    assert "state" in str(OAuthStateMismatchError())
    assert "api_access_point" in str(MissingAccessPointError())

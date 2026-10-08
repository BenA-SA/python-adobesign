"""Every HTTP-calling method run through the same failure scenarios.

Error bodies follow the documented v6 error shape ``{"code", "message"}``
(API reference, "Error codes" on each operation); 429 handling follows the
"Throttling" section of the Acrobat Sign developer guide (``Retry-After``).
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from adobesign import AdobeSignClient
from adobesign import AuthenticationError
from adobesign import BadRequestError
from adobesign import ConflictError
from adobesign import NotFoundError
from adobesign import OAuthApp
from adobesign import PermissionDeniedError
from adobesign import RateLimitedError
from adobesign import ResponseParseError
from adobesign import ServerError
from adobesign import TransportError
from adobesign.errors import ApiError

from endpoint_cases import ENDPOINT_CASES
from endpoint_cases import EndpointCase
from endpoint_cases import Env
from helpers import MAX_RETRIES
from helpers import SleepRecorder

CASES = pytest.mark.parametrize("case", ENDPOINT_CASES, ids=lambda c: c.target)
JSON_CASES = pytest.mark.parametrize(
    "case",
    [case for case in ENDPOINT_CASES if case.returns_json],
    ids=lambda c: c.target,
)
REQUEST_ID = "4f6a1c2e-request-id"


@pytest.fixture
def env(client: AdobeSignClient, oauth_app: OAuthApp) -> Env:
    return Env(client=client, oauth_app=oauth_app)


def route_for(mock_api: respx.MockRouter, case: EndpointCase) -> respx.Route:
    return mock_api.route(
        method=case.method, url__regex=rf"^{re.escape(case.url)}(\?.*)?$"
    )


def api_error(status: int, code: str, **headers: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"code": code, "message": f"{code} message"},
        headers={"X-Request-Id": REQUEST_ID, **headers},
    )


@CASES
@pytest.mark.parametrize(
    ("status", "code", "error_class"),
    [
        (400, "INVALID_ARGUMENTS", BadRequestError),
        (401, "INVALID_ACCESS_TOKEN", AuthenticationError),
        (403, "PERMISSION_DENIED", PermissionDeniedError),
        (404, "RESOURCE_NOT_FOUND", NotFoundError),
        (409, "AGREEMENT_NOT_EXPOSED", ConflictError),
        (418, "UNEXPECTED_STATUS", ApiError),
    ],
)
def test_client_errors_map_to_typed_exceptions_without_retry(
    case: EndpointCase,
    status: int,
    code: str,
    error_class: type[ApiError],
    env: Env,
    mock_api: respx.MockRouter,
    sleeps: SleepRecorder,
) -> None:
    route = route_for(mock_api, case).mock(return_value=api_error(status, code))

    with pytest.raises(error_class) as caught:
        case.call(env)

    assert type(caught.value) is error_class
    assert caught.value.status_code == status
    assert caught.value.code == code
    assert caught.value.api_message == f"{code} message"
    assert caught.value.request_id == REQUEST_ID
    assert caught.value.method == case.method
    assert route.call_count == 1
    assert sleeps.calls == []


@CASES
def test_429_waits_for_retry_after_then_succeeds(
    case: EndpointCase,
    env: Env,
    mock_api: respx.MockRouter,
    sleeps: SleepRecorder,
) -> None:
    route = route_for(mock_api, case).mock(
        side_effect=[
            api_error(429, "THROTTLING_TOO_MANY_REQUESTS", **{"Retry-After": "7"}),
            case.success(),
        ]
    )

    case.call(env)

    assert route.call_count == 2
    assert sleeps.calls == [7.0]


@CASES
def test_429_exhausted_raises_rate_limited_with_retry_after(
    case: EndpointCase,
    env: Env,
    mock_api: respx.MockRouter,
    sleeps: SleepRecorder,
) -> None:
    route = route_for(mock_api, case).mock(
        return_value=api_error(
            429, "THROTTLING_TOO_MANY_REQUESTS", **{"Retry-After": "7"}
        )
    )

    with pytest.raises(RateLimitedError) as caught:
        case.call(env)

    assert caught.value.retry_after == 7.0
    assert caught.value.status_code == 429
    assert route.call_count == MAX_RETRIES + 1
    assert sleeps.calls == [7.0] * MAX_RETRIES


@CASES
def test_5xx_then_success_retries_only_idempotent_methods(
    case: EndpointCase,
    env: Env,
    mock_api: respx.MockRouter,
    sleeps: SleepRecorder,
) -> None:
    route = route_for(mock_api, case).mock(
        side_effect=[api_error(503, "SERVICE_UNAVAILABLE"), case.success()]
    )

    if not case.idempotent:
        with pytest.raises(ServerError):
            case.call(env)
        assert route.call_count == 1
        assert sleeps.calls == []
        return

    case.call(env)
    assert route.call_count == 2
    assert sleeps.calls == [0.5]


@CASES
def test_5xx_exhausted_raises_server_error(
    case: EndpointCase,
    env: Env,
    mock_api: respx.MockRouter,
    sleeps: SleepRecorder,
) -> None:
    route = route_for(mock_api, case).mock(
        return_value=api_error(500, "MISC_SERVER_ERROR")
    )

    with pytest.raises(ServerError) as caught:
        case.call(env)

    assert caught.value.status_code == 500
    expected_calls = MAX_RETRIES + 1 if case.idempotent else 1
    assert route.call_count == expected_calls
    assert sleeps.calls == [0.5, 1.0][: expected_calls - 1]


@CASES
def test_network_failure_retries_only_idempotent_methods(
    case: EndpointCase,
    env: Env,
    mock_api: respx.MockRouter,
    sleeps: SleepRecorder,
) -> None:
    route = route_for(mock_api, case).mock(
        side_effect=[httpx.ConnectError("connection refused"), case.success()]
    )

    if not case.idempotent:
        with pytest.raises(TransportError) as caught:
            case.call(env)
        assert caught.value.reason == "ConnectError"
        assert route.call_count == 1
        return

    case.call(env)
    assert route.call_count == 2
    assert sleeps.calls == [0.5]


@JSON_CASES
def test_malformed_json_raises_response_parse_error(
    case: EndpointCase, env: Env, mock_api: respx.MockRouter
) -> None:
    route_for(mock_api, case).mock(
        return_value=httpx.Response(200, text="<html>gateway hiccup</html>")
    )

    with pytest.raises(ResponseParseError) as caught:
        case.call(env)

    assert caught.value.detail == "body is not valid JSON"
    assert caught.value.status_code == 200


@JSON_CASES
def test_wrong_shape_json_raises_response_parse_error(
    case: EndpointCase, env: Env, mock_api: respx.MockRouter
) -> None:
    route_for(mock_api, case).mock(return_value=httpx.Response(200, json=[]))

    with pytest.raises(ResponseParseError) as caught:
        case.call(env)

    assert "model_type" in caught.value.detail or "dict_type" in caught.value.detail


@JSON_CASES
def test_unexpected_response_fields_are_tolerated(
    case: EndpointCase, env: Env, mock_api: respx.MockRouter
) -> None:
    body = case.success().json()
    body["someFieldAdobeAddsLater"] = {"nested": True}
    route_for(mock_api, case).mock(return_value=httpx.Response(200, json=body))

    result = case.call(env)

    assert result is not None

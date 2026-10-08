"""Retry/backoff policy details and error construction not covered per endpoint.

Throttling behaviour follows the Acrobat Sign developer guide's "API usage /
throttling" section: HTTP 429 with code ``THROTTLING_TOO_MANY_REQUESTS``, a
``Retry-After`` header (seconds) and a ``retryAfter`` body field.
"""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from email.utils import format_datetime

import httpx
import pytest
import respx

from adobesign import AdobeSignClient
from adobesign import IntegrationKey
from adobesign import NotFoundError
from adobesign import RateLimitedError
from adobesign import RetryPolicy
from adobesign import ServerError
from adobesign import TransportError

from endpoint_cases import AGREEMENT_ID
from endpoint_cases import sample_agreement
from helpers import ACCESS_POINT
from helpers import API
from helpers import INTEGRATION_KEY
from helpers import SleepRecorder

AGREEMENT_URL = f"{API}/agreements/{AGREEMENT_ID}"


def client_with(policy: RetryPolicy, sleeps: SleepRecorder) -> AdobeSignClient:
    return AdobeSignClient(
        IntegrationKey(INTEGRATION_KEY),
        api_access_point=ACCESS_POINT,
        retry=policy,
        sleep=sleeps,
    )


def throttled(**kwargs: object) -> httpx.Response:
    return httpx.Response(429, **kwargs)  # type: ignore[arg-type]


@pytest.mark.covers("RetryPolicy.delay")
def test_delay_grows_exponentially_and_caps() -> None:
    policy = RetryPolicy(backoff_factor=1.0, max_backoff=5.0, jitter=False)

    assert [policy.delay(n, None) for n in range(5)] == [1.0, 2.0, 4.0, 5.0, 5.0]
    assert policy.delay(3, retry_after=0.25) == 0.25


@pytest.mark.covers("RetryPolicy.delay")
def test_delay_full_jitter_stays_within_backoff() -> None:
    policy = RetryPolicy(backoff_factor=1.0, max_backoff=8.0)

    delays = [policy.delay(2, None) for _ in range(200)]

    assert all(0.0 <= delay <= 4.0 for delay in delays)
    assert len(set(delays)) > 1


def test_retry_after_http_date_is_converted_to_seconds(
    mock_api: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    mock_api.get(AGREEMENT_URL).mock(
        side_effect=[
            throttled(headers={"Retry-After": format_datetime(when, usegmt=True)}),
            httpx.Response(200, json={"id": "a", "name": "n", "status": "SIGNED"}),
        ]
    )

    with client_with(RetryPolicy(jitter=False), sleeps) as client:
        client.agreements.get(AGREEMENT_ID)

    assert 25.0 <= sleeps.calls[0] <= 30.0


def test_retry_after_in_the_past_waits_zero(
    mock_api: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    mock_api.get(AGREEMENT_URL).mock(
        side_effect=[
            throttled(headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}),
            throttled(headers={"Retry-After": "-5"}),
            httpx.Response(200, json={"id": "a", "name": "n", "status": "SIGNED"}),
        ]
    )

    with client_with(RetryPolicy(jitter=False), sleeps) as client:
        client.agreements.get(AGREEMENT_ID)

    assert sleeps.calls == [0.0, 0.0]


def test_retry_after_falls_back_to_body_field(
    mock_api: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    mock_api.get(AGREEMENT_URL).mock(
        return_value=throttled(
            json={"code": "THROTTLING_TOO_MANY_REQUESTS", "retryAfter": 12}
        )
    )

    with (
        client_with(RetryPolicy(max_retries=1, jitter=False), sleeps) as client,
        pytest.raises(RateLimitedError) as caught,
    ):
        client.agreements.get(AGREEMENT_ID)

    assert caught.value.retry_after == 12.0
    assert sleeps.calls == [12.0]
    assert str(caught.value).endswith("(retry after 12s)")


def test_unparseable_retry_after_uses_backoff(
    mock_api: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    mock_api.get(AGREEMENT_URL).mock(
        return_value=throttled(headers={"Retry-After": "soon-ish"}, text="busy")
    )

    with (
        client_with(RetryPolicy(max_retries=2, jitter=False), sleeps) as client,
        pytest.raises(RateLimitedError) as caught,
    ):
        client.agreements.get(AGREEMENT_ID)

    assert caught.value.retry_after is None
    assert caught.value.code is None
    assert sleeps.calls == [0.5, 1.0]
    assert "retry after" not in str(caught.value)


def test_retry_after_beyond_ceiling_is_raised_not_waited(
    mock_api: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    route = mock_api.get(AGREEMENT_URL).mock(
        return_value=throttled(headers={"Retry-After": "3600"})
    )

    with (
        client_with(RetryPolicy(max_retry_after=60), sleeps) as client,
        pytest.raises(RateLimitedError) as caught,
    ):
        client.agreements.get(AGREEMENT_ID)

    assert caught.value.retry_after == 3600.0
    assert route.call_count == 1
    assert sleeps.calls == []


def test_retry_non_idempotent_opt_in_retries_post(
    mock_api: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    route = mock_api.post(f"{API}/agreements").mock(
        side_effect=[
            httpx.Response(502),
            httpx.ConnectTimeout("slow"),
            httpx.Response(201, json={"id": "new"}),
        ]
    )
    policy = RetryPolicy(retry_non_idempotent=True, jitter=False)

    with client_with(policy, sleeps) as client:
        created = client.agreements.create(sample_agreement())

    assert created.id == "new"
    assert route.call_count == 3
    sent = [json.loads(call.request.content) for call in route.calls]
    assert sent[0] == sent[2]


def test_status_outside_retry_statuses_is_not_retried(
    mock_api: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    route = mock_api.get(AGREEMENT_URL).mock(return_value=httpx.Response(501))

    with (
        client_with(RetryPolicy(), sleeps) as client,
        pytest.raises(ServerError) as caught,
    ):
        client.agreements.get(AGREEMENT_ID)

    assert route.call_count == 1
    assert caught.value.code is None
    assert caught.value.request_id is None


def test_network_failure_exhausts_retries(
    mock_api: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    route = mock_api.get(AGREEMENT_URL).mock(side_effect=httpx.ReadTimeout("slow"))

    with (
        client_with(RetryPolicy(max_retries=2, jitter=False), sleeps) as client,
        pytest.raises(TransportError) as caught,
    ):
        client.agreements.get(AGREEMENT_ID)

    assert route.call_count == 3
    assert caught.value.reason == "ReadTimeout"
    assert caught.value.url == AGREEMENT_URL
    assert INTEGRATION_KEY not in str(caught.value)


def test_error_url_excludes_query_string(
    mock_api: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    mock_api.get(f"{AGREEMENT_URL}/signingUrls").mock(
        return_value=httpx.Response(
            404,
            json={"code": "AGREEMENT_NOT_SIGNABLE", "message": "Not signable yet"},
        )
    )

    with (
        client_with(RetryPolicy(), sleeps) as client,
        pytest.raises(NotFoundError) as caught,
    ):
        client.agreements.get_signing_urls(AGREEMENT_ID, frame_parent="x")

    assert str(caught.value) == (
        "Acrobat Sign returned HTTP 404 AGREEMENT_NOT_SIGNABLE: Not signable yet "
        f"(GET {AGREEMENT_URL}/signingUrls)"
    )


def test_bare_429_has_no_retry_after_and_uses_backoff(
    mock_api: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    mock_api.get(AGREEMENT_URL).mock(
        side_effect=[
            throttled(json={"code": "THROTTLING_TOO_MANY_REQUESTS"}),
            throttled(headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 -0000"}),
            httpx.Response(200, json={"id": "a", "name": "n", "status": "SIGNED"}),
        ]
    )

    with client_with(RetryPolicy(jitter=False), sleeps) as client:
        client.agreements.get(AGREEMENT_ID)

    assert sleeps.calls == [0.5, 0.0]

"""Client construction and per-account access-point discovery.

Modelled on the v6 API reference, "base_uris" → ``GET /baseUris``
(``{"apiAccessPoint", "webAccessPoint"}``) and the developer guide's advice to
send every subsequent call to the returned ``apiAccessPoint``.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from adobesign import AdobeSignClient
from adobesign import IntegrationKey
from adobesign import OAuthApp
from adobesign import OAuthCredentials
from adobesign import TokenSet

from endpoint_cases import AGREEMENT_ID
from helpers import ACCESS_POINT
from helpers import INTEGRATION_KEY
from helpers import SleepRecorder
from helpers import assert_bearer
from helpers import deterministic_retry_policy
from helpers import json_response

DISCOVERY_URL = "https://api.adobesign.com/api/rest/v6/baseUris"


def undiscovered_client(sleeps: SleepRecorder) -> AdobeSignClient:
    return AdobeSignClient(
        IntegrationKey(INTEGRATION_KEY),
        retry=deterministic_retry_policy(),
        sleep=sleeps,
    )


@pytest.mark.covers("AdobeSignClient.discover_base_uris")
def test_discover_base_uris_parses_and_adopts_access_point(
    mock_api: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    route = mock_api.get(DISCOVERY_URL).mock(
        return_value=json_response("base_uris.json")
    )

    with undiscovered_client(sleeps) as client:
        base_uris = client.discover_base_uris()
        assert client.api_base_url == "https://api.eu1.adobesign.com/api/rest/v6"

    assert base_uris.api_access_point == "https://api.eu1.adobesign.com/"
    assert base_uris.web_access_point == "https://secure.eu1.adobesign.com/"
    assert_bearer(route.calls.last.request)
    assert route.call_count == 1


@pytest.mark.covers("AdobeSignClient.api_base_url")
def test_first_call_discovers_access_point_once(
    mock_api: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    discovery = mock_api.get(DISCOVERY_URL).mock(
        return_value=json_response("base_uris.json")
    )
    agreement = mock_api.get(
        f"https://api.eu1.adobesign.com/api/rest/v6/agreements/{AGREEMENT_ID}"
    ).mock(return_value=json_response("agreement.json"))

    with undiscovered_client(sleeps) as client:
        client.agreements.get(AGREEMENT_ID)
        client.agreements.get(AGREEMENT_ID)

    assert discovery.call_count == 1
    assert agreement.call_count == 2


@pytest.mark.covers("AdobeSignClient.api_base_url")
def test_explicit_access_point_skips_discovery(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    discovery = mock_api.get(DISCOVERY_URL)

    assert client.api_base_url == "https://api.eu1.adobesign.com/api/rest/v6"
    assert not discovery.called


@pytest.mark.covers("AdobeSignClient.api_base_url")
def test_oauth_tokens_supply_access_point(
    mock_api: respx.MockRouter, oauth_app: OAuthApp
) -> None:
    discovery = mock_api.get(DISCOVERY_URL)
    credentials = OAuthCredentials.from_tokens(
        oauth_app,
        TokenSet(access_token="a", api_access_point="https://api.na2.adobesign.com"),
    )

    with AdobeSignClient(credentials) as client:
        assert client.api_base_url == "https://api.na2.adobesign.com/api/rest/v6"

    assert not discovery.called


@pytest.mark.covers("AdobeSignClient.discover_base_uris")
def test_custom_discovery_url(mock_api: respx.MockRouter) -> None:
    route = mock_api.get("https://api.na1.adobesign.com/api/rest/v6/baseUris").mock(
        return_value=json_response("base_uris.json")
    )

    with AdobeSignClient(
        IntegrationKey(INTEGRATION_KEY),
        discovery_url="https://api.na1.adobesign.com/api/rest/v6/baseUris",
    ) as client:
        client.discover_base_uris()

    assert route.called


@pytest.mark.covers("AdobeSignClient.close")
def test_close_releases_owned_http_client() -> None:
    client = AdobeSignClient(IntegrationKey(INTEGRATION_KEY))
    http = client._transport.http

    client.close()

    assert http.is_closed


@pytest.mark.covers("AdobeSignClient.close")
def test_close_leaves_injected_http_client_open() -> None:
    http = httpx.Client()
    with AdobeSignClient(
        IntegrationKey(INTEGRATION_KEY),
        api_access_point=ACCESS_POINT,
        http_client=http,
    ):
        pass

    assert not http.is_closed
    http.close()

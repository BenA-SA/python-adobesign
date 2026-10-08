"""OAuth 2.0 authorisation-code flow, token refresh and credential behaviour.

Modelled on the Acrobat Sign developer guide, "Managing OAuth tokens":
authorise at ``/public/oauth/v2``; the redirect carries ``code``, ``state``,
``api_access_point`` and ``web_access_point``; ``POST
{api_access_point}oauth/v2/token`` (form-encoded ``grant_type=
authorization_code``) returns ``access_token``, ``refresh_token``,
``token_type`` and ``expires_in``; ``POST {api_access_point}oauth/v2/refresh``
(``grant_type=refresh_token``) returns a new access token without a new
refresh token. OAuth errors use the RFC 6749 ``error`` /
``error_description`` body.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import httpx
import pytest
import respx

from adobesign import AdobeSignClient
from adobesign import AuthenticationError
from adobesign import InMemoryTokenStore
from adobesign import IntegrationKey
from adobesign import MissingAccessPointError
from adobesign import MissingRefreshTokenError
from adobesign import OAuthApp
from adobesign import OAuthCredentials
from adobesign import OAuthError
from adobesign import OAuthStateMismatchError
from adobesign import TokenSet

from endpoint_cases import AGREEMENT_ID
from helpers import ACCESS_POINT
from helpers import API
from helpers import CLIENT_ID
from helpers import CLIENT_SECRET
from helpers import REDIRECT_URI
from helpers import SleepRecorder
from helpers import deterministic_retry_policy
from helpers import json_response

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
TOKEN_URL = f"{ACCESS_POINT}oauth/v2/token"
REFRESH_URL = f"{ACCESS_POINT}oauth/v2/refresh"
AGREEMENT_URL = f"{API}/agreements/{AGREEMENT_ID}"


def form_fields(request: httpx.Request) -> dict[str, str]:
    return {
        key: values[0] for key, values in parse_qs(request.content.decode()).items()
    }


@pytest.fixture
def app(sleeps: SleepRecorder, mock_api: respx.MockRouter) -> Iterator[OAuthApp]:
    oauth_app = OAuthApp(
        CLIENT_ID,
        CLIENT_SECRET,
        REDIRECT_URI,
        retry=deterministic_retry_policy(),
        sleep=sleeps,
        clock=lambda: NOW,
    )
    yield oauth_app
    oauth_app.close()


def tokens(
    *, expires_at: datetime | None = None, refresh: str | None = "rt"
) -> TokenSet:
    return TokenSet(
        access_token="old-access",
        refresh_token=refresh,
        expires_at=expires_at,
        api_access_point=ACCESS_POINT,
        web_access_point="https://secure.eu1.adobesign.com/",
    )


@pytest.mark.covers("OAuthApp.authorization_url")
def test_authorization_url_carries_documented_parameters(app: OAuthApp) -> None:
    url = app.authorization_url(
        ["user_login:self", "agreement_write:account"], state="csrf-123"
    )

    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "https://secure.adobesign.com/public/oauth/v2"
    )
    assert parse_qs(parts.query) == {
        "response_type": ["code"],
        "client_id": [CLIENT_ID],
        "redirect_uri": [REDIRECT_URI],
        "scope": ["user_login:self agreement_write:account"],
        "state": ["csrf-123"],
    }


@pytest.mark.covers("OAuthApp.authorization_url")
def test_authorization_url_honours_custom_shard() -> None:
    app = OAuthApp(
        CLIENT_ID,
        CLIENT_SECRET,
        REDIRECT_URI,
        authorize_url="https://secure.eu1.adobesign.com/public/oauth/v2",
    )

    assert app.authorization_url([], state="s").startswith(
        "https://secure.eu1.adobesign.com/public/oauth/v2?"
    )
    app.close()


@pytest.mark.covers("OAuthApp.parse_redirect")
def test_parse_redirect_extracts_code_and_access_points(app: OAuthApp) -> None:
    redirect = app.parse_redirect(
        f"{REDIRECT_URI}?code=CBNCKBAAHBCAABAA&state=csrf-123"
        "&api_access_point=https%3A%2F%2Fapi.eu1.adobesign.com%2F"
        "&web_access_point=https%3A%2F%2Fsecure.eu1.adobesign.com%2F",
        expected_state="csrf-123",
    )

    assert redirect.code == "CBNCKBAAHBCAABAA"
    assert redirect.api_access_point == "https://api.eu1.adobesign.com/"
    assert redirect.web_access_point == "https://secure.eu1.adobesign.com/"


@pytest.mark.covers("OAuthApp.parse_redirect")
def test_parse_redirect_reports_denied_consent(app: OAuthApp) -> None:
    with pytest.raises(OAuthError) as caught:
        app.parse_redirect(
            f"{REDIRECT_URI}?error=access_denied&error_description=User+declined",
            expected_state="csrf-123",
        )

    assert caught.value.error == "access_denied"
    assert caught.value.error_description == "User declined"
    assert caught.value.status_code == 0
    assert str(caught.value) == "OAuth error access_denied: User declined"


@pytest.mark.covers("OAuthApp.parse_redirect")
def test_parse_redirect_rejects_wrong_state(app: OAuthApp) -> None:
    with pytest.raises(OAuthStateMismatchError):
        app.parse_redirect(f"{REDIRECT_URI}?code=c&state=evil", expected_state="good")


@pytest.mark.covers("OAuthApp.parse_redirect")
def test_parse_redirect_requires_code(app: OAuthApp) -> None:
    with pytest.raises(OAuthError) as caught:
        app.parse_redirect(f"{REDIRECT_URI}?state=s", expected_state="s")

    assert caught.value.error == "missing_code"


@pytest.mark.covers("OAuthApp.exchange_code")
def test_exchange_code_posts_form_and_builds_token_set(
    app: OAuthApp, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(TOKEN_URL).mock(
        return_value=json_response("oauth_token.json")
    )

    token_set = app.exchange_code("auth-code", api_access_point=ACCESS_POINT)

    request = route.calls.last.request
    assert "Authorization" not in request.headers
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert form_fields(request) == {
        "grant_type": "authorization_code",
        "code": "auth-code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
    }
    assert token_set.access_token == "3AAABLblqZhBexampleAccessToken"
    assert token_set.refresh_token == "3AAABLblqZhCexampleRefreshToken"
    assert token_set.expires_at == NOW + timedelta(seconds=3600)
    assert token_set.api_access_point == "https://api.eu1.adobesign.com/"


@pytest.mark.covers("OAuthApp.exchange_code")
def test_exchange_code_falls_back_to_redirect_access_point(
    app: OAuthApp, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "a", "refresh_token": "r"}
        )
    )

    token_set = app.exchange_code("c", api_access_point=ACCESS_POINT.rstrip("/"))

    assert route.called
    assert token_set.api_access_point == "https://api.eu1.adobesign.com"
    assert token_set.expires_at is None


@pytest.mark.covers("OAuthApp.exchange_code")
def test_exchange_code_maps_oauth_error_body(
    app: OAuthApp, mock_api: respx.MockRouter
) -> None:
    mock_api.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "code expired"},
        )
    )

    with pytest.raises(OAuthError) as caught:
        app.exchange_code("stale", api_access_point=ACCESS_POINT)

    assert caught.value.error == "invalid_grant"
    assert caught.value.error_description == "code expired"
    assert caught.value.status_code == 400
    assert isinstance(caught.value, AuthenticationError)
    assert CLIENT_SECRET not in str(caught.value.context)


@pytest.mark.covers("OAuthApp.refresh")
def test_refresh_keeps_refresh_token_and_access_point(
    app: OAuthApp, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(REFRESH_URL).mock(
        return_value=json_response("oauth_refresh.json")
    )

    renewed = app.refresh(tokens())

    assert form_fields(route.calls.last.request) == {
        "grant_type": "refresh_token",
        "refresh_token": "rt",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    assert renewed.access_token == "3AAABLblqZhBexampleRefreshedAccessToken"
    assert renewed.refresh_token == "rt"
    assert renewed.api_access_point == ACCESS_POINT
    assert renewed.web_access_point == "https://secure.eu1.adobesign.com/"
    assert renewed.expires_at == NOW + timedelta(seconds=3600)


@pytest.mark.covers("OAuthApp.refresh")
def test_refresh_without_refresh_token_raises(app: OAuthApp) -> None:
    with pytest.raises(MissingRefreshTokenError):
        app.refresh(tokens(refresh=None))


@pytest.mark.covers("OAuthApp.refresh")
def test_refresh_without_access_point_raises(app: OAuthApp) -> None:
    with pytest.raises(MissingAccessPointError):
        app.refresh(TokenSet(access_token="a", refresh_token="r"))


@pytest.mark.covers("OAuthApp.close")
def test_oauth_app_close_and_repr_hide_secret() -> None:
    app = OAuthApp(CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
    http = app._transport.http

    app.close()

    assert http.is_closed
    assert CLIENT_SECRET not in repr(app)


@pytest.mark.covers("OAuthApp.close")
def test_oauth_app_close_leaves_injected_client_open() -> None:
    http = httpx.Client()
    OAuthApp(CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, http_client=http).close()

    assert not http.is_closed
    http.close()


@pytest.mark.covers("TokenSet.is_expired")
def test_token_set_expiry_uses_leeway() -> None:
    assert not tokens().is_expired(now=NOW)
    assert not tokens(expires_at=NOW + timedelta(minutes=5)).is_expired(now=NOW)
    assert tokens(expires_at=NOW + timedelta(seconds=30)).is_expired(now=NOW)
    assert tokens(expires_at=NOW - timedelta(days=1)).is_expired()


@pytest.mark.covers("TokenSet.is_expired")
def test_token_set_round_trips_and_hides_secrets_in_repr() -> None:
    original = tokens(expires_at=NOW)

    assert TokenSet.model_validate_json(original.model_dump_json()) == original
    assert "old-access" not in repr(original)
    assert "rt" not in repr(original).replace("expires_at", "")


@pytest.mark.covers("InMemoryTokenStore.load")
@pytest.mark.covers("InMemoryTokenStore.save")
def test_in_memory_store_saves_and_calls_back() -> None:
    saved: list[TokenSet] = []
    store = InMemoryTokenStore(tokens(), on_save=saved.append)
    renewed = tokens(refresh="new")

    store.save(renewed)

    assert store.load() is renewed
    assert saved == [renewed]
    InMemoryTokenStore(tokens()).save(renewed)


@pytest.mark.covers("IntegrationKey.authorization_header")
@pytest.mark.covers("IntegrationKey.handle_unauthorized")
@pytest.mark.covers("IntegrationKey.api_access_point")
def test_integration_key_is_a_static_bearer() -> None:
    key = IntegrationKey("secret-key")

    assert key.authorization_header() == "Bearer secret-key"
    assert key.handle_unauthorized() is False
    assert key.api_access_point is None
    assert "secret-key" not in repr(key)


@pytest.mark.covers("OAuthCredentials.authorization_header")
@pytest.mark.covers("OAuthCredentials.from_tokens")
@pytest.mark.covers("OAuthCredentials.api_access_point")
def test_credentials_use_current_token_until_near_expiry(
    app: OAuthApp, mock_api: respx.MockRouter
) -> None:
    refresh = mock_api.post(REFRESH_URL)
    credentials = OAuthCredentials.from_tokens(
        app, tokens(expires_at=NOW + timedelta(hours=1))
    )

    assert credentials.authorization_header() == "Bearer old-access"
    assert credentials.api_access_point == ACCESS_POINT
    assert not refresh.called
    assert "old-access" not in repr(credentials)


@pytest.mark.covers("OAuthCredentials.authorization_header")
@pytest.mark.covers("OAuthCredentials.refresh")
def test_expired_token_is_refreshed_before_the_request_and_persisted(
    app: OAuthApp, mock_api: respx.MockRouter
) -> None:
    mock_api.post(REFRESH_URL).mock(return_value=json_response("oauth_refresh.json"))
    agreement = mock_api.get(AGREEMENT_URL).mock(
        return_value=json_response("agreement.json")
    )
    persisted: list[TokenSet] = []
    credentials = OAuthCredentials(
        app,
        InMemoryTokenStore(tokens(expires_at=NOW), on_save=persisted.append),
        clock=lambda: NOW,
    )

    with AdobeSignClient(credentials) as client:
        client.agreements.get(AGREEMENT_ID)

    assert agreement.calls.last.request.headers["Authorization"] == (
        "Bearer 3AAABLblqZhBexampleRefreshedAccessToken"
    )
    assert [t.access_token for t in persisted] == [
        "3AAABLblqZhBexampleRefreshedAccessToken"
    ]


@pytest.mark.covers("OAuthCredentials.handle_unauthorized")
def test_unexpected_401_refreshes_once_and_replays(
    app: OAuthApp, mock_api: respx.MockRouter
) -> None:
    refresh = mock_api.post(REFRESH_URL).mock(
        return_value=json_response("oauth_refresh.json")
    )
    agreement = mock_api.get(AGREEMENT_URL).mock(
        side_effect=[
            httpx.Response(401, json={"code": "INVALID_ACCESS_TOKEN", "message": "x"}),
            json_response("agreement.json"),
        ]
    )
    credentials = OAuthCredentials.from_tokens(app, tokens())

    with AdobeSignClient(credentials) as client:
        result = client.agreements.get(AGREEMENT_ID)

    assert result.id == AGREEMENT_ID
    assert refresh.call_count == 1
    first, second = (call.request for call in agreement.calls)
    assert first.headers["Authorization"] == "Bearer old-access"
    assert second.headers["Authorization"] == (
        "Bearer 3AAABLblqZhBexampleRefreshedAccessToken"
    )


@pytest.mark.covers("OAuthCredentials.handle_unauthorized")
def test_repeated_401_after_refresh_raises_without_looping(
    app: OAuthApp, mock_api: respx.MockRouter
) -> None:
    refresh = mock_api.post(REFRESH_URL).mock(
        return_value=json_response("oauth_refresh.json")
    )
    agreement = mock_api.get(AGREEMENT_URL).mock(
        return_value=httpx.Response(401, json={"code": "INVALID_ACCESS_TOKEN"})
    )

    with (
        AdobeSignClient(OAuthCredentials.from_tokens(app, tokens())) as client,
        pytest.raises(AuthenticationError),
    ):
        client.agreements.get(AGREEMENT_ID)

    assert refresh.call_count == 1
    assert agreement.call_count == 2


@pytest.mark.covers("OAuthCredentials.handle_unauthorized")
def test_401_without_refresh_token_raises_immediately(
    app: OAuthApp, mock_api: respx.MockRouter
) -> None:
    refresh = mock_api.post(REFRESH_URL)
    mock_api.get(AGREEMENT_URL).mock(
        return_value=httpx.Response(401, json={"code": "INVALID_ACCESS_TOKEN"})
    )
    credentials = OAuthCredentials.from_tokens(app, tokens(refresh=None))

    with AdobeSignClient(credentials) as client, pytest.raises(AuthenticationError):
        client.agreements.get(AGREEMENT_ID)

    assert not refresh.called


@pytest.mark.covers("OAuthCredentials.refresh")
def test_failed_refresh_surfaces_oauth_error(
    app: OAuthApp, mock_api: respx.MockRouter
) -> None:
    mock_api.post(REFRESH_URL).mock(
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "revoked"}
        )
    )
    credentials = OAuthCredentials.from_tokens(
        app, tokens(expires_at=NOW - timedelta(days=365))
    )

    with pytest.raises(OAuthError) as caught:
        credentials.authorization_header()

    assert caught.value.error == "invalid_grant"

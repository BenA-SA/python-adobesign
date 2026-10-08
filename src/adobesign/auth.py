"""Credentials: static integration keys and the OAuth 2.0 authorisation-code flow.

Acrobat Sign OAuth (v2 endpoints) works like this:

1. Send the user to :meth:`OAuthApp.authorization_url`.
2. Acrobat Sign redirects back with ``code``, ``state`` and the account's
   ``api_access_point``; :meth:`OAuthApp.parse_redirect` checks the state and
   extracts them.
3. :meth:`OAuthApp.exchange_code` POSTs the code to
   ``{api_access_point}oauth/v2/token`` for an access + refresh token.
4. Access tokens last about an hour. :meth:`OAuthApp.refresh` POSTs to
   ``{api_access_point}oauth/v2/refresh``; the response carries a new access
   token but not a new refresh token, so the existing one is kept.

:class:`OAuthCredentials` refreshes automatically (shortly before expiry, and
once after an unexpected 401) and hands every new :class:`TokenSet` to a
:class:`TokenStore` so the application can persist it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Protocol
from urllib.parse import parse_qs
from urllib.parse import urlencode
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from adobesign import errors
from adobesign._endpoint import api_endpoint
from adobesign._transport import RetryPolicy
from adobesign._transport import Sleep
from adobesign._transport import Transport
from adobesign._transport import parse_model

DEFAULT_AUTHORIZE_URL = "https://secure.adobesign.com/public/oauth/v2"
DEFAULT_TOKEN_LEEWAY = timedelta(seconds=60)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TokenSet(BaseModel):
    """An OAuth access token plus what is needed to renew and use it.

    Serialise with ``model_dump_json()`` and restore with
    ``TokenSet.model_validate_json()`` to persist between processes. Tokens
    are excluded from ``repr`` so they do not leak into logs.
    """

    model_config = ConfigDict(frozen=True)

    access_token: str = Field(repr=False)
    refresh_token: str | None = Field(default=None, repr=False)
    token_type: str = "Bearer"
    expires_at: datetime | None = None
    api_access_point: str | None = None
    web_access_point: str | None = None

    def is_expired(
        self,
        *,
        leeway: timedelta = DEFAULT_TOKEN_LEEWAY,
        now: datetime | None = None,
    ) -> bool:
        """True when the access token expires within ``leeway`` of ``now``."""
        if self.expires_at is None:
            return False
        return self.expires_at - leeway <= (now or _utcnow())


class _TokenResponse(BaseModel):
    """The token / refresh endpoint body (snake_case on the wire)."""

    model_config = ConfigDict(extra="allow")

    access_token: str
    token_type: str = "Bearer"
    expires_in: int | None = None
    refresh_token: str | None = None
    api_access_point: str | None = None
    web_access_point: str | None = None

    def to_token_set(self, previous: TokenSet | None, issued_at: datetime) -> TokenSet:
        expires_at = (
            issued_at + timedelta(seconds=self.expires_in)
            if self.expires_in is not None
            else None
        )
        return TokenSet(
            access_token=self.access_token,
            refresh_token=self.refresh_token
            or (previous.refresh_token if previous else None),
            token_type=self.token_type,
            expires_at=expires_at,
            api_access_point=self.api_access_point
            or (previous.api_access_point if previous else None),
            web_access_point=self.web_access_point
            or (previous.web_access_point if previous else None),
        )


@dataclass(frozen=True)
class AuthorizationRedirect:
    """The values Acrobat Sign appends to the OAuth ``redirect_uri``."""

    code: str
    api_access_point: str | None
    web_access_point: str | None


def _ensure_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else f"{url}/"


class OAuthApp:
    """An Acrobat Sign API application registered for OAuth.

    ``client_id`` / ``client_secret`` / ``redirect_uri`` come from the API
    application's OAuth configuration in Acrobat Sign.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        *,
        authorize_url: str = DEFAULT_AUTHORIZE_URL,
        http_client: httpx.Client | None = None,
        retry: RetryPolicy | None = None,
        sleep: Sleep = time.sleep,
        timeout: float = 30.0,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.client_id = client_id
        self._client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.authorize_url = authorize_url
        self._owns_http = http_client is None
        self._transport = Transport(
            http_client or httpx.Client(timeout=timeout),
            retry=retry or RetryPolicy(),
            sleep=sleep,
        )
        self._clock = clock

    def __repr__(self) -> str:
        return f"OAuthApp(client_id={self.client_id!r})"

    def close(self) -> None:
        """Close the HTTP client if this object created it."""
        if self._owns_http:
            self._transport.http.close()

    def authorization_url(self, scopes: Sequence[str], *, state: str) -> str:
        """The URL to send the user to so they can grant ``scopes``.

        Scopes use Acrobat Sign's ``name:modifier`` form, for example
        ``["user_login:self", "agreement_read:account",
        "agreement_write:account", "agreement_send:account",
        "webhook_read:account", "webhook_write:account"]``.
        ``state`` should be an unguessable per-session value.
        """
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": " ".join(scopes),
                "state": state,
            }
        )
        return f"{self.authorize_url}?{query}"

    def parse_redirect(self, url: str, *, expected_state: str) -> AuthorizationRedirect:
        """Validate the redirect Acrobat Sign sent back and extract the code.

        Raises :class:`~adobesign.errors.OAuthError` if the user denied
        access, and :class:`~adobesign.errors.OAuthStateMismatchError` if the
        ``state`` is not ``expected_state``.
        """
        query = {
            key: values[0] for key, values in parse_qs(urlsplit(url).query).items()
        }
        if "error" in query:
            raise errors.OAuthError(
                error=query["error"],
                error_description=query.get("error_description"),
            )
        if query.get("state") != expected_state:
            raise errors.OAuthStateMismatchError()
        if not query.get("code"):
            raise errors.OAuthError(
                error="missing_code",
                error_description="the redirect has no code parameter",
            )
        return AuthorizationRedirect(
            code=query["code"],
            api_access_point=query.get("api_access_point"),
            web_access_point=query.get("web_access_point"),
        )

    @api_endpoint("POST", "/oauth/v2/token")
    def exchange_code(self, code: str, *, api_access_point: str) -> TokenSet:
        """Swap an authorisation code for an access and refresh token."""
        issued_at = self._clock()
        response = self._transport.send(
            "POST",
            f"{_ensure_trailing_slash(api_access_point)}oauth/v2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self.redirect_uri,
            },
        )
        token_response = parse_model(response, _TokenResponse)
        tokens = token_response.to_token_set(None, issued_at)
        if tokens.api_access_point is None:
            tokens = tokens.model_copy(update={"api_access_point": api_access_point})
        return tokens

    @api_endpoint("POST", "/oauth/v2/refresh")
    def refresh(self, tokens: TokenSet) -> TokenSet:
        """Get a new access token, keeping the existing refresh token.

        Raises :class:`~adobesign.errors.MissingRefreshTokenError` when
        ``tokens`` has no refresh token, and a ``ValueError`` when it has no
        ``api_access_point`` to send the refresh to.
        """
        if not tokens.refresh_token:
            raise errors.MissingRefreshTokenError()
        if not tokens.api_access_point:
            raise ValueError("TokenSet.api_access_point is required to refresh")
        issued_at = self._clock()
        response = self._transport.send(
            "POST",
            f"{_ensure_trailing_slash(tokens.api_access_point)}oauth/v2/refresh",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": self.client_id,
                "client_secret": self._client_secret,
            },
        )
        return parse_model(response, _TokenResponse).to_token_set(tokens, issued_at)


class TokenStore(Protocol):
    """Where :class:`OAuthCredentials` reads and persists tokens."""

    def load(self) -> TokenSet:
        """Return the current tokens."""
        ...

    def save(self, tokens: TokenSet) -> None:
        """Persist freshly refreshed tokens."""
        ...


class InMemoryTokenStore:
    """Keeps tokens in memory, optionally calling back on every save."""

    def __init__(
        self,
        tokens: TokenSet,
        on_save: Callable[[TokenSet], None] | None = None,
    ) -> None:
        self._tokens = tokens
        self._on_save = on_save

    def load(self) -> TokenSet:
        return self._tokens

    def save(self, tokens: TokenSet) -> None:
        self._tokens = tokens
        if self._on_save is not None:
            self._on_save(tokens)


class IntegrationKey:
    """A static integration key, sent as a bearer token.

    Integration keys never expire on their own, so a 401 is not retried.
    """

    def __init__(self, key: str) -> None:
        self._key = key

    def __repr__(self) -> str:
        return "IntegrationKey(<redacted>)"

    @property
    def api_access_point(self) -> str | None:
        """Integration keys carry no access point; it must be discovered."""
        return None

    def authorization_header(self) -> str:
        return f"Bearer {self._key}"

    def handle_unauthorized(self) -> bool:
        return False


class OAuthCredentials:
    """OAuth tokens that renew themselves through an :class:`OAuthApp`."""

    def __init__(
        self,
        app: OAuthApp,
        store: TokenStore,
        *,
        leeway: timedelta = DEFAULT_TOKEN_LEEWAY,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.app = app
        self.store = store
        self._leeway = leeway
        self._clock = clock

    @classmethod
    def from_tokens(
        cls,
        app: OAuthApp,
        tokens: TokenSet,
        *,
        on_refresh: Callable[[TokenSet], None] | None = None,
    ) -> OAuthCredentials:
        """Wrap ``tokens`` in an in-memory store that calls ``on_refresh``."""
        return cls(app, InMemoryTokenStore(tokens, on_save=on_refresh))

    def __repr__(self) -> str:
        return f"OAuthCredentials(app={self.app!r})"

    @property
    def api_access_point(self) -> str | None:
        """The access point recorded when the tokens were issued."""
        return self.store.load().api_access_point

    def authorization_header(self) -> str:
        tokens = self.store.load()
        if tokens.is_expired(leeway=self._leeway, now=self._clock()):
            tokens = self.refresh()
        return f"Bearer {tokens.access_token}"

    def handle_unauthorized(self) -> bool:
        """Refresh once after a 401 if a refresh token is available."""
        if not self.store.load().refresh_token:
            return False
        self.refresh()
        return True

    def refresh(self) -> TokenSet:
        """Refresh now and persist the result through the store."""
        tokens = self.app.refresh(self.store.load())
        self.store.save(tokens)
        return tokens

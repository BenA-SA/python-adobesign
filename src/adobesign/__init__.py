"""Unofficial Python client for the Adobe Acrobat Sign REST API v6.

Not an official Adobe SDK.

This is a community-maintained client built on Adobe's publicly documented
Acrobat Sign REST API (v6). It is not affiliated with, endorsed by or
supported by Adobe. Adobe and Acrobat Sign are trademarks of Adobe Inc.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

from adobesign._transport import RetryPolicy
from adobesign.auth import AuthorizationRedirect
from adobesign.auth import InMemoryTokenStore
from adobesign.auth import IntegrationKey
from adobesign.auth import OAuthApp
from adobesign.auth import OAuthCredentials
from adobesign.auth import TokenSet
from adobesign.auth import TokenStore
from adobesign.client import AdobeSignClient
from adobesign.enums import AgreementCreateState
from adobesign.enums import AgreementEventType
from adobesign.enums import AgreementStatus
from adobesign.enums import ParticipantRole
from adobesign.enums import ParticipantSetStatus
from adobesign.enums import SignatureType
from adobesign.enums import WebhookEvent
from adobesign.enums import WebhookResourceType
from adobesign.enums import WebhookScope
from adobesign.enums import WebhookState
from adobesign.errors import AdobeSignError
from adobesign.errors import ApiError
from adobesign.errors import AuthenticationError
from adobesign.errors import BadRequestError
from adobesign.errors import ConflictError
from adobesign.errors import MissingRefreshTokenError
from adobesign.errors import NotFoundError
from adobesign.errors import OAuthError
from adobesign.errors import OAuthStateMismatchError
from adobesign.errors import PermissionDeniedError
from adobesign.errors import RateLimitedError
from adobesign.errors import ResponseParseError
from adobesign.errors import ServerError
from adobesign.errors import TransportError
from adobesign.errors import WebhookClientIdError
from adobesign.errors import WebhookPayloadError
from adobesign.models import AgreementCreate
from adobesign.models import CcInfo
from adobesign.models import ExternalId
from adobesign.models import FileInfo
from adobesign.models import MemberInfo
from adobesign.models import ParticipantSetInfo
from adobesign.models import WebhookCreate
from adobesign.models import WebhookUrlInfo
from adobesign.notifications import WebhookHandshake
from adobesign.notifications import parse_notification
from adobesign.notifications import verify_webhook_request

try:
    __version__ = version("python-adobesign")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = [
    "AdobeSignClient",
    "AdobeSignError",
    "AgreementCreate",
    "AgreementCreateState",
    "AgreementEventType",
    "AgreementStatus",
    "ApiError",
    "AuthenticationError",
    "AuthorizationRedirect",
    "BadRequestError",
    "CcInfo",
    "ConflictError",
    "ExternalId",
    "FileInfo",
    "InMemoryTokenStore",
    "IntegrationKey",
    "MemberInfo",
    "MissingRefreshTokenError",
    "NotFoundError",
    "OAuthApp",
    "OAuthCredentials",
    "OAuthError",
    "OAuthStateMismatchError",
    "ParticipantRole",
    "ParticipantSetInfo",
    "ParticipantSetStatus",
    "PermissionDeniedError",
    "RateLimitedError",
    "ResponseParseError",
    "RetryPolicy",
    "ServerError",
    "SignatureType",
    "TokenSet",
    "TokenStore",
    "TransportError",
    "WebhookClientIdError",
    "WebhookCreate",
    "WebhookEvent",
    "WebhookHandshake",
    "WebhookPayloadError",
    "WebhookResourceType",
    "WebhookScope",
    "WebhookState",
    "WebhookUrlInfo",
    "__version__",
    "parse_notification",
    "verify_webhook_request",
]

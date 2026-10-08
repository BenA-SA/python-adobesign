"""Registry of every HTTP-calling public method, for the shared error matrix.

Each :class:`EndpointCase` says how to call one method against the mocked
API and which route it hits. ``test_error_matrix.py`` runs every case through
the same failure scenarios (401/403/404, 429 with ``Retry-After``, 5xx retry
and exhaustion, malformed and wrong-shape JSON, unexpected fields), and
``test_coverage_guard.py`` fails if a method marked with ``@api_endpoint`` in
the package is missing from :data:`ENDPOINT_CASES`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from adobesign import AdobeSignClient
from adobesign import AgreementCreate
from adobesign import FileInfo
from adobesign import OAuthApp
from adobesign import ParticipantSetInfo
from adobesign import TokenSet
from adobesign import WebhookCreate
from adobesign import WebhookEvent
from adobesign import WebhookScope
from adobesign import WebhookUrlInfo

from helpers import ACCESS_POINT
from helpers import API
from helpers import FIXTURES
from helpers import load_fixture

AGREEMENT_ID = "CBJCHBCAABAAlmUFfQ7Hm3kXH1v6Dk3B6dQ0RjK9tY2a"
WEBHOOK_ID = "CBJCHBCAABAAwh7Tz2Lq9Xv4nB1mK8cJ5dF0sG3hR6pY"
PDF_BYTES = (FIXTURES / "sample.pdf").read_bytes()
REFRESHABLE_TOKENS = TokenSet(
    access_token="old-access",
    refresh_token="refresh-me",
    api_access_point=ACCESS_POINT,
)


@dataclass(frozen=True)
class Env:
    """The objects a case may call."""

    client: AdobeSignClient
    oauth_app: OAuthApp


@dataclass(frozen=True)
class EndpointCase:
    """How to exercise one HTTP-calling method in the error matrix.

    ``success`` builds a fresh 2xx response; ``returns_json`` is false for
    endpoints whose success body is a PDF or empty (they skip the
    malformed-JSON scenarios). ``idempotent`` mirrors the HTTP method: only
    idempotent requests are retried on 5xx by default.
    """

    target: str
    method: str
    url: str
    call: Callable[[Env], Any]
    success: Callable[[], httpx.Response]
    returns_json: bool = True

    @property
    def idempotent(self) -> bool:
        return self.method in {"GET", "PUT", "DELETE"}


def _json(name: str) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(200, json=load_fixture(name))


def _pdf() -> httpx.Response:
    return httpx.Response(
        200, content=PDF_BYTES, headers={"Content-Type": "application/pdf"}
    )


def _no_content() -> httpx.Response:
    return httpx.Response(204)


def sample_agreement() -> AgreementCreate:
    return AgreementCreate(
        name="Consultancy agreement — Example Ltd",
        file_infos=[FileInfo(transient_document_id="3AAABLblqZhCtransient")],
        participant_sets_info=[
            ParticipantSetInfo.single("alice@example.com", order=1, name="Alice"),
        ],
    )


def sample_webhook() -> WebhookCreate:
    return WebhookCreate(
        name="Agreement events",
        scope=WebhookScope.ACCOUNT,
        webhook_subscription_events=[WebhookEvent.AGREEMENT_WORKFLOW_COMPLETED],
        webhook_url_info=WebhookUrlInfo(url="https://hooks.example.com/adobesign"),
    )


ENDPOINT_CASES: list[EndpointCase] = [
    EndpointCase(
        target="OAuthApp.exchange_code",
        method="POST",
        url=f"{ACCESS_POINT}oauth/v2/token",
        call=lambda env: env.oauth_app.exchange_code(
            "auth-code", api_access_point=ACCESS_POINT
        ),
        success=_json("oauth_token.json"),
    ),
    EndpointCase(
        target="OAuthApp.refresh",
        method="POST",
        url=f"{ACCESS_POINT}oauth/v2/refresh",
        call=lambda env: env.oauth_app.refresh(REFRESHABLE_TOKENS),
        success=_json("oauth_refresh.json"),
    ),
    EndpointCase(
        target="AdobeSignClient.discover_base_uris",
        method="GET",
        url="https://api.adobesign.com/api/rest/v6/baseUris",
        call=lambda env: env.client.discover_base_uris(),
        success=_json("base_uris.json"),
    ),
    EndpointCase(
        target="TransientDocumentsResource.upload",
        method="POST",
        url=f"{API}/transientDocuments",
        call=lambda env: env.client.transient_documents.upload(PDF_BYTES),
        success=_json("transient_document.json"),
    ),
    EndpointCase(
        target="AgreementsResource.create",
        method="POST",
        url=f"{API}/agreements",
        call=lambda env: env.client.agreements.create(sample_agreement()),
        success=_json("agreement_created.json"),
    ),
    EndpointCase(
        target="AgreementsResource.get",
        method="GET",
        url=f"{API}/agreements/{AGREEMENT_ID}",
        call=lambda env: env.client.agreements.get(AGREEMENT_ID),
        success=_json("agreement.json"),
    ),
    EndpointCase(
        target="AgreementsResource.list",
        method="GET",
        url=f"{API}/agreements",
        call=lambda env: list(env.client.agreements.list()),
        success=_json("agreements_page_2.json"),
    ),
    EndpointCase(
        target="AgreementsResource.get_members",
        method="GET",
        url=f"{API}/agreements/{AGREEMENT_ID}/members",
        call=lambda env: env.client.agreements.get_members(AGREEMENT_ID),
        success=_json("agreement_members.json"),
    ),
    EndpointCase(
        target="AgreementsResource.get_events",
        method="GET",
        url=f"{API}/agreements/{AGREEMENT_ID}/events",
        call=lambda env: env.client.agreements.get_events(AGREEMENT_ID),
        success=_json("agreement_events.json"),
    ),
    EndpointCase(
        target="AgreementsResource.cancel",
        method="PUT",
        url=f"{API}/agreements/{AGREEMENT_ID}/state",
        call=lambda env: env.client.agreements.cancel(AGREEMENT_ID),
        success=_no_content,
        returns_json=False,
    ),
    EndpointCase(
        target="AgreementsResource.send_reminder",
        method="POST",
        url=f"{API}/agreements/{AGREEMENT_ID}/reminders",
        call=lambda env: env.client.agreements.send_reminder(AGREEMENT_ID, ["p1"]),
        success=_json("reminder_created.json"),
    ),
    EndpointCase(
        target="AgreementsResource.download_combined_document",
        method="GET",
        url=f"{API}/agreements/{AGREEMENT_ID}/combinedDocument",
        call=lambda env: env.client.agreements.download_combined_document(AGREEMENT_ID),
        success=_pdf,
        returns_json=False,
    ),
    EndpointCase(
        target="AgreementsResource.download_audit_trail",
        method="GET",
        url=f"{API}/agreements/{AGREEMENT_ID}/auditTrail",
        call=lambda env: env.client.agreements.download_audit_trail(AGREEMENT_ID),
        success=_pdf,
        returns_json=False,
    ),
    EndpointCase(
        target="AgreementsResource.get_signing_urls",
        method="GET",
        url=f"{API}/agreements/{AGREEMENT_ID}/signingUrls",
        call=lambda env: env.client.agreements.get_signing_urls(AGREEMENT_ID),
        success=_json("signing_urls.json"),
    ),
    EndpointCase(
        target="WebhooksResource.create",
        method="POST",
        url=f"{API}/webhooks",
        call=lambda env: env.client.webhooks.create(sample_webhook()),
        success=_json("webhook_created.json"),
    ),
    EndpointCase(
        target="WebhooksResource.list",
        method="GET",
        url=f"{API}/webhooks",
        call=lambda env: list(env.client.webhooks.list()),
        success=_json("webhooks_page_2.json"),
    ),
    EndpointCase(
        target="WebhooksResource.delete",
        method="DELETE",
        url=f"{API}/webhooks/{WEBHOOK_ID}",
        call=lambda env: env.client.webhooks.delete(WEBHOOK_ID),
        success=_no_content,
        returns_json=False,
    ),
]

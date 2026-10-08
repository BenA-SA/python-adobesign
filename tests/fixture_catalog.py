"""Which model each JSON fixture represents, and the v6 doc section it mirrors.

References are to the Acrobat Sign REST API v6 reference
(https://secure.adobesign.com/public/docs/restapi/v6) and the developer guide
(https://opensource.adobe.com/acrobat-sign/). The shapes were written from the
documentation without a live account, so the contract test pins them to the
models; see the README's "Unverified against a live account" list.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from adobesign.auth import _TokenResponse
from adobesign.models import Agreement
from adobesign.models import AgreementCreated
from adobesign.models import AgreementEvents
from adobesign.models import AgreementMembers
from adobesign.models import BaseUris
from adobesign.models import ReminderCreated
from adobesign.models import SigningUrls
from adobesign.models import TransientDocument
from adobesign.models import UserAgreements
from adobesign.models import UserWebhooks
from adobesign.models import WebhookCreated
from adobesign.models import WebhookNotification


@dataclass(frozen=True)
class FixtureSpec:
    model: type[BaseModel]
    doc_section: str


FIXTURE_CATALOG: dict[str, FixtureSpec] = {
    "base_uris.json": FixtureSpec(BaseUris, "base_uris: GET /baseUris → BaseUriInfo"),
    "oauth_token.json": FixtureSpec(
        _TokenResponse, "Developer guide, OAuth: POST /oauth/v2/token response"
    ),
    "oauth_refresh.json": FixtureSpec(
        _TokenResponse, "Developer guide, OAuth: POST /oauth/v2/refresh response"
    ),
    "transient_document.json": FixtureSpec(
        TransientDocument,
        "transientDocuments: POST /transientDocuments → TransientDocumentResponse",
    ),
    "agreement_created.json": FixtureSpec(
        AgreementCreated, "agreements: POST /agreements → AgreementCreationResponse"
    ),
    "agreement.json": FixtureSpec(
        Agreement, "agreements: GET /agreements/{agreementId} → AgreementInfo"
    ),
    "agreements_page_1.json": FixtureSpec(
        UserAgreements, "agreements: GET /agreements → UserAgreements (with cursor)"
    ),
    "agreements_page_2.json": FixtureSpec(
        UserAgreements, "agreements: GET /agreements → UserAgreements (last page)"
    ),
    "agreement_members.json": FixtureSpec(
        AgreementMembers,
        "agreements: GET /agreements/{agreementId}/members → MembersInfo",
    ),
    "agreement_events.json": FixtureSpec(
        AgreementEvents,
        "agreements: GET /agreements/{agreementId}/events → AgreementEvents",
    ),
    "reminder_created.json": FixtureSpec(
        ReminderCreated,
        "agreements: POST /agreements/{agreementId}/reminders → ReminderCreationResult",
    ),
    "signing_urls.json": FixtureSpec(
        SigningUrls,
        "agreements: GET /agreements/{agreementId}/signingUrls → SigningUrlResponse",
    ),
    "webhook_created.json": FixtureSpec(
        WebhookCreated, "webhooks: POST /webhooks → WebhookCreationResponse"
    ),
    "webhooks_page_1.json": FixtureSpec(
        UserWebhooks, "webhooks: GET /webhooks → UserWebhooks (with cursor)"
    ),
    "webhooks_page_2.json": FixtureSpec(
        UserWebhooks, "webhooks: GET /webhooks → UserWebhooks (last page)"
    ),
    "webhook_notification.json": FixtureSpec(
        WebhookNotification,
        "Developer guide, Webhooks: agreement event notification payload",
    ),
}

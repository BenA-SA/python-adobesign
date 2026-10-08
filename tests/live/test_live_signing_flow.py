"""End-to-end signing flow against a real Acrobat Sign (developer) account.

Skipped unless credentials are in the environment. Never run against a
production account: it sends a real agreement to ``ADOBESIGN_TEST_SIGNER_EMAIL``
and then cancels it.

Credentials (one of):

* ``ADOBESIGN_INTEGRATION_KEY``, or
* ``ADOBESIGN_CLIENT_ID`` + ``ADOBESIGN_CLIENT_SECRET`` +
  ``ADOBESIGN_REFRESH_TOKEN`` + ``ADOBESIGN_API_ACCESS_POINT``
  (also exercises ``OAuthApp.refresh``).

Also required: ``ADOBESIGN_TEST_SIGNER_EMAIL`` (a mailbox you control, not the
account's own address). Optional: ``ADOBESIGN_TEST_WEBHOOK_URL`` (a public
HTTPS endpoint that echoes ``X-AdobeSign-ClientId``) to exercise webhook
create/delete, and ``ADOBESIGN_DISCOVERY_URL`` to override ``/baseUris``.

The tests run in file order and share one agreement; the last step cancels it
(and the fixture teardown cancels it again, best-effort, if a step failed).
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from collections.abc import Iterator
from itertools import islice
from pathlib import Path
from typing import TypeVar

import pytest

from adobesign import AdobeSignClient
from adobesign import AdobeSignError
from adobesign import AgreementCreate
from adobesign import AgreementStatus
from adobesign import FileInfo
from adobesign import IntegrationKey
from adobesign import NotFoundError
from adobesign import OAuthApp
from adobesign import OAuthCredentials
from adobesign import ParticipantSetInfo
from adobesign import TokenSet
from adobesign import WebhookCreate
from adobesign import WebhookEvent
from adobesign import WebhookResourceType
from adobesign import WebhookScope
from adobesign import WebhookUrlInfo
from adobesign.client import DEFAULT_DISCOVERY_URL
from adobesign.models import AgreementCreated

ResultT = TypeVar("ResultT")

SAMPLE_PDF = Path(__file__).parent.parent / "fixtures" / "sample.pdf"
SIGNER_EMAIL = os.environ.get("ADOBESIGN_TEST_SIGNER_EMAIL")
HAS_INTEGRATION_KEY = bool(os.environ.get("ADOBESIGN_INTEGRATION_KEY"))
OAUTH_VARS = (
    "ADOBESIGN_CLIENT_ID",
    "ADOBESIGN_CLIENT_SECRET",
    "ADOBESIGN_REFRESH_TOKEN",
    "ADOBESIGN_API_ACCESS_POINT",
)
HAS_OAUTH = all(os.environ.get(name) for name in OAUTH_VARS)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (HAS_INTEGRATION_KEY or HAS_OAUTH) or not SIGNER_EMAIL,
        reason="live Acrobat Sign credentials / ADOBESIGN_TEST_SIGNER_EMAIL not set",
    ),
]


def eventually(
    action: Callable[[], ResultT],
    *,
    timeout: float = 90.0,
    interval: float = 3.0,
    retry_on: tuple[type[Exception], ...] = (NotFoundError,),
) -> ResultT:
    """Retry ``action`` until it stops raising ``retry_on`` or time runs out.

    Agreements are processed asynchronously after creation, so some reads
    404 for a few seconds.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return action()
        except retry_on:
            if time.monotonic() > deadline:
                raise
            time.sleep(interval)


def _oauth_app() -> OAuthApp:
    return OAuthApp(
        os.environ["ADOBESIGN_CLIENT_ID"],
        os.environ["ADOBESIGN_CLIENT_SECRET"],
        redirect_uri=os.environ.get(
            "ADOBESIGN_REDIRECT_URI", "https://localhost/callback"
        ),
    )


def _oauth_tokens() -> TokenSet:
    return TokenSet(
        access_token="expired",
        refresh_token=os.environ["ADOBESIGN_REFRESH_TOKEN"],
        expires_at=None,
        api_access_point=os.environ["ADOBESIGN_API_ACCESS_POINT"],
    )


@pytest.fixture(scope="module")
def live_client() -> Iterator[AdobeSignClient]:
    discovery_url = os.environ.get("ADOBESIGN_DISCOVERY_URL", DEFAULT_DISCOVERY_URL)
    if HAS_INTEGRATION_KEY:
        credentials: IntegrationKey | OAuthCredentials = IntegrationKey(
            os.environ["ADOBESIGN_INTEGRATION_KEY"]
        )
    else:
        app = _oauth_app()
        credentials = OAuthCredentials.from_tokens(app, app.refresh(_oauth_tokens()))
    with AdobeSignClient(credentials, discovery_url=discovery_url) as client:
        yield client


@pytest.fixture(scope="module")
def agreement(live_client: AdobeSignClient) -> Iterator[AgreementCreated]:
    transient = live_client.transient_documents.upload(SAMPLE_PDF)
    assert SIGNER_EMAIL is not None
    created = live_client.agreements.create(
        AgreementCreate(
            name="python-adobesign live test (safe to ignore)",
            file_infos=[
                FileInfo(transient_document_id=transient.transient_document_id)
            ],
            participant_sets_info=[ParticipantSetInfo.single(SIGNER_EMAIL, order=1)],
            message="Automated test from python-adobesign; it will be cancelled.",
        )
    )
    yield created
    with contextlib.suppress(AdobeSignError):
        live_client.agreements.cancel(created.id, comment="live test teardown")


@pytest.mark.covers("OAuthApp.refresh")
@pytest.mark.skipif(not HAS_OAUTH, reason="OAuth credentials not set")
def test_refresh_token_yields_access_token() -> None:
    app = _oauth_app()
    try:
        tokens = app.refresh(_oauth_tokens())
    finally:
        app.close()
    assert tokens.access_token
    assert tokens.refresh_token == os.environ["ADOBESIGN_REFRESH_TOKEN"]


@pytest.mark.covers("AdobeSignClient.discover_base_uris")
def test_discover_base_uris(live_client: AdobeSignClient) -> None:
    base_uris = live_client.discover_base_uris()

    assert base_uris.api_access_point.startswith("https://")


@pytest.mark.covers("TransientDocumentsResource.upload")
def test_upload_transient_document(live_client: AdobeSignClient) -> None:
    assert live_client.transient_documents.upload(SAMPLE_PDF).transient_document_id


@pytest.mark.covers("AgreementsResource.create")
@pytest.mark.covers("AgreementsResource.get")
def test_agreement_is_out_for_signature(
    live_client: AdobeSignClient, agreement: AgreementCreated
) -> None:
    def processed_status() -> AgreementStatus | str:
        status = live_client.agreements.get(agreement.id).status
        if status == AgreementStatus.DOCUMENTS_NOT_YET_PROCESSED:
            raise NotFoundError(status_code=404, code="NOT_YET_PROCESSED")
        return status

    assert eventually(processed_status) == AgreementStatus.OUT_FOR_SIGNATURE


@pytest.mark.covers("AgreementsResource.get_members")
def test_members_list_the_signer(
    live_client: AdobeSignClient, agreement: AgreementCreated
) -> None:
    members = eventually(lambda: live_client.agreements.get_members(agreement.id))

    emails = [m.email for s in members.participant_sets for m in s.member_infos]
    assert SIGNER_EMAIL in emails


@pytest.mark.covers("AgreementsResource.get_events")
def test_events_include_creation(
    live_client: AdobeSignClient, agreement: AgreementCreated
) -> None:
    events = eventually(lambda: live_client.agreements.get_events(agreement.id))

    assert any(event.type == "CREATED" for event in events)


@pytest.mark.covers("AgreementsResource.list")
def test_list_contains_agreement(
    live_client: AdobeSignClient, agreement: AgreementCreated
) -> None:
    recent = islice(live_client.agreements.list(page_size=50), 500)

    assert agreement.id in {row.id for row in recent}


@pytest.mark.covers("AgreementsResource.get_signing_urls")
def test_signing_urls(
    live_client: AdobeSignClient, agreement: AgreementCreated
) -> None:
    urls = eventually(lambda: live_client.agreements.get_signing_urls(agreement.id))

    assert urls.signing_url_set_infos[0].signing_urls[0].esign_url.startswith("https")


@pytest.mark.covers("AgreementsResource.send_reminder")
def test_send_reminder(
    live_client: AdobeSignClient, agreement: AgreementCreated
) -> None:
    members = live_client.agreements.get_members(agreement.id)
    participant_ids = [
        m.id for s in members.participant_sets for m in s.member_infos if m.id
    ]

    reminder = live_client.agreements.send_reminder(
        agreement.id, participant_ids, note="python-adobesign live test"
    )

    assert reminder.id


@pytest.mark.covers("AgreementsResource.download_combined_document")
def test_download_combined_document(
    live_client: AdobeSignClient, agreement: AgreementCreated
) -> None:
    pdf = live_client.agreements.download_combined_document(agreement.id)

    assert pdf.startswith(b"%PDF")


@pytest.mark.covers("AgreementsResource.download_audit_trail")
def test_download_audit_trail(
    live_client: AdobeSignClient, agreement: AgreementCreated
) -> None:
    pdf = live_client.agreements.download_audit_trail(agreement.id)

    assert pdf.startswith(b"%PDF")


@pytest.mark.covers("WebhooksResource.list")
def test_list_webhooks(live_client: AdobeSignClient) -> None:
    assert all(hook.id for hook in islice(live_client.webhooks.list(), 100))


@pytest.mark.covers("WebhooksResource.create")
@pytest.mark.covers("WebhooksResource.delete")
@pytest.mark.skipif(
    not os.environ.get("ADOBESIGN_TEST_WEBHOOK_URL"),
    reason="ADOBESIGN_TEST_WEBHOOK_URL not set",
)
def test_create_and_delete_webhook(
    live_client: AdobeSignClient, agreement: AgreementCreated
) -> None:
    created = live_client.webhooks.create(
        WebhookCreate(
            name="python-adobesign live test",
            scope=WebhookScope.RESOURCE,
            resource_type=WebhookResourceType.AGREEMENT,
            resource_id=agreement.id,
            webhook_subscription_events=[WebhookEvent.AGREEMENT_ALL],
            webhook_url_info=WebhookUrlInfo(
                url=os.environ["ADOBESIGN_TEST_WEBHOOK_URL"]
            ),
        )
    )

    live_client.webhooks.delete(created.id)


@pytest.mark.covers("AgreementsResource.cancel")
def test_cancel_agreement(
    live_client: AdobeSignClient, agreement: AgreementCreated
) -> None:
    live_client.agreements.cancel(agreement.id, comment="python-adobesign live test")

    assert live_client.agreements.get(agreement.id).status == AgreementStatus.CANCELLED

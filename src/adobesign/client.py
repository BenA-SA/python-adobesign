"""The synchronous Acrobat Sign REST API v6 client.

Every Acrobat Sign account lives on a regional shard, so API calls must go to
that account's ``apiAccessPoint`` (for example
``https://api.na1.adobesign.com/``). The client takes it from, in order: the
``api_access_point`` argument, the OAuth tokens, or a ``GET /baseUris``
discovery call made lazily before the first request.
"""

from __future__ import annotations

import builtins
import os
import time
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import IO
from typing import Any
from typing import Protocol
from urllib.parse import quote

import httpx

from adobesign._endpoint import api_endpoint
from adobesign._transport import AuthProvider
from adobesign._transport import RetryPolicy
from adobesign._transport import Sleep
from adobesign._transport import Transport
from adobesign._transport import parse_model
from adobesign.models import Agreement
from adobesign.models import AgreementCancellationInfo
from adobesign.models import AgreementCreate
from adobesign.models import AgreementCreated
from adobesign.models import AgreementEvent
from adobesign.models import AgreementEvents
from adobesign.models import AgreementMembers
from adobesign.models import AgreementStateUpdate
from adobesign.models import BaseUris
from adobesign.models import Page
from adobesign.models import ReminderCreate
from adobesign.models import ReminderCreated
from adobesign.models import SigningUrls
from adobesign.models import TransientDocument
from adobesign.models import UserAgreement
from adobesign.models import UserAgreements
from adobesign.models import UserWebhooks
from adobesign.models import Webhook
from adobesign.models import WebhookCreate
from adobesign.models import WebhookCreated
from adobesign.pagination import Paginator

DEFAULT_DISCOVERY_URL = "https://api.adobesign.com/api/rest/v6/baseUris"
API_PATH = "api/rest/v6"
PDF_MIME_TYPE = "application/pdf"
DEFAULT_FILE_NAME = "document.pdf"

UploadSource = str | os.PathLike[str] | bytes | IO[bytes]


class Credentials(AuthProvider, Protocol):
    """An :class:`~adobesign.auth.IntegrationKey` or ``OAuthCredentials``."""

    @property
    def api_access_point(self) -> str | None:
        """The access point the credential already knows, if any."""
        ...


def _segment(identifier: str) -> str:
    """Percent-encode an id for use as one URL path segment."""
    return quote(identifier, safe="")


class AdobeSignClient:
    """Entry point: ``client.agreements``, ``client.transient_documents``,
    ``client.webhooks``.

    Use as a context manager (or call :meth:`close`) to release the HTTP
    connection pool when the client created it.
    """

    def __init__(
        self,
        credentials: Credentials,
        *,
        api_access_point: str | None = None,
        discovery_url: str = DEFAULT_DISCOVERY_URL,
        retry: RetryPolicy | None = None,
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._credentials = credentials
        self._api_access_point = api_access_point
        self._discovery_url = discovery_url
        self._owns_http = http_client is None
        self._transport = Transport(
            http_client or httpx.Client(timeout=timeout),
            retry=retry or RetryPolicy(),
            sleep=sleep,
        )
        self.agreements = AgreementsResource(self)
        self.transient_documents = TransientDocumentsResource(self)
        self.webhooks = WebhooksResource(self)

    def __enter__(self) -> AdobeSignClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP client if this object created it."""
        if self._owns_http:
            self._transport.http.close()

    @api_endpoint("GET", "/baseUris")
    def discover_base_uris(self) -> BaseUris:
        """Look up the account's access points and use them from now on."""
        response = self._transport.send(
            "GET", self._discovery_url, auth=self._credentials
        )
        base_uris = parse_model(response, BaseUris)
        self._api_access_point = base_uris.api_access_point
        return base_uris

    @property
    def api_base_url(self) -> str:
        """``{apiAccessPoint}api/rest/v6``, discovering the access point if needed."""
        access_point = self._api_access_point or self._credentials.api_access_point
        if access_point is None:
            access_point = self.discover_base_uris().api_access_point
        return f"{access_point.rstrip('/')}/{API_PATH}"

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self._transport.send(
            method, f"{self.api_base_url}{path}", auth=self._credentials, **kwargs
        )


class _Resource:
    def __init__(self, client: AdobeSignClient) -> None:
        self._client = client


class TransientDocumentsResource(_Resource):
    """``/transientDocuments``: short-lived uploads referenced by agreements."""

    @api_endpoint("POST", "/transientDocuments")
    def upload(
        self,
        source: UploadSource,
        *,
        file_name: str | None = None,
        mime_type: str = PDF_MIME_TYPE,
    ) -> TransientDocument:
        """Upload a document and return its ``transientDocumentId``.

        ``source`` may be a filesystem path, raw bytes, or a binary file-like
        object. ``file_name`` defaults to the path's or file object's name,
        else ``document.pdf``. Transient documents expire after 7 days.
        """
        content, inferred_name = _read_upload(source)
        name = file_name or inferred_name
        response = self._client._send(
            "POST",
            "/transientDocuments",
            data={"File-Name": name, "Mime-Type": mime_type},
            files={"File": (name, content, mime_type)},
        )
        return parse_model(response, TransientDocument)


def _read_upload(source: UploadSource) -> tuple[bytes, str]:
    """Return the bytes to upload and a best-guess file name."""
    if isinstance(source, bytes):
        return source, DEFAULT_FILE_NAME
    if isinstance(source, (str, os.PathLike)):
        path = Path(source)
        return path.read_bytes(), path.name
    raw_name = getattr(source, "name", None)
    name = Path(raw_name).name if isinstance(raw_name, str) else DEFAULT_FILE_NAME
    return source.read(), name


class AgreementsResource(_Resource):
    """``/agreements``: send, track, remind, cancel and download agreements."""

    @api_endpoint("POST", "/agreements")
    def create(self, agreement: AgreementCreate) -> AgreementCreated:
        """Create an agreement; with state ``IN_PROCESS`` it is sent at once.

        Not retried on 5xx by default, because a replay could send twice.
        """
        response = self._client._send("POST", "/agreements", json=agreement.to_api())
        return parse_model(response, AgreementCreated)

    @api_endpoint("GET", "/agreements/{agreementId}")
    def get(self, agreement_id: str) -> Agreement:
        """Fetch an agreement's current details and status."""
        response = self._client._send("GET", f"/agreements/{_segment(agreement_id)}")
        return parse_model(response, Agreement)

    @api_endpoint("GET", "/agreements")
    def list(self, *, page_size: int | None = None) -> Paginator[UserAgreement]:
        """Iterate the user's agreements, fetching pages lazily."""

        def fetch(cursor: str | None) -> Page[UserAgreement]:
            response = self._client._send(
                "GET",
                "/agreements",
                params={"cursor": cursor, "pageSize": page_size},
            )
            listing = parse_model(response, UserAgreements)
            return Page(
                items=listing.user_agreement_list,
                next_cursor=listing.page.next_cursor,
            )

        return Paginator(fetch)

    @api_endpoint("GET", "/agreements/{agreementId}/members")
    def get_members(self, agreement_id: str) -> AgreementMembers:
        """Participants with their per-member status and who is next."""
        response = self._client._send(
            "GET", f"/agreements/{_segment(agreement_id)}/members"
        )
        return parse_model(response, AgreementMembers)

    @api_endpoint("GET", "/agreements/{agreementId}/events")
    def get_events(self, agreement_id: str) -> builtins.list[AgreementEvent]:
        """The agreement's audit events, oldest first."""
        response = self._client._send(
            "GET", f"/agreements/{_segment(agreement_id)}/events"
        )
        return parse_model(response, AgreementEvents).events

    @api_endpoint("PUT", "/agreements/{agreementId}/state")
    def cancel(
        self,
        agreement_id: str,
        *,
        comment: str | None = None,
        notify_others: bool = True,
    ) -> None:
        """Cancel (recall) an agreement that is still in progress."""
        update = AgreementStateUpdate(
            state="CANCELLED",
            agreement_cancellation_info=AgreementCancellationInfo(
                comment=comment, notify_others=notify_others
            ),
        )
        self._client._send(
            "PUT",
            f"/agreements/{_segment(agreement_id)}/state",
            json=update.to_api(),
        )

    @api_endpoint("POST", "/agreements/{agreementId}/reminders")
    def send_reminder(
        self,
        agreement_id: str,
        participant_ids: Sequence[str],
        *,
        note: str | None = None,
    ) -> ReminderCreated:
        """Remind the given participants (member ids from :meth:`get_members`)."""
        reminder = ReminderCreate(
            recipient_participant_ids=list(participant_ids), note=note
        )
        response = self._client._send(
            "POST",
            f"/agreements/{_segment(agreement_id)}/reminders",
            json=reminder.to_api(),
        )
        return parse_model(response, ReminderCreated)

    @api_endpoint("GET", "/agreements/{agreementId}/combinedDocument")
    def download_combined_document(
        self,
        agreement_id: str,
        *,
        attach_audit_report: bool = False,
        attach_supporting_documents: bool | None = None,
    ) -> bytes:
        """The agreement's documents (signed, once complete) as one PDF."""
        response = self._client._send(
            "GET",
            f"/agreements/{_segment(agreement_id)}/combinedDocument",
            params={
                "attachAuditReport": attach_audit_report,
                "attachSupportingDocuments": attach_supporting_documents,
            },
            accept=PDF_MIME_TYPE,
        )
        return response.content

    @api_endpoint("GET", "/agreements/{agreementId}/auditTrail")
    def download_audit_trail(self, agreement_id: str) -> bytes:
        """The agreement's audit report as a PDF."""
        response = self._client._send(
            "GET",
            f"/agreements/{_segment(agreement_id)}/auditTrail",
            accept=PDF_MIME_TYPE,
        )
        return response.content

    @api_endpoint("GET", "/agreements/{agreementId}/signingUrls")
    def get_signing_urls(
        self, agreement_id: str, *, frame_parent: str | None = None
    ) -> SigningUrls:
        """Embedded-signing URLs for participants who are due to sign.

        Acrobat Sign answers 404 (``AGREEMENT_NOT_SIGNABLE`` and similar)
        while the agreement is still being processed, so callers may need to
        retry shortly after creating one.
        """
        response = self._client._send(
            "GET",
            f"/agreements/{_segment(agreement_id)}/signingUrls",
            params={"frameParent": frame_parent},
        )
        return parse_model(response, SigningUrls)


class WebhooksResource(_Resource):
    """``/webhooks``: subscribe a URL to agreement events."""

    @api_endpoint("POST", "/webhooks")
    def create(self, webhook: WebhookCreate) -> WebhookCreated:
        """Register a webhook.

        Acrobat Sign first verifies the URL by calling it with an
        ``X-AdobeSign-ClientId`` header that must be echoed back (see
        :func:`adobesign.notifications.verify_webhook_request`).
        """
        response = self._client._send("POST", "/webhooks", json=webhook.to_api())
        return parse_model(response, WebhookCreated)

    @api_endpoint("GET", "/webhooks")
    def list(
        self,
        *,
        page_size: int | None = None,
        show_inactive: bool | None = None,
    ) -> Paginator[Webhook]:
        """Iterate the user's webhooks, fetching pages lazily."""

        def fetch(cursor: str | None) -> Page[Webhook]:
            response = self._client._send(
                "GET",
                "/webhooks",
                params={
                    "cursor": cursor,
                    "pageSize": page_size,
                    "showInActiveWebhooks": show_inactive,
                },
            )
            listing = parse_model(response, UserWebhooks)
            return Page(
                items=listing.user_webhook_list,
                next_cursor=listing.page.next_cursor,
            )

        return Paginator(fetch)

    @api_endpoint("DELETE", "/webhooks/{webhookId}")
    def delete(self, webhook_id: str) -> None:
        """Delete a webhook."""
        self._client._send("DELETE", f"/webhooks/{_segment(webhook_id)}")

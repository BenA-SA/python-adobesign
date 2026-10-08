"""Typed request and response models for the signing-flow endpoints.

Attributes are snake_case; on the wire they use the camelCase names Acrobat
Sign documents (``participantSetsInfo``, ``transientDocumentId`` ...). Models
accept either spelling when constructed.

Runtime models are deliberately tolerant: unknown response fields are kept in
``model_extra`` instead of raising, so a new field added by Adobe never breaks
a running integration. The test suite separately asserts that every fixture
field is modelled, so drift between docs and models is still caught.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from typing import Any
from typing import Generic
from typing import TypeVar

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic.alias_generators import to_camel

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

ItemT = TypeVar("ItemT")
_KNOWN_FIRST = Field(union_mode="left_to_right")

OpenAgreementCreateState = Annotated[AgreementCreateState | str, _KNOWN_FIRST]
OpenAgreementEventType = Annotated[AgreementEventType | str, _KNOWN_FIRST]
OpenAgreementStatus = Annotated[AgreementStatus | str, _KNOWN_FIRST]
OpenParticipantRole = Annotated[ParticipantRole | str, _KNOWN_FIRST]
OpenParticipantSetStatus = Annotated[ParticipantSetStatus | str, _KNOWN_FIRST]
OpenSignatureType = Annotated[SignatureType | str, _KNOWN_FIRST]
OpenWebhookEvent = Annotated[WebhookEvent | str, _KNOWN_FIRST]
OpenWebhookResourceType = Annotated[WebhookResourceType | str, _KNOWN_FIRST]
OpenWebhookScope = Annotated[WebhookScope | str, _KNOWN_FIRST]
OpenWebhookState = Annotated[WebhookState | str, _KNOWN_FIRST]


class AdobeSignModel(BaseModel):
    """Base model: camelCase aliases, snake_case attributes, extra fields kept."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="allow",
    )

    def to_api(self) -> dict[str, Any]:
        """Serialise to the JSON body Acrobat Sign expects (aliases, no nulls)."""
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")


class BaseUris(AdobeSignModel):
    """``GET /baseUris``: the account's API and web access points."""

    api_access_point: str
    web_access_point: str


class TransientDocument(AdobeSignModel):
    """``POST /transientDocuments`` response."""

    transient_document_id: str


class FileInfo(AdobeSignModel):
    """One document attached to an agreement."""

    transient_document_id: str | None = None
    library_document_id: str | None = None
    label: str | None = None


class MemberInfo(AdobeSignModel):
    """A participant in a participant set (request and ``GET`` response)."""

    email: str
    name: str | None = None
    id: str | None = None


class ParticipantSetInfo(AdobeSignModel):
    """A group of participants who act at the same step of the workflow."""

    member_infos: list[MemberInfo]
    order: int
    role: OpenParticipantRole
    name: str | None = None
    label: str | None = None
    private_message: str | None = None
    id: str | None = None

    @classmethod
    def single(
        cls,
        email: str,
        *,
        order: int,
        name: str | None = None,
        role: ParticipantRole = ParticipantRole.SIGNER,
    ) -> ParticipantSetInfo:
        """Build a one-person participant set (the common case)."""
        return cls(
            member_infos=[MemberInfo(email=email, name=name)],
            order=order,
            role=role,
        )


class CcInfo(AdobeSignModel):
    """An address copied on the agreement."""

    email: str
    label: str | None = None


class ExternalId(AdobeSignModel):
    """The caller's own identifier for an agreement."""

    id: str


class AgreementCreate(AdobeSignModel):
    """``POST /agreements`` request body (``AgreementInfo``)."""

    file_infos: list[FileInfo]
    name: str
    participant_sets_info: list[ParticipantSetInfo]
    signature_type: OpenSignatureType = SignatureType.ESIGN
    state: OpenAgreementCreateState = AgreementCreateState.IN_PROCESS
    message: str | None = None
    ccs: list[CcInfo] | None = None
    external_id: ExternalId | None = None
    expiration_time: datetime | None = None
    reminder_frequency: str | None = None
    locale: str | None = None


class AgreementCreated(AdobeSignModel):
    """``POST /agreements`` response."""

    id: str


class Agreement(AdobeSignModel):
    """``GET /agreements/{agreementId}`` response (``AgreementInfo``)."""

    id: str
    name: str
    status: OpenAgreementStatus
    participant_sets_info: list[ParticipantSetInfo] = Field(default_factory=list)
    signature_type: OpenSignatureType | None = None
    created_date: datetime | None = None
    expiration_time: datetime | None = None
    message: str | None = None
    sender_email: str | None = None
    group_id: str | None = None
    workflow_id: str | None = None
    locale: str | None = None
    type: str | None = None
    external_id: ExternalId | None = None
    ccs: list[CcInfo] = Field(default_factory=list)
    document_visibility_enabled: bool | None = None
    first_reminder_delay: int | None = None
    reminder_frequency: str | None = None
    last_event_date: datetime | None = None


class UserAgreement(AdobeSignModel):
    """One row of ``GET /agreements`` (``UserAgreement``)."""

    id: str
    name: str | None = None
    status: OpenAgreementStatus | None = None
    display_date: datetime | None = None
    group_id: str | None = None
    hidden: bool | None = None
    esign: bool | None = None
    type: str | None = None
    parent_id: str | None = None
    latest_version_id: str | None = None


class PageInfo(AdobeSignModel):
    """Cursor-pagination block returned by list endpoints."""

    next_cursor: str | None = None


class UserAgreements(AdobeSignModel):
    """``GET /agreements`` response."""

    user_agreement_list: list[UserAgreement] = Field(default_factory=list)
    page: PageInfo = Field(default_factory=PageInfo)


class ParticipantMember(AdobeSignModel):
    """A member as reported by ``GET /agreements/{id}/members``."""

    id: str | None = None
    email: str
    name: str | None = None
    status: str | None = None
    company_name: str | None = None
    user_id: str | None = None
    created_date: datetime | None = None


class DetailedParticipantSet(AdobeSignModel):
    """A participant set with per-member status (``/members``)."""

    id: str | None = None
    member_infos: list[ParticipantMember] = Field(default_factory=list)
    order: int | None = None
    role: OpenParticipantRole | None = None
    status: OpenParticipantSetStatus | None = None
    name: str | None = None
    private_message: str | None = None


class SenderInfo(AdobeSignModel):
    """The agreement's sender as reported by ``/members``."""

    id: str | None = None
    email: str | None = None
    name: str | None = None
    status: str | None = None
    company_name: str | None = None
    user_id: str | None = None
    created_date: datetime | None = None


class NextParticipantMember(AdobeSignModel):
    """A member the agreement is currently waiting on."""

    email: str
    name: str | None = None
    waiting_since: datetime | None = None


class NextParticipantSet(AdobeSignModel):
    """A participant set the agreement is currently waiting on."""

    member_infos: list[NextParticipantMember] = Field(default_factory=list)
    name: str | None = None


class CcParticipant(AdobeSignModel):
    """A CC participant as reported by ``/members``."""

    email: str
    id: str | None = None
    company_name: str | None = None
    name: str | None = None
    status: str | None = None
    user_id: str | None = None


class AgreementMembers(AdobeSignModel):
    """``GET /agreements/{agreementId}/members`` response."""

    participant_sets: list[DetailedParticipantSet] = Field(default_factory=list)
    sender_info: SenderInfo | None = None
    ccs_info: list[CcParticipant] = Field(default_factory=list)
    next_participant_sets: list[NextParticipantSet] = Field(default_factory=list)


class AgreementEvent(AdobeSignModel):
    """One audit event from ``GET /agreements/{agreementId}/events``."""

    id: str | None = None
    type: OpenAgreementEventType
    date: datetime
    description: str | None = None
    participant_email: str | None = None
    participant_role: OpenParticipantRole | None = None
    acting_user_email: str | None = None
    acting_user_ip_address: str | None = None
    acting_user_name: str | None = None
    initiating_user_email: str | None = None
    initiating_user_name: str | None = None
    comment: str | None = None


class AgreementEvents(AdobeSignModel):
    """``GET /agreements/{agreementId}/events`` response."""

    events: list[AgreementEvent] = Field(default_factory=list)


class AgreementCancellationInfo(AdobeSignModel):
    """Optional detail sent when cancelling an agreement."""

    comment: str | None = None
    notify_others: bool | None = None


class AgreementStateUpdate(AdobeSignModel):
    """``PUT /agreements/{agreementId}/state`` request body."""

    state: str
    agreement_cancellation_info: AgreementCancellationInfo | None = None


class ReminderCreate(AdobeSignModel):
    """``POST /agreements/{agreementId}/reminders`` request (``ReminderInfo``)."""

    recipient_participant_ids: list[str]
    status: str = "ACTIVE"
    note: str | None = None
    first_reminder_delay: int | None = None
    frequency: str | None = None


class ReminderCreated(AdobeSignModel):
    """``POST /agreements/{agreementId}/reminders`` response."""

    id: str


class SigningUrl(AdobeSignModel):
    """One participant's embedded signing URL."""

    email: str
    esign_url: str


class SigningUrlSet(AdobeSignModel):
    """Signing URLs for one participant set."""

    signing_urls: list[SigningUrl] = Field(default_factory=list)
    signing_url_set_name: str | None = None


class SigningUrls(AdobeSignModel):
    """``GET /agreements/{agreementId}/signingUrls`` response."""

    signing_url_set_infos: list[SigningUrlSet] = Field(default_factory=list)


class WebhookUrlInfo(AdobeSignModel):
    """Where Acrobat Sign delivers webhook notifications."""

    url: str


class WebhookCreate(AdobeSignModel):
    """``POST /webhooks`` request body (``WebhookInfo``)."""

    name: str
    scope: OpenWebhookScope
    state: OpenWebhookState = WebhookState.ACTIVE
    webhook_subscription_events: list[OpenWebhookEvent]
    webhook_url_info: WebhookUrlInfo
    resource_type: OpenWebhookResourceType | None = None
    resource_id: str | None = None
    application_display_name: str | None = None
    application_name: str | None = None


class WebhookCreated(AdobeSignModel):
    """``POST /webhooks`` response."""

    id: str


class Webhook(AdobeSignModel):
    """One row of ``GET /webhooks`` (``UserWebhook``)."""

    id: str
    name: str | None = None
    scope: OpenWebhookScope | None = None
    state: OpenWebhookState | None = None
    status: str | None = None
    webhook_subscription_events: list[OpenWebhookEvent] = Field(default_factory=list)
    webhook_url_info: WebhookUrlInfo | None = None
    resource_type: OpenWebhookResourceType | None = None
    resource_id: str | None = None
    application_display_name: str | None = None
    application_name: str | None = None
    last_modified: datetime | None = None


class UserWebhooks(AdobeSignModel):
    """``GET /webhooks`` response."""

    user_webhook_list: list[Webhook] = Field(default_factory=list)
    page: PageInfo = Field(default_factory=PageInfo)


class NotificationApplicableUser(AdobeSignModel):
    """A user a webhook notification applies to."""

    id: str | None = None
    email: str | None = None
    role: str | None = None
    payload_applicable: bool | None = None


class NotificationAgreement(AdobeSignModel):
    """The ``agreement`` object embedded in an agreement webhook notification.

    Which sub-objects are present depends on the webhook's conditional
    parameters (``includeParticipantsInfo`` etc.).
    """

    id: str
    name: str | None = None
    signature_type: OpenSignatureType | None = None
    status: OpenAgreementStatus | None = None
    created_date: datetime | None = None
    expiration_time: datetime | None = None
    external_id: ExternalId | None = None
    locale: str | None = None
    message: str | None = None
    sender_email: str | None = None
    workflow_id: str | None = None
    participant_sets_info: AgreementMembers | None = None


class WebhookNotification(AdobeSignModel):
    """The body Acrobat Sign POSTs to a webhook URL for an agreement event."""

    webhook_id: str
    webhook_name: str | None = None
    webhook_notification_id: str | None = None
    webhook_url_info: WebhookUrlInfo | None = None
    webhook_scope: OpenWebhookScope | None = None
    webhook_notification_applicable_users: list[NotificationApplicableUser] = Field(
        default_factory=list
    )
    event: OpenWebhookEvent
    sub_event: str | None = None
    event_date: datetime | None = None
    event_resource_type: str | None = None
    event_resource_parent_type: str | None = None
    event_resource_parent_id: str | None = None
    participant_role: OpenParticipantRole | None = None
    action_type: str | None = None
    participant_user_id: str | None = None
    participant_user_email: str | None = None
    acting_user_id: str | None = None
    acting_user_email: str | None = None
    acting_user_ip_address: str | None = None
    initiating_user_id: str | None = None
    initiating_user_email: str | None = None
    agreement: NotificationAgreement | None = None


class Page(BaseModel, Generic[ItemT]):
    """One page of a cursor-paginated list."""

    items: list[ItemT]
    next_cursor: str | None = None

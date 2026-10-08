"""Request shape and response parsing for the ``/agreements`` endpoints.

Fixtures model the v6 API reference sections: "agreements" → ``POST
/agreements`` (AgreementCreationResponse), ``GET /agreements/{agreementId}``
(AgreementInfo), ``GET /agreements`` (UserAgreements), ``GET
.../members`` (MembersInfo), ``GET .../events`` (AgreementEvents), ``PUT
.../state`` (AgreementStateInfo), ``POST .../reminders`` (ReminderInfo /
ReminderCreationResult), ``GET .../combinedDocument``, ``GET .../auditTrail``
and ``GET .../signingUrls`` (SigningUrlResponse).
"""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone

import httpx
import pytest
import respx

from adobesign import AdobeSignClient
from adobesign import AgreementCreate
from adobesign import AgreementCreateState
from adobesign import AgreementEventType
from adobesign import AgreementStatus
from adobesign import CcInfo
from adobesign import ExternalId
from adobesign import FileInfo
from adobesign import MemberInfo
from adobesign import ParticipantRole
from adobesign import ParticipantSetInfo
from adobesign import ParticipantSetStatus

from endpoint_cases import AGREEMENT_ID
from endpoint_cases import PDF_BYTES
from helpers import API
from helpers import assert_bearer
from helpers import json_response

AGREEMENT_URL = f"{API}/agreements/{AGREEMENT_ID}"


@pytest.mark.covers("AgreementsResource.create")
@pytest.mark.covers("AdobeSignModel.to_api")
@pytest.mark.covers("ParticipantSetInfo.single")
def test_create_sends_documented_agreement_info_body(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(f"{API}/agreements").mock(
        return_value=json_response("agreement_created.json")
    )
    agreement = AgreementCreate(
        name="Consultancy agreement",
        file_infos=[FileInfo(transient_document_id="3AAABtransient")],
        participant_sets_info=[
            ParticipantSetInfo.single("alice@example.com", order=1, name="Alice"),
            ParticipantSetInfo(
                member_infos=[MemberInfo(email="bob@example.com")],
                order=2,
                role=ParticipantRole.APPROVER,
            ),
        ],
        message="Please sign",
        ccs=[CcInfo(email="records@example.com")],
        external_id=ExternalId(id="crm-1042"),
        expiration_time=datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc),
    )

    created = client.agreements.create(agreement)

    assert created.id == AGREEMENT_ID
    request = route.calls.last.request
    assert_bearer(request)
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "fileInfos": [{"transientDocumentId": "3AAABtransient"}],
        "name": "Consultancy agreement",
        "participantSetsInfo": [
            {
                "memberInfos": [{"email": "alice@example.com", "name": "Alice"}],
                "order": 1,
                "role": "SIGNER",
            },
            {
                "memberInfos": [{"email": "bob@example.com"}],
                "order": 2,
                "role": "APPROVER",
            },
        ],
        "signatureType": "ESIGN",
        "state": "IN_PROCESS",
        "message": "Please sign",
        "ccs": [{"email": "records@example.com"}],
        "externalId": {"id": "crm-1042"},
        "expirationTime": "2026-10-31T23:59:59Z",
    }


@pytest.mark.covers("AgreementsResource.create")
@pytest.mark.parametrize(
    "state", [AgreementCreateState.DRAFT, AgreementCreateState.AUTHORING]
)
def test_create_supports_draft_and_authoring_states(
    client: AdobeSignClient,
    mock_api: respx.MockRouter,
    state: AgreementCreateState,
) -> None:
    route = mock_api.post(f"{API}/agreements").mock(
        return_value=json_response("agreement_created.json")
    )
    agreement = AgreementCreate(
        name="Draft",
        file_infos=[FileInfo(transient_document_id="t")],
        participant_sets_info=[ParticipantSetInfo.single("a@example.com", order=1)],
        state=state,
    )

    client.agreements.create(agreement)

    assert json.loads(route.calls.last.request.content)["state"] == state.value


@pytest.mark.covers("AgreementsResource.get")
def test_get_parses_agreement_info(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(AGREEMENT_URL).mock(
        return_value=json_response("agreement.json")
    )

    agreement = client.agreements.get(AGREEMENT_ID)

    assert_bearer(route.calls.last.request)
    assert route.calls.last.request.headers["Accept"] == "application/json"
    assert agreement.id == AGREEMENT_ID
    assert agreement.status is AgreementStatus.OUT_FOR_SIGNATURE
    assert agreement.created_date == datetime(
        2026, 10, 1, 9, 15, 22, tzinfo=timezone.utc
    )
    assert agreement.external_id == ExternalId(id="crm-quote-1042")
    signer_set, approver_set = agreement.participant_sets_info
    assert signer_set.role is ParticipantRole.SIGNER
    assert signer_set.member_infos[0].email == "alice@example.com"
    assert approver_set.role is ParticipantRole.APPROVER
    assert agreement.ccs[0].email == "records@example.com"


@pytest.mark.covers("AgreementsResource.get")
def test_get_keeps_unknown_status_as_string(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    mock_api.get(AGREEMENT_URL).mock(
        return_value=httpx.Response(
            200, json={"id": AGREEMENT_ID, "name": "n", "status": "SOME_NEW_STATUS"}
        )
    )

    agreement = client.agreements.get(AGREEMENT_ID)

    assert agreement.status == "SOME_NEW_STATUS"
    assert not isinstance(agreement.status, AgreementStatus)


@pytest.mark.covers("AgreementsResource.get")
def test_ids_are_percent_encoded_as_one_path_segment(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(f"{API}/agreements/a%2Fb%3Fc").mock(
        return_value=json_response("agreement.json")
    )

    client.agreements.get("a/b?c")

    assert route.called


@pytest.mark.covers("AgreementsResource.list")
def test_list_follows_cursor_across_pages(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(f"{API}/agreements").mock(
        side_effect=[
            json_response("agreements_page_1.json"),
            json_response("agreements_page_2.json"),
        ]
    )

    agreements = list(client.agreements.list(page_size=1))

    assert [a.name for a in agreements] == [
        "Consultancy agreement — Example Ltd",
        "Renewal 2026",
    ]
    assert agreements[1].status is AgreementStatus.SIGNED
    first, second = (call.request for call in route.calls)
    assert dict(first.url.params) == {"pageSize": "1"}
    assert dict(second.url.params) == {
        "cursor": "qJXXj2UAUX1X9rTSqoUOlkhsdo-",
        "pageSize": "1",
    }
    assert_bearer(second)


@pytest.mark.covers("AgreementsResource.list")
def test_list_is_lazy(client: AdobeSignClient, mock_api: respx.MockRouter) -> None:
    route = mock_api.get(f"{API}/agreements")

    client.agreements.list()

    assert not route.called


@pytest.mark.covers("AgreementsResource.get_members")
def test_get_members_parses_participant_status(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(f"{AGREEMENT_URL}/members").mock(
        return_value=json_response("agreement_members.json")
    )

    members = client.agreements.get_members(AGREEMENT_ID)

    assert_bearer(route.calls.last.request)
    signer_set = members.participant_sets[0]
    assert signer_set.status is ParticipantSetStatus.WAITING_FOR_MY_SIGNATURE
    assert signer_set.member_infos[0].name == "Alice Example"
    assert members.participant_sets[1].status is ParticipantSetStatus.NOT_YET_VISIBLE
    assert members.sender_info is not None
    assert members.sender_info.email == "sender@example.com"
    assert members.next_participant_sets[0].member_infos[0].email == "alice@example.com"
    assert members.ccs_info[0].email == "records@example.com"


@pytest.mark.covers("AgreementsResource.get_events")
def test_get_events_returns_typed_events(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(f"{AGREEMENT_URL}/events").mock(
        return_value=json_response("agreement_events.json")
    )

    events = client.agreements.get_events(AGREEMENT_ID)

    assert_bearer(route.calls.last.request)
    assert [event.type for event in events] == [
        AgreementEventType.CREATED,
        AgreementEventType.ACTION_REQUESTED,
        AgreementEventType.ESIGNED,
    ]
    assert events[2].acting_user_ip_address == "198.51.100.24"
    assert events[0].participant_role == "SENDER"


@pytest.mark.covers("AgreementsResource.cancel")
def test_cancel_puts_cancelled_state(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.put(f"{AGREEMENT_URL}/state").mock(
        return_value=httpx.Response(204)
    )

    result = client.agreements.cancel(
        AGREEMENT_ID, comment="Superseded", notify_others=False
    )

    assert result is None
    request = route.calls.last.request
    assert_bearer(request)
    assert json.loads(request.content) == {
        "state": "CANCELLED",
        "agreementCancellationInfo": {"comment": "Superseded", "notifyOthers": False},
    }


@pytest.mark.covers("AgreementsResource.cancel")
def test_cancel_defaults_notify_others(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.put(f"{AGREEMENT_URL}/state").mock(
        return_value=httpx.Response(204)
    )

    client.agreements.cancel(AGREEMENT_ID)

    assert json.loads(route.calls.last.request.content) == {
        "state": "CANCELLED",
        "agreementCancellationInfo": {"notifyOthers": True},
    }


@pytest.mark.covers("AgreementsResource.send_reminder")
def test_send_reminder_posts_reminder_info(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(f"{AGREEMENT_URL}/reminders").mock(
        return_value=json_response("reminder_created.json", status_code=201)
    )

    reminder = client.agreements.send_reminder(
        AGREEMENT_ID, ("participant-1", "participant-2"), note="Gentle nudge"
    )

    assert reminder.id == "CBJCHBCAABAAr3m1nd3rExample0000000000000"
    request = route.calls.last.request
    assert_bearer(request)
    assert json.loads(request.content) == {
        "recipientParticipantIds": ["participant-1", "participant-2"],
        "status": "ACTIVE",
        "note": "Gentle nudge",
    }


@pytest.mark.covers("AgreementsResource.download_combined_document")
def test_download_combined_document_returns_pdf_bytes(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(f"{AGREEMENT_URL}/combinedDocument").mock(
        return_value=httpx.Response(
            200, content=PDF_BYTES, headers={"Content-Type": "application/pdf"}
        )
    )

    pdf = client.agreements.download_combined_document(
        AGREEMENT_ID, attach_audit_report=True, attach_supporting_documents=False
    )

    assert pdf == PDF_BYTES
    request = route.calls.last.request
    assert_bearer(request)
    assert request.headers["Accept"] == "application/pdf"
    assert dict(request.url.params) == {
        "attachAuditReport": "true",
        "attachSupportingDocuments": "false",
    }


@pytest.mark.covers("AgreementsResource.download_combined_document")
def test_download_combined_document_omits_unset_options(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(f"{AGREEMENT_URL}/combinedDocument").mock(
        return_value=httpx.Response(200, content=PDF_BYTES)
    )

    client.agreements.download_combined_document(AGREEMENT_ID)

    assert dict(route.calls.last.request.url.params) == {"attachAuditReport": "false"}


@pytest.mark.covers("AgreementsResource.download_audit_trail")
def test_download_audit_trail_returns_pdf_bytes(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(f"{AGREEMENT_URL}/auditTrail").mock(
        return_value=httpx.Response(
            200, content=PDF_BYTES, headers={"Content-Type": "application/pdf"}
        )
    )

    pdf = client.agreements.download_audit_trail(AGREEMENT_ID)

    assert pdf.startswith(b"%PDF-")
    request = route.calls.last.request
    assert_bearer(request)
    assert request.headers["Accept"] == "application/pdf"
    assert not request.url.params


@pytest.mark.covers("AgreementsResource.get_signing_urls")
def test_get_signing_urls_parses_url_sets(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(f"{AGREEMENT_URL}/signingUrls").mock(
        return_value=json_response("signing_urls.json")
    )

    urls = client.agreements.get_signing_urls(
        AGREEMENT_ID, frame_parent="app.example.com"
    )

    request = route.calls.last.request
    assert_bearer(request)
    assert dict(request.url.params) == {"frameParent": "app.example.com"}
    url_set = urls.signing_url_set_infos[0]
    assert url_set.signing_url_set_name == "Client signatory"
    assert url_set.signing_urls[0].email == "alice@example.com"
    assert url_set.signing_urls[0].esign_url.startswith("https://secure.eu1")

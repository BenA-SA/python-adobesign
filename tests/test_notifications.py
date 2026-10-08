"""Webhook verification-of-intent handshake and notification parsing.

Modelled on the Acrobat Sign "Webhooks" developer guide: each webhook call
carries ``X-AdobeSign-ClientId``; the endpoint must answer 2xx and echo the id
in the same response header or as ``{"xAdobeSignClientId": ...}``, and must
not answer 2xx to an unrecognised id. The notification fixture follows the
guide's "Agreement events" payload (``AGREEMENT_ACTION_COMPLETED`` with the
``includeParticipantsInfo`` conditional parameter).
"""

from __future__ import annotations

import json

import pytest

from adobesign import AgreementStatus
from adobesign import ParticipantSetStatus
from adobesign import WebhookClientIdError
from adobesign import WebhookEvent
from adobesign import WebhookHandshake
from adobesign import WebhookPayloadError
from adobesign import WebhookScope
from adobesign import parse_notification
from adobesign import verify_webhook_request

from helpers import load_fixture

CLIENT_ID = "CBJCHBCAABAAtestClientId"


@pytest.mark.covers("verify_webhook_request")
@pytest.mark.covers("WebhookHandshake.headers")
@pytest.mark.covers("WebhookHandshake.body")
@pytest.mark.covers("WebhookHandshake.body_json")
def test_handshake_echoes_client_id_in_header_and_body() -> None:
    handshake = verify_webhook_request({"X-AdobeSign-ClientId": CLIENT_ID}, CLIENT_ID)

    assert handshake == WebhookHandshake(client_id=CLIENT_ID)
    assert handshake.status_code == 200
    assert handshake.headers["X-AdobeSign-ClientId"] == CLIENT_ID
    assert handshake.headers["Content-Type"] == "application/json"
    assert handshake.body == {"xAdobeSignClientId": CLIENT_ID}
    assert json.loads(handshake.body_json) == {"xAdobeSignClientId": CLIENT_ID}


@pytest.mark.covers("verify_webhook_request")
def test_handshake_header_lookup_is_case_insensitive_over_pairs() -> None:
    headers = [("host", "hooks.example.com"), ("x-adobesign-clientid", CLIENT_ID)]

    assert verify_webhook_request(headers, {"other", CLIENT_ID}).client_id == CLIENT_ID


@pytest.mark.covers("verify_webhook_request")
def test_handshake_rejects_unknown_client_id() -> None:
    with pytest.raises(WebhookClientIdError) as caught:
        verify_webhook_request({"X-AdobeSign-ClientId": "intruder"}, CLIENT_ID)

    assert caught.value.received_client_id == "intruder"
    assert "intruder" in str(caught.value)


@pytest.mark.covers("verify_webhook_request")
def test_handshake_rejects_missing_header() -> None:
    with pytest.raises(WebhookClientIdError) as caught:
        verify_webhook_request({}, CLIENT_ID)

    assert caught.value.received_client_id is None
    assert "missing" in str(caught.value)


@pytest.mark.covers("parse_notification")
@pytest.mark.parametrize("encode", [json.dumps, lambda d: json.dumps(d).encode(), dict])
def test_parse_notification_accepts_text_bytes_and_mapping(encode: object) -> None:
    payload = load_fixture("webhook_notification.json")

    notification = parse_notification(encode(payload))  # type: ignore[operator]

    assert notification.event is WebhookEvent.AGREEMENT_ACTION_COMPLETED
    assert notification.webhook_scope is WebhookScope.ACCOUNT
    assert notification.acting_user_email == "alice@example.com"
    assert notification.agreement is not None
    assert notification.agreement.status is AgreementStatus.OUT_FOR_SIGNATURE
    participants = notification.agreement.participant_sets_info
    assert participants is not None
    assert participants.participant_sets[1].status is (
        ParticipantSetStatus.WAITING_FOR_MY_APPROVAL
    )
    assert participants.next_participant_sets[0].member_infos[0].email == (
        "bob@example.com"
    )


@pytest.mark.covers("parse_notification")
def test_parse_notification_keeps_unknown_events_and_fields() -> None:
    notification = parse_notification(
        {"webhookId": "w", "event": "AGREEMENT_SOMETHING_NEW", "brandNewField": 1}
    )

    assert notification.event == "AGREEMENT_SOMETHING_NEW"
    assert notification.model_extra == {"brandNewField": 1}
    assert notification.agreement is None


@pytest.mark.covers("parse_notification")
@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (b"not json", "body is not valid JSON"),
        ("[1, 2]", "body is not a JSON object"),
        ({"event": "AGREEMENT_CREATED"}, "webhookId: missing"),
    ],
)
def test_parse_notification_rejects_invalid_bodies(body: object, detail: str) -> None:
    with pytest.raises(WebhookPayloadError) as caught:
        parse_notification(body)  # type: ignore[arg-type]

    assert detail in caught.value.detail

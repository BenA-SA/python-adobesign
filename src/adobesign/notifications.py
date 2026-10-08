"""Framework-agnostic helpers for receiving Acrobat Sign webhook calls.

Acrobat Sign calls a webhook URL in two ways, and both carry an
``X-AdobeSign-ClientId`` header naming the API application that registered
the webhook:

* a ``GET`` "verification of intent" when the webhook is created or
  re-activated;
* a ``POST`` per event, whose JSON body is the notification.

In both cases the endpoint must reply with a 2xx status **and** echo the
client id, either in an ``X-AdobeSign-ClientId`` response header or in a JSON
body ``{"xAdobeSignClientId": "<id>"}``. Otherwise Acrobat Sign refuses to
register the webhook, or keeps retrying the notification. A request bearing
an unknown client id must not get a 2xx.

Example (any framework)::

    handshake = verify_webhook_request(request.headers, MY_CLIENT_ID)
    if request.method == "POST":
        notification = parse_notification(request.body)
        ...
    return Response(handshake.body_json, status=handshake.status_code,
                    headers=handshake.headers)
"""

from __future__ import annotations

import json
from collections.abc import Collection
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from adobesign import errors
from adobesign.models import WebhookNotification

CLIENT_ID_HEADER = "X-AdobeSign-ClientId"
CLIENT_ID_BODY_KEY = "xAdobeSignClientId"

HeaderSource = Mapping[str, str] | Iterable[tuple[str, str]]


@dataclass(frozen=True)
class WebhookHandshake:
    """The response that acknowledges a verified webhook request."""

    client_id: str
    status_code: int = 200

    @property
    def headers(self) -> dict[str, str]:
        """Response headers that echo the client id."""
        return {CLIENT_ID_HEADER: self.client_id, "Content-Type": "application/json"}

    @property
    def body(self) -> dict[str, str]:
        """Response JSON body that echoes the client id."""
        return {CLIENT_ID_BODY_KEY: self.client_id}

    @property
    def body_json(self) -> str:
        """:attr:`body` serialised as JSON text."""
        return json.dumps(self.body)


def _header_value(headers: HeaderSource, name: str) -> str | None:
    """Case-insensitive header lookup over a mapping or ``(name, value)`` pairs."""
    pairs = headers.items() if isinstance(headers, Mapping) else headers
    wanted = name.lower()
    for key, value in pairs:
        if key.lower() == wanted:
            return value
    return None


def verify_webhook_request(
    headers: HeaderSource,
    expected_client_ids: str | Collection[str],
) -> WebhookHandshake:
    """Check the ``X-AdobeSign-ClientId`` header and build the reply.

    ``expected_client_ids`` is the client (application) id of the API app
    that registered the webhook, or a collection of acceptable ids. Raises
    :class:`~adobesign.errors.WebhookClientIdError` when the header is
    missing or not expected; the caller should then answer with a 4xx.
    """
    accepted = (
        {expected_client_ids}
        if isinstance(expected_client_ids, str)
        else set(expected_client_ids)
    )
    received = _header_value(headers, CLIENT_ID_HEADER)
    if received is None or received not in accepted:
        raise errors.WebhookClientIdError(received_client_id=received)
    return WebhookHandshake(client_id=received)


def parse_notification(body: bytes | str | Mapping[str, Any]) -> WebhookNotification:
    """Parse an agreement webhook notification body into a typed model.

    Unknown fields are kept (``model_extra``); unknown event names are kept
    as plain strings. Raises :class:`~adobesign.errors.WebhookPayloadError`
    for invalid JSON or a body that is not a notification.
    """
    payload: Any = body
    if isinstance(body, (bytes, str)):
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise errors.WebhookPayloadError(detail="body is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise errors.WebhookPayloadError(detail="body is not a JSON object")
    try:
        return WebhookNotification.model_validate(payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}: {issue['type']}"
            for issue in exc.errors(include_input=False)
        )
        raise errors.WebhookPayloadError(detail=problems) from exc

"""``/webhooks`` create, list (cursor-paginated) and delete.

Modelled on the v6 API reference, "webhooks" → ``POST /webhooks``
(WebhookInfo → WebhookCreationResponse), ``GET /webhooks`` (UserWebhooks with
``page.nextCursor``) and ``DELETE /webhooks/{webhookId}`` (204).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from adobesign import AdobeSignClient
from adobesign import WebhookCreate
from adobesign import WebhookEvent
from adobesign import WebhookResourceType
from adobesign import WebhookScope
from adobesign import WebhookState
from adobesign import WebhookUrlInfo

from endpoint_cases import AGREEMENT_ID
from endpoint_cases import WEBHOOK_ID
from helpers import API
from helpers import assert_bearer
from helpers import json_response


@pytest.mark.covers("WebhooksResource.create")
def test_create_posts_webhook_info(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(f"{API}/webhooks").mock(
        return_value=json_response("webhook_created.json", status_code=201)
    )
    webhook = WebhookCreate(
        name="One agreement",
        scope=WebhookScope.RESOURCE,
        resource_type=WebhookResourceType.AGREEMENT,
        resource_id=AGREEMENT_ID,
        webhook_subscription_events=[
            WebhookEvent.AGREEMENT_WORKFLOW_COMPLETED,
            WebhookEvent.AGREEMENT_RECALLED,
        ],
        webhook_url_info=WebhookUrlInfo(url="https://hooks.example.com/adobesign"),
    )

    created = client.webhooks.create(webhook)

    assert created.id == WEBHOOK_ID
    request = route.calls.last.request
    assert_bearer(request)
    assert json.loads(request.content) == {
        "name": "One agreement",
        "scope": "RESOURCE",
        "state": "ACTIVE",
        "webhookSubscriptionEvents": [
            "AGREEMENT_WORKFLOW_COMPLETED",
            "AGREEMENT_RECALLED",
        ],
        "webhookUrlInfo": {"url": "https://hooks.example.com/adobesign"},
        "resourceType": "AGREEMENT",
        "resourceId": AGREEMENT_ID,
    }


@pytest.mark.covers("WebhooksResource.list")
def test_list_follows_cursor_and_parses_webhooks(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(f"{API}/webhooks").mock(
        side_effect=[
            json_response("webhooks_page_1.json"),
            json_response("webhooks_page_2.json"),
        ]
    )

    webhooks = list(client.webhooks.list(page_size=50, show_inactive=True))

    assert [hook.scope for hook in webhooks] == [
        WebhookScope.ACCOUNT,
        WebhookScope.RESOURCE,
    ]
    assert webhooks[0].webhook_subscription_events == [
        WebhookEvent.AGREEMENT_WORKFLOW_COMPLETED,
        WebhookEvent.AGREEMENT_RECALLED,
    ]
    assert webhooks[0].webhook_url_info is not None
    assert webhooks[1].state is WebhookState.INACTIVE
    first, second = (call.request for call in route.calls)
    assert_bearer(first)
    assert dict(first.url.params) == {
        "pageSize": "50",
        "showInActiveWebhooks": "true",
    }
    assert dict(second.url.params)["cursor"] == "bXlDdXJzb3JWYWx1ZQ"


@pytest.mark.covers("WebhooksResource.delete")
def test_delete_sends_delete(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.delete(f"{API}/webhooks/{WEBHOOK_ID}").mock(
        return_value=httpx.Response(204)
    )

    assert client.webhooks.delete(WEBHOOK_ID) is None
    assert_bearer(route.calls.last.request)

"""``adobesign webhooks`` and ``adobesign notifications`` commands.

Shapes follow the v6 "webhooks" reference and the webhook developer guide
(``X-AdobeSign-ClientId`` handshake), as in ``test_webhooks.py`` and
``test_notifications.py``.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from cli_helpers import CLI_API
from cli_helpers import CliHarness
from endpoint_cases import AGREEMENT_ID
from endpoint_cases import WEBHOOK_ID
from helpers import FIXTURES
from helpers import json_response

HOOK_URL = "https://hooks.example.com/adobesign"


@pytest.mark.covers("cli:webhooks create")
def test_create_account_webhook(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(f"{CLI_API}/webhooks").mock(
        return_value=json_response("webhook_created.json")
    )

    outcome = run_cli(
        "webhooks", "create", "--name", "Completions", "--url", HOOK_URL,
        "--event", "AGREEMENT_WORKFLOW_COMPLETED", "--event", "AGREEMENT_RECALLED",
        "--yes",
    )  # fmt: skip

    assert outcome.exit_code == 0
    assert outcome.json() == {"id": WEBHOOK_ID}
    assert json.loads(route.calls.last.request.content) == {
        "name": "Completions",
        "scope": "ACCOUNT",
        "state": "ACTIVE",
        "webhookSubscriptionEvents": [
            "AGREEMENT_WORKFLOW_COMPLETED",
            "AGREEMENT_RECALLED",
        ],
        "webhookUrlInfo": {"url": HOOK_URL},
    }


@pytest.mark.covers("cli:webhooks create")
def test_create_for_one_agreement_defaults_to_all_events(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(f"{CLI_API}/webhooks").mock(
        return_value=json_response("webhook_created.json")
    )

    run_cli(
        "webhooks", "create", "--name", "One", "--url", HOOK_URL,
        "--agreement-id", AGREEMENT_ID, "--yes",
    )  # fmt: skip

    body = json.loads(route.calls.last.request.content)
    assert body["scope"] == "RESOURCE"
    assert body["resourceType"] == "AGREEMENT"
    assert body["resourceId"] == AGREEMENT_ID
    assert body["webhookSubscriptionEvents"] == ["AGREEMENT_ALL"]


@pytest.mark.covers("cli:webhooks create")
def test_create_requires_yes_and_dry_run_sends_nothing(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(f"{CLI_API}/webhooks")
    args = ("webhooks", "create", "--name", "N", "--url", HOOK_URL)

    refused = run_cli(*args)
    dry = run_cli(*args, "--dry-run")

    assert refused.exit_code == 2
    assert dry.exit_code == 0
    assert dry.json()["requests"][0]["body"]["webhookUrlInfo"] == {"url": HOOK_URL}
    assert not route.called


@pytest.mark.covers("cli:webhooks create")
def test_create_rejects_unknown_event(run_cli: CliHarness) -> None:
    outcome = run_cli(
        "webhooks", "create", "--name", "N", "--url", HOOK_URL, "--event", "NOPE"
    )

    assert outcome.exit_code == 2
    assert outcome.error()["type"] == "UsageError"


@pytest.mark.covers("cli:webhooks list")
def test_list_webhooks(run_cli: CliHarness, mock_api: respx.MockRouter) -> None:
    route = mock_api.get(f"{CLI_API}/webhooks").mock(
        side_effect=[
            json_response("webhooks_page_1.json"),
            json_response("webhooks_page_2.json"),
        ]
    )

    outcome = run_cli("webhooks", "list", "--include-inactive")

    assert outcome.exit_code == 0
    assert [hook["scope"] for hook in outcome.json()] == ["ACCOUNT", "RESOURCE"]
    assert route.calls[0].request.url.params["showInActiveWebhooks"] == "true"


@pytest.mark.covers("cli:webhooks delete")
def test_delete_webhook(run_cli: CliHarness, mock_api: respx.MockRouter) -> None:
    route = mock_api.delete(f"{CLI_API}/webhooks/{WEBHOOK_ID}").mock(
        return_value=httpx.Response(204)
    )

    refused = run_cli("webhooks", "delete", WEBHOOK_ID)
    dry = run_cli("webhooks", "delete", WEBHOOK_ID, "--dry-run")
    assert not route.called
    done = run_cli("webhooks", "delete", WEBHOOK_ID, "--yes")

    assert refused.exit_code == 2
    assert dry.json()["requests"][0]["method"] == "DELETE"
    assert done.exit_code == 0
    assert done.json() == {"id": WEBHOOK_ID, "deleted": True}
    assert route.call_count == 1


@pytest.mark.covers("cli:webhooks verify")
def test_verify_accepts_expected_client_id(run_cli: CliHarness) -> None:
    outcome = run_cli(
        "webhooks", "verify", "--received-client-id", "CID",
        "--expected-client-id", "CID",
    )  # fmt: skip

    assert outcome.exit_code == 0
    assert outcome.json() == {
        "status_code": 200,
        "headers": {"X-AdobeSign-ClientId": "CID", "Content-Type": "application/json"},
        "body": {"xAdobeSignClientId": "CID"},
    }


@pytest.mark.covers("cli:webhooks verify")
def test_verify_defaults_to_configured_client_id(run_cli: CliHarness) -> None:
    ok = run_cli(
        "webhooks", "verify", "--received-client-id", "CID", ADOBESIGN_CLIENT_ID="CID"
    )
    bad = run_cli(
        "webhooks", "verify", "--received-client-id", "EVIL", ADOBESIGN_CLIENT_ID="CID"
    )

    assert ok.exit_code == 0
    assert bad.exit_code == 3
    assert bad.error()["type"] == "WebhookClientIdError"


@pytest.mark.covers("cli:notifications parse")
def test_parse_notification_file(run_cli: CliHarness) -> None:
    outcome = run_cli(
        "notifications", "parse", str(FIXTURES / "webhook_notification.json")
    )

    assert outcome.exit_code == 0
    document = outcome.json()
    assert document["event"] == "AGREEMENT_ACTION_COMPLETED"
    assert document["agreement"]["id"] == AGREEMENT_ID


@pytest.mark.covers("cli:notifications parse")
def test_parse_notification_stdin_and_invalid(run_cli: CliHarness) -> None:
    good = run_cli(
        "notifications", "parse", "-", input='{"webhookId": "w", "event": "X"}'
    )
    bad = run_cli("notifications", "parse", "-", input="not json")

    assert good.json()["event"] == "X"
    assert bad.exit_code == 2
    assert bad.error()["type"] == "WebhookPayloadError"

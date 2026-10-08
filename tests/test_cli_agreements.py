"""``adobesign documents`` and ``adobesign agreements`` commands.

Responses come from the same v6-modelled fixtures as the library tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from adobesign.cli import app as cli_app

from cli_helpers import CLI_API
from cli_helpers import CliHarness
from endpoint_cases import AGREEMENT_ID
from endpoint_cases import PDF_BYTES
from helpers import FIXTURES
from helpers import json_response

SAMPLE = str(FIXTURES / "sample.pdf")
AGREEMENT_URL = f"{CLI_API}/agreements/{AGREEMENT_ID}"
M1 = "CBJCHBCAABAAm1Bq9Xw4zA7cV2nL6hJ0sD5fG8kP3rTy"


def mock_send(mock_api: respx.MockRouter) -> tuple[respx.Route, respx.Route]:
    upload = mock_api.post(f"{CLI_API}/transientDocuments").mock(
        return_value=json_response("transient_document.json")
    )
    create = mock_api.post(f"{CLI_API}/agreements").mock(
        return_value=json_response("agreement_created.json")
    )
    return upload, create


@pytest.mark.covers("cli:documents upload")
def test_documents_upload(run_cli: CliHarness, mock_api: respx.MockRouter) -> None:
    route = mock_api.post(f"{CLI_API}/transientDocuments").mock(
        return_value=json_response("transient_document.json")
    )

    outcome = run_cli("documents", "upload", SAMPLE)

    assert outcome.exit_code == 0
    assert outcome.json() == {
        "transientDocumentId": "3AAABLblqZhCtpwYb9nPjD0Xu6K1mHkOeQ8r2sTzWvYx-example"
    }
    assert route.call_count == 1


@pytest.mark.covers("cli:documents upload")
def test_documents_upload_dry_run_sends_nothing(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(f"{CLI_API}/transientDocuments")

    outcome = run_cli("documents", "upload", SAMPLE, "--dry-run")

    assert outcome.exit_code == 0
    assert not route.called
    (request,) = outcome.json()["requests"]
    assert request["method"] == "POST"
    assert request["body"]["multipart"]["File"] == "<605 bytes from sample.pdf>"


@pytest.mark.covers("cli:agreements send")
def test_send_uploads_then_creates_in_signing_order(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    upload, create = mock_send(mock_api)

    outcome = run_cli(
        "agreements", "send",
        "--file", SAMPLE,
        "--name", "Consultancy agreement",
        "--signer", "Alice Example <alice@example.com>",
        "--signer", "bob@example.com",
        "--message", "Please sign",
        "--cc", "records@example.com",
        "--external-id", "crm-1",
        "--yes",
    )  # fmt: skip

    assert outcome.exit_code == 0, outcome.stderr
    assert outcome.json() == {"id": AGREEMENT_ID}
    assert upload.call_count == 1
    body = json.loads(create.calls.last.request.content)
    assert body["state"] == "IN_PROCESS"
    assert body["fileInfos"] == [
        {"transientDocumentId": "3AAABLblqZhCtpwYb9nPjD0Xu6K1mHkOeQ8r2sTzWvYx-example"}
    ]
    assert [(s["order"], s["memberInfos"][0]) for s in body["participantSetsInfo"]] == [
        (1, {"email": "alice@example.com", "name": "Alice Example"}),
        (2, {"email": "bob@example.com"}),
    ]
    assert body["ccs"] == [{"email": "records@example.com"}]
    assert body["externalId"] == {"id": "crm-1"}
    assert body["message"] == "Please sign"


@pytest.mark.covers("cli:agreements send")
def test_send_parallel_puts_everyone_at_order_one(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    _, create = mock_send(mock_api)

    outcome = run_cli(
        "agreements", "send", "--document-id", "doc-1", "--name", "N",
        "--signer", "a@example.com", "--signer", "b@example.com",
        "--order", "parallel", "--yes",
    )  # fmt: skip

    assert outcome.exit_code == 0
    body = json.loads(create.calls.last.request.content)
    assert [s["order"] for s in body["participantSetsInfo"]] == [1, 1]
    assert body["fileInfos"] == [{"transientDocumentId": "doc-1"}]


@pytest.mark.covers("cli:agreements send")
def test_send_without_yes_refuses_when_not_interactive(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    upload, create = mock_send(mock_api)

    outcome = run_cli(
        "agreements", "send", "--file", SAMPLE, "--name", "N",
        "--signer", "a@example.com",
    )  # fmt: skip

    assert outcome.exit_code == 2
    assert outcome.error()["type"] == "ConfirmationRequired"
    assert "--yes" in outcome.error()["message"]
    assert not upload.called
    assert not create.called


@pytest.mark.covers("cli:agreements send")
@pytest.mark.parametrize(("answer", "exit_code"), [("y\n", 0), ("n\n", 2)])
def test_send_asks_for_confirmation_on_a_tty(
    run_cli: CliHarness,
    mock_api: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    exit_code: int,
) -> None:
    _, create = mock_send(mock_api)
    monkeypatch.setattr(cli_app, "is_interactive", lambda: True)

    outcome = run_cli(
        "agreements", "send", "--document-id", "d", "--name", "N",
        "--signer", "a@example.com", input=answer,
    )  # fmt: skip

    assert outcome.exit_code == exit_code
    assert "a@example.com (they will be emailed). Continue?" in outcome.stderr
    assert create.called is (exit_code == 0)


@pytest.mark.covers("cli:agreements send")
def test_send_dry_run_sends_nothing_and_needs_no_yes(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    upload, create = mock_send(mock_api)

    outcome = run_cli(
        "agreements", "send", "--file", SAMPLE, "--name", "N",
        "--signer", "Alice <a@example.com>", "--dry-run",
    )  # fmt: skip

    assert outcome.exit_code == 0
    assert not upload.called
    assert not create.called
    document = outcome.json()
    assert document["dry_run"] is True
    upload_request, create_request = document["requests"]
    assert upload_request["url"] == f"{CLI_API}/transientDocuments"
    assert create_request["url"] == f"{CLI_API}/agreements"
    assert create_request["headers"]["authorization"] == "Bearer ***REDACTED***"
    assert create_request["body"]["fileInfos"] == [
        {"transientDocumentId": "DRY-RUN-TRANSIENT-DOCUMENT-ID"}
    ]


@pytest.mark.covers("cli:agreements send")
@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--signer", "not an email"], "Invalid --signer"),
        ([], "At least one --signer"),
        (["--signer", "a@example.com", "--no-files"], None),
    ],
)
def test_send_validation_errors_exit_2(
    run_cli: CliHarness, extra: list[str], message: str | None
) -> None:
    files = [] if "--no-files" in extra else ["--document-id", "d"]
    args = [arg for arg in extra if arg != "--no-files"]

    outcome = run_cli("agreements", "send", "--name", "N", *files, *args, "--dry-run")

    assert outcome.exit_code == 2
    expected = message or "Give at least one --file or --document-id"
    assert expected in outcome.error()["message"]


@pytest.mark.covers("cli:agreements draft")
def test_draft_creates_draft_without_confirmation(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    _, create = mock_send(mock_api)

    outcome = run_cli(
        "agreements", "draft", "--document-id", "d", "--name", "N",
        "--signer", "a@example.com",
    )  # fmt: skip

    assert outcome.exit_code == 0
    assert json.loads(create.calls.last.request.content)["state"] == "DRAFT"


@pytest.mark.covers("cli:agreements draft")
def test_draft_dry_run_sends_nothing(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    _, create = mock_send(mock_api)

    outcome = run_cli(
        "agreements", "draft", "--document-id", "d", "--name", "N",
        "--signer", "a@example.com", "--dry-run",
    )  # fmt: skip

    assert outcome.exit_code == 0
    assert not create.called
    assert outcome.json()["requests"][0]["body"]["state"] == "DRAFT"


@pytest.mark.covers("cli:agreements get")
def test_get_prints_agreement_with_api_field_names(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    mock_api.get(AGREEMENT_URL).mock(return_value=json_response("agreement.json"))

    outcome = run_cli("agreements", "get", AGREEMENT_ID)

    assert outcome.exit_code == 0
    document = outcome.json()
    assert document["id"] == AGREEMENT_ID
    assert document["status"] == "OUT_FOR_SIGNATURE"
    assert document["participantSetsInfo"][0]["memberInfos"][0]["email"] == (
        "alice@example.com"
    )


@pytest.mark.covers("cli:agreements get")
def test_get_table_format(run_cli: CliHarness, mock_api: respx.MockRouter) -> None:
    mock_api.get(AGREEMENT_URL).mock(return_value=json_response("agreement.json"))

    outcome = run_cli("agreements", "get", AGREEMENT_ID, "--format", "table")

    assert outcome.exit_code == 0
    lines = outcome.stdout.splitlines()
    assert lines[0].split() == ["field", "value"]
    assert any(
        line.startswith("status") and "OUT_FOR_SIGNATURE" in line for line in lines
    )


@pytest.mark.covers("cli:agreements list")
def test_list_respects_limit_across_pages(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(f"{CLI_API}/agreements").mock(
        side_effect=[
            json_response("agreements_page_1.json"),
            json_response("agreements_page_2.json"),
        ]
    )

    outcome = run_cli("agreements", "list", "--limit", "2", "--page-size", "1")

    assert outcome.exit_code == 0
    assert [row["name"] for row in outcome.json()] == [
        "Consultancy agreement — Example Ltd",
        "Renewal 2026",
    ]
    assert route.call_count == 2


@pytest.mark.covers("cli:agreements list")
def test_list_table_format(run_cli: CliHarness, mock_api: respx.MockRouter) -> None:
    mock_api.get(f"{CLI_API}/agreements").mock(
        return_value=json_response("agreements_page_2.json")
    )

    outcome = run_cli("agreements", "list", "--format", "table")

    header, row = outcome.stdout.splitlines()
    assert header.split()[:3] == ["id", "name", "status"]
    assert "Renewal 2026" in row


@pytest.mark.covers("cli:agreements members")
def test_members(run_cli: CliHarness, mock_api: respx.MockRouter) -> None:
    mock_api.get(f"{AGREEMENT_URL}/members").mock(
        return_value=json_response("agreement_members.json")
    )

    outcome = run_cli("agreements", "members", AGREEMENT_ID)

    assert outcome.exit_code == 0
    assert outcome.json()["participantSets"][0]["status"] == "WAITING_FOR_MY_SIGNATURE"


@pytest.mark.covers("cli:agreements events")
def test_events_json_and_table(run_cli: CliHarness, mock_api: respx.MockRouter) -> None:
    mock_api.get(f"{AGREEMENT_URL}/events").mock(
        return_value=json_response("agreement_events.json")
    )

    as_json = run_cli("agreements", "events", AGREEMENT_ID)
    as_table = run_cli("agreements", "events", AGREEMENT_ID, "--format", "table")

    assert [event["type"] for event in as_json.json()] == [
        "CREATED",
        "ACTION_REQUESTED",
        "ESIGNED",
    ]
    assert len(as_table.stdout.splitlines()) == 4


@pytest.mark.covers("cli:agreements cancel")
def test_cancel_with_yes(run_cli: CliHarness, mock_api: respx.MockRouter) -> None:
    route = mock_api.put(f"{AGREEMENT_URL}/state").mock(
        return_value=httpx.Response(204)
    )

    outcome = run_cli(
        "agreements", "cancel", AGREEMENT_ID, "--comment", "Superseded",
        "--no-notify", "--yes",
    )  # fmt: skip

    assert outcome.exit_code == 0
    assert outcome.json() == {"id": AGREEMENT_ID, "state": "CANCELLED"}
    assert json.loads(route.calls.last.request.content) == {
        "state": "CANCELLED",
        "agreementCancellationInfo": {"comment": "Superseded", "notifyOthers": False},
    }


@pytest.mark.covers("cli:agreements cancel")
def test_cancel_requires_yes_and_dry_run_sends_nothing(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    route = mock_api.put(f"{AGREEMENT_URL}/state")

    refused = run_cli("agreements", "cancel", AGREEMENT_ID)
    dry = run_cli("agreements", "cancel", AGREEMENT_ID, "--dry-run")

    assert refused.exit_code == 2
    assert "email its participants" in refused.error()["message"]
    assert dry.exit_code == 0
    assert dry.json()["requests"][0]["method"] == "PUT"
    assert not route.called


@pytest.mark.covers("cli:agreements remind")
def test_remind_defaults_to_pending_participants(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    mock_api.get(f"{AGREEMENT_URL}/members").mock(
        return_value=json_response("agreement_members.json")
    )
    remind = mock_api.post(f"{AGREEMENT_URL}/reminders").mock(
        return_value=json_response("reminder_created.json")
    )

    outcome = run_cli("agreements", "remind", AGREEMENT_ID, "--note", "Hi", "--yes")

    assert outcome.exit_code == 0
    assert outcome.json() == {"id": "CBJCHBCAABAAr3m1nd3rExample0000000000000"}
    assert json.loads(remind.calls.last.request.content) == {
        "recipientParticipantIds": [M1],
        "status": "ACTIVE",
        "note": "Hi",
    }


@pytest.mark.covers("cli:agreements remind")
def test_remind_explicit_participants_skip_members_lookup(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    members = mock_api.get(f"{AGREEMENT_URL}/members")
    remind = mock_api.post(f"{AGREEMENT_URL}/reminders").mock(
        return_value=json_response("reminder_created.json")
    )

    outcome = run_cli(
        "agreements", "remind", AGREEMENT_ID, "--participant", "p1", "--yes"
    )

    assert outcome.exit_code == 0
    assert not members.called
    body = json.loads(remind.calls.last.request.content)
    assert body["recipientParticipantIds"] == ["p1"]


@pytest.mark.covers("cli:agreements remind")
def test_remind_with_nobody_pending_exits_2(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    mock_api.get(f"{AGREEMENT_URL}/members").mock(
        return_value=httpx.Response(200, json={"participantSets": []})
    )

    outcome = run_cli("agreements", "remind", AGREEMENT_ID, "--yes")

    assert outcome.exit_code == 2
    assert "Nobody is waiting" in outcome.error()["message"]


@pytest.mark.covers("cli:agreements remind")
def test_remind_gating_and_dry_run(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    remind = mock_api.post(f"{AGREEMENT_URL}/reminders")

    refused = run_cli("agreements", "remind", AGREEMENT_ID)
    dry = run_cli("agreements", "remind", AGREEMENT_ID, "--dry-run")

    assert refused.exit_code == 2
    assert dry.exit_code == 0
    members_request, remind_request = dry.json()["requests"]
    assert members_request["method"] == "GET"
    assert remind_request["body"]["recipientParticipantIds"] == [
        "<pending participant ids from GET .../members>"
    ]
    assert not remind.called


@pytest.mark.covers("cli:agreements signing-urls")
def test_signing_urls(run_cli: CliHarness, mock_api: respx.MockRouter) -> None:
    route = mock_api.get(f"{AGREEMENT_URL}/signingUrls").mock(
        return_value=json_response("signing_urls.json")
    )

    outcome = run_cli(
        "agreements", "signing-urls", AGREEMENT_ID, "--frame-parent", "app.example.com"
    )

    assert outcome.exit_code == 0
    url_set = outcome.json()["signingUrlSetInfos"][0]
    assert url_set["signingUrls"][0]["email"] == "alice@example.com"
    assert route.calls.last.request.url.params["frameParent"] == "app.example.com"


@pytest.mark.covers("cli:agreements download")
@pytest.mark.parametrize(
    ("flag", "path", "kind"),
    [
        ("--signed", "combinedDocument", "signed"),
        ("--audit-trail", "auditTrail", "audit-trail"),
    ],
)
def test_download_writes_pdf(
    run_cli: CliHarness,
    mock_api: respx.MockRouter,
    tmp_path: Path,
    flag: str,
    path: str,
    kind: str,
) -> None:
    mock_api.get(f"{AGREEMENT_URL}/{path}").mock(
        return_value=httpx.Response(200, content=PDF_BYTES)
    )
    target = tmp_path / "out.pdf"

    outcome = run_cli("agreements", "download", AGREEMENT_ID, flag, "-o", str(target))

    assert outcome.exit_code == 0
    assert outcome.json() == {
        "path": str(target),
        "bytes": len(PDF_BYTES),
        "kind": kind,
    }
    assert target.read_bytes() == PDF_BYTES


@pytest.mark.covers("cli:agreements download")
def test_download_needs_a_kind(run_cli: CliHarness, tmp_path: Path) -> None:
    outcome = run_cli("agreements", "download", AGREEMENT_ID, "-o", str(tmp_path / "x"))

    assert outcome.exit_code == 2
    assert "--signed or --audit-trail" in outcome.error()["message"]

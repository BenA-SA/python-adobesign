"""The CLI's machine contract: exit codes, error documents, redaction, entry point.

Every HTTP-calling command is run against each mapped failure status and
must exit with the documented code and a complete ``{"error": ...}``
document on stderr.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
import respx

from adobesign import cli as cli_entry
from adobesign.cli._config import default_config_path
from adobesign.cli._http import DryRunCredentials
from adobesign.cli._http import describe_request
from adobesign.cli._output import render_table

from cli_helpers import CLI_API
from cli_helpers import CLI_KEY
from cli_helpers import CliHarness
from endpoint_cases import AGREEMENT_ID
from endpoint_cases import WEBHOOK_ID
from helpers import FIXTURES

AGREEMENT_URL = f"{CLI_API}/agreements/{AGREEMENT_ID}"
ERROR_KEYS = {
    "type",
    "message",
    "exit_code",
    "status_code",
    "code",
    "api_message",
    "request_id",
    "retry_after",
}

HTTP_COMMANDS: list[tuple[list[str], str, str]] = [
    (["auth", "base-uris"], "GET", "https://api.adobesign.com/api/rest/v6/baseUris"),
    (
        ["documents", "upload", str(FIXTURES / "sample.pdf")],
        "POST",
        f"{CLI_API}/transientDocuments",
    ),
    (
        [
            "agreements",
            "send",
            "--document-id",
            "d",
            "--name",
            "N",
            "--signer",
            "a@example.com",
            "--yes",
        ],
        "POST",
        f"{CLI_API}/agreements",
    ),
    (
        [
            "agreements",
            "draft",
            "--document-id",
            "d",
            "--name",
            "N",
            "--signer",
            "a@example.com",
        ],
        "POST",
        f"{CLI_API}/agreements",
    ),
    (["agreements", "get", AGREEMENT_ID], "GET", AGREEMENT_URL),
    (["agreements", "list"], "GET", f"{CLI_API}/agreements"),
    (["agreements", "members", AGREEMENT_ID], "GET", f"{AGREEMENT_URL}/members"),
    (["agreements", "events", AGREEMENT_ID], "GET", f"{AGREEMENT_URL}/events"),
    (["agreements", "cancel", AGREEMENT_ID, "--yes"], "PUT", f"{AGREEMENT_URL}/state"),
    (
        ["agreements", "remind", AGREEMENT_ID, "--participant", "p", "--yes"],
        "POST",
        f"{AGREEMENT_URL}/reminders",
    ),
    (
        ["agreements", "signing-urls", AGREEMENT_ID],
        "GET",
        f"{AGREEMENT_URL}/signingUrls",
    ),
    (
        ["agreements", "download", AGREEMENT_ID, "--signed", "-o", "out.pdf"],
        "GET",
        f"{AGREEMENT_URL}/combinedDocument",
    ),
    (
        [
            "webhooks",
            "create",
            "--name",
            "N",
            "--url",
            "https://h.example.com",
            "--yes",
        ],
        "POST",
        f"{CLI_API}/webhooks",
    ),
    (["webhooks", "list"], "GET", f"{CLI_API}/webhooks"),
    (
        ["webhooks", "delete", WEBHOOK_ID, "--yes"],
        "DELETE",
        f"{CLI_API}/webhooks/{WEBHOOK_ID}",
    ),
]


@pytest.mark.parametrize(
    ("args", "method", "url"),
    HTTP_COMMANDS,
    ids=[" ".join(c[0][:2]) for c in HTTP_COMMANDS],
)
@pytest.mark.parametrize(
    ("status", "exit_code", "error_type"),
    [
        (400, 2, "BadRequestError"),
        (401, 3, "AuthenticationError"),
        (403, 3, "PermissionDeniedError"),
        (404, 5, "NotFoundError"),
        (429, 4, "RateLimitedError"),
        (500, 1, "ServerError"),
    ],
)
def test_api_failures_map_to_documented_exit_codes(
    run_cli: CliHarness,
    mock_api: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    args: list[str],
    method: str,
    url: str,
    status: int,
    exit_code: int,
    error_type: str,
) -> None:
    monkeypatch.chdir(str(tmp_path))
    mock_api.route(method=method, url=url).mock(
        return_value=httpx.Response(
            status,
            json={"code": "SOME_CODE", "message": "Something happened"},
            headers={"Retry-After": "30", "X-Request-Id": "req-9"},
        )
    )

    outcome = run_cli(*args)

    assert outcome.exit_code == exit_code
    error = outcome.error()
    assert set(error) == ERROR_KEYS
    assert error["type"] == error_type
    assert error["status_code"] == status
    assert error["code"] == "SOME_CODE"
    assert error["request_id"] == "req-9"
    assert error["exit_code"] == exit_code
    assert error["retry_after"] == (30.0 if status == 429 else None)
    assert outcome.stdout == ""


def test_network_failure_exits_1(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    mock_api.get(AGREEMENT_URL).mock(side_effect=httpx.ConnectError("refused"))

    outcome = run_cli("agreements", "get", AGREEMENT_ID)

    assert outcome.exit_code == 1
    assert outcome.error()["type"] == "TransportError"


def test_retries_are_on_by_default(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(AGREEMENT_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"id": "a", "name": "n", "status": "SIGNED"}),
        ]
    )

    outcome = run_cli("agreements", "get", AGREEMENT_ID, ADOBESIGN_MAX_RETRIES=None)

    assert outcome.exit_code == 0
    assert route.call_count == 2


def test_invalid_max_retries_exits_2(run_cli: CliHarness) -> None:
    outcome = run_cli("agreements", "get", "A", ADOBESIGN_MAX_RETRIES="lots")

    assert outcome.exit_code == 2
    assert "ADOBESIGN_MAX_RETRIES" in outcome.error()["message"]


def test_verbose_logs_requests_with_credentials_redacted(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    mock_api.get(AGREEMENT_URL).mock(
        return_value=httpx.Response(
            200, json={"id": "a", "name": "n", "status": "SIGNED"}
        )
    )

    outcome = run_cli("agreements", "get", AGREEMENT_ID, "--verbose")

    assert outcome.exit_code == 0
    assert '"verbose": "request"' in outcome.stderr
    assert '"verbose": "response"' in outcome.stderr
    assert "Bearer ***REDACTED***" in outcome.stderr
    assert CLI_KEY not in outcome.stderr + outcome.stdout


def test_dry_run_never_shows_the_key_or_touches_the_network(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    outcome = run_cli(
        "agreements", "cancel", AGREEMENT_ID, "--dry-run", "--verbose",
        ADOBESIGN_BASE_URI=None,
    )  # fmt: skip

    assert outcome.exit_code == 0
    assert not mock_api.calls
    discovery, cancel = outcome.json()["requests"]
    assert discovery["url"] == "https://api.adobesign.com/api/rest/v6/baseUris"
    assert cancel["url"].startswith("https://api-access-point.dry-run.invalid/")
    assert CLI_KEY not in outcome.stderr + outcome.stdout


def test_click_usage_errors_are_json(run_cli: CliHarness) -> None:
    outcome = run_cli("agreements", "get", "--no-such-option")

    assert outcome.exit_code == 2
    assert outcome.error()["type"] == "UsageError"
    assert "--no-such-option" in outcome.error()["message"]


def test_help_and_version_exit_0(run_cli: CliHarness) -> None:
    help_outcome = run_cli("agreements", "send", "--help")
    version = run_cli("--version")

    assert help_outcome.exit_code == 0
    assert "Emails or calls anyone: YES" in help_outcome.stdout
    assert "Exit codes: 0 ok" in help_outcome.stdout
    assert version.exit_code == 0
    assert "adobesign" in version.stdout


def test_ctrl_c_at_prompt_exits_2(
    run_cli: CliHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adobesign.cli import app as cli_app

    monkeypatch.setattr(cli_app, "is_interactive", lambda: True)

    outcome = run_cli("agreements", "cancel", "A", input="")

    assert outcome.exit_code == 2
    assert outcome.error()["type"] == "Aborted"


def test_entry_point_runs_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["adobesign", "--version"])

    with pytest.raises(SystemExit) as caught:
        cli_entry.main()

    assert caught.value.code == 0


def test_entry_point_without_cli_extra_prints_install_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delitem(sys.modules, "adobesign.cli.app")
    monkeypatch.setitem(sys.modules, "click", None)

    with pytest.raises(SystemExit) as caught:
        cli_entry.main()

    assert caught.value.code == 2
    assert "python-adobesign[cli]" in capsys.readouterr().err


def test_entry_point_reraises_unrelated_import_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "adobesign.cli.app")
    monkeypatch.setitem(sys.modules, "adobesign.cli._http", None)

    with pytest.raises(ModuleNotFoundError):
        cli_entry.main()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ([], ""),
        (["a", None], "a\n"),
        ("plain", "plain"),
        (None, ""),
        ([{"id": "1", "nested": {"x": 1}}], "id\n1"),
        ({"k": [1, 2]}, "field  value\nk      [1, 2]"),
    ],
)
def test_table_rendering(data: object, expected: str) -> None:
    assert render_table(data) == expected


def test_default_config_path_resolution(tmp_path: Path) -> None:
    assert (
        default_config_path({"ADOBESIGN_CONFIG": "~/x.toml"})
        == Path("~/x.toml").expanduser()
    )
    assert default_config_path({"XDG_CONFIG_HOME": str(tmp_path)}) == (
        tmp_path / "adobesign" / "config.toml"
    )
    assert default_config_path({}) == (
        Path.home() / ".config" / "adobesign" / "config.toml"
    )


def test_describe_request_handles_opaque_bodies() -> None:
    request = httpx.Request(
        "POST",
        "https://example.com",
        content=b"\x00binary",
        headers={"Content-Type": "application/octet-stream", "X-Api-Key": "k"},
    )

    described = describe_request(request)

    assert described["body"] == "<7 bytes>"
    assert described["headers"]["x-api-key"] == "***REDACTED***"


def test_dry_run_credentials_never_refresh() -> None:
    credentials = DryRunCredentials(None)

    assert credentials.handle_unauthorized() is False
    assert credentials.api_access_point is None

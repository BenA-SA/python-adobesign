"""``adobesign auth`` commands and credential/config handling.

OAuth shapes follow the developer guide's "Managing OAuth tokens" section, as
in ``test_auth.py``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import httpx
import pytest
import respx

from adobesign.cli._config import read_config_file
from adobesign.cli._config import write_config_file

from cli_helpers import CLI_ACCESS_POINT
from cli_helpers import CLI_KEY
from cli_helpers import CliHarness
from helpers import json_response

CLIENT_ID = "CBJCHBCAABAAcliClientId"
CLIENT_SECRET = "cli-client-secret-value"
REDIRECT_URI = "https://app.example.com/callback"
TOKEN_URL = f"{CLI_ACCESS_POINT}oauth/v2/token"
REFRESH_URL = f"{CLI_ACCESS_POINT}oauth/v2/refresh"
OAUTH_ENV = {
    "ADOBESIGN_INTEGRATION_KEY": None,
    "ADOBESIGN_CLIENT_ID": CLIENT_ID,
    "ADOBESIGN_CLIENT_SECRET": CLIENT_SECRET,
    "ADOBESIGN_REDIRECT_URI": REDIRECT_URI,
}
REDIRECT = (
    f"{REDIRECT_URI}?code=ONE-TIME-CODE-123&state=st8"
    "&api_access_point=https%3A%2F%2Fapi.eu1.adobesign.com%2F"
    "&web_access_point=https%3A%2F%2Fsecure.eu1.adobesign.com%2F"
)
SECRETS = (
    CLIENT_SECRET,
    "ONE-TIME-CODE-123",
    "3AAABLblqZhBexampleAccessToken",
    "3AAABLblqZhCexampleRefreshToken",
    "stored-refresh",
    CLI_KEY,
)


def assert_no_secrets(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


def config_path(harness: CliHarness) -> Path:
    return Path(harness.env["ADOBESIGN_CONFIG"])


def write_tokens(
    harness: CliHarness, *, expires_at: str = "2099-01-01T00:00:00Z"
) -> Path:
    path = config_path(harness)
    write_config_file(
        path,
        {
            "client_id": CLIENT_ID,
            "tokens": {
                "access_token": "stored-access",
                "refresh_token": "stored-refresh",
                "expires_at": expires_at,
                "api_access_point": CLI_ACCESS_POINT,
            },
        },
    )
    return path


@pytest.mark.covers("cli:auth login-url")
def test_login_url_builds_authorisation_url(run_cli: CliHarness) -> None:
    outcome = run_cli("auth", "login-url", "--state", "st8", **OAUTH_ENV)

    assert outcome.exit_code == 0
    document = outcome.json()
    query = parse_qs(urlsplit(document["url"]).query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["state"] == ["st8"]
    assert "agreement_send:account" in query["scope"][0]
    assert document["redirect_uri"] == REDIRECT_URI
    assert_no_secrets(outcome.stdout)


@pytest.mark.covers("cli:auth login-url")
def test_login_url_generates_state_and_custom_scopes(run_cli: CliHarness) -> None:
    outcome = run_cli("auth", "login-url", "--scope", "user_login:self", **OAUTH_ENV)

    document = outcome.json()
    assert len(document["state"]) >= 24
    assert parse_qs(urlsplit(document["url"]).query)["scope"] == ["user_login:self"]


@pytest.mark.covers("cli:auth login-url")
def test_login_url_without_client_id_exits_3(run_cli: CliHarness) -> None:
    outcome = run_cli("auth", "login-url")

    assert outcome.exit_code == 3
    assert outcome.error()["type"] == "CredentialsError"
    assert "ADOBESIGN_CLIENT_ID" in outcome.error()["message"]


@pytest.mark.covers("cli:auth exchange")
def test_exchange_saves_tokens_0600_and_prints_no_secrets(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(TOKEN_URL).mock(
        return_value=json_response("oauth_token.json")
    )

    outcome = run_cli("auth", "exchange", REDIRECT, "--state", "st8", **OAUTH_ENV)

    assert outcome.exit_code == 0, outcome.stderr
    assert route.call_count == 1
    document = outcome.json()
    assert document["saved_to"] == str(config_path(run_cli))
    assert document["has_refresh_token"] is True
    assert document["api_access_point"] == CLI_ACCESS_POINT
    assert_no_secrets(outcome.stdout + outcome.stderr)
    path = config_path(run_cli)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    saved = read_config_file(path)["tokens"]
    assert saved["refresh_token"] == "3AAABLblqZhCexampleRefreshToken"


@pytest.mark.covers("cli:auth exchange")
def test_exchange_reads_redirect_from_stdin(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    mock_api.post(TOKEN_URL).mock(return_value=json_response("oauth_token.json"))

    outcome = run_cli(
        "auth", "exchange", "-", "--state", "st8", input=REDIRECT + "\n", **OAUTH_ENV
    )

    assert outcome.exit_code == 0


@pytest.mark.covers("cli:auth exchange")
def test_exchange_state_mismatch_exits_3(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(TOKEN_URL)

    outcome = run_cli("auth", "exchange", REDIRECT, "--state", "other", **OAUTH_ENV)

    assert outcome.exit_code == 3
    assert outcome.error()["type"] == "OAuthStateMismatchError"
    assert not route.called


@pytest.mark.covers("cli:auth exchange")
def test_exchange_without_access_point_exits_2(run_cli: CliHarness) -> None:
    outcome = run_cli(
        "auth", "exchange", f"{REDIRECT_URI}?code=c&state=s", "--state", "s",
        ADOBESIGN_BASE_URI=None, **OAUTH_ENV,
    )  # fmt: skip

    assert outcome.exit_code == 2
    assert "ADOBESIGN_BASE_URI" in outcome.error()["message"]


@pytest.mark.covers("cli:auth exchange")
def test_exchange_dry_run_redacts_and_saves_nothing(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(TOKEN_URL)

    outcome = run_cli(
        "auth", "exchange", REDIRECT, "--state", "st8", "--dry-run", **OAUTH_ENV
    )

    assert outcome.exit_code == 0
    assert not route.called
    assert not config_path(run_cli).exists()
    form = outcome.json()["requests"][0]["body"]["form"]
    assert form["code"] == "***REDACTED***"
    assert form["client_secret"] == "***REDACTED***"
    assert form["grant_type"] == "authorization_code"
    assert_no_secrets(outcome.stdout)


@pytest.mark.covers("cli:auth refresh")
def test_refresh_persists_tokens_from_config(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    path = write_tokens(run_cli)
    mock_api.post(REFRESH_URL).mock(return_value=json_response("oauth_refresh.json"))

    outcome = run_cli("auth", "refresh", **OAUTH_ENV)

    assert outcome.exit_code == 0, outcome.stderr
    assert outcome.json()["persisted"] is True
    saved = read_config_file(path)
    assert saved["tokens"]["access_token"] == "3AAABLblqZhBexampleRefreshedAccessToken"
    assert saved["tokens"]["refresh_token"] == "stored-refresh"
    assert saved["client_id"] == CLIENT_ID
    assert_no_secrets(outcome.stdout)


@pytest.mark.covers("cli:auth refresh")
def test_refresh_with_env_refresh_token_is_not_persisted(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(REFRESH_URL).mock(
        return_value=json_response("oauth_refresh.json")
    )

    outcome = run_cli(
        "auth", "refresh", ADOBESIGN_REFRESH_TOKEN="stored-refresh", **OAUTH_ENV
    )

    assert outcome.exit_code == 0
    assert outcome.json()["persisted"] is False
    assert "refresh_token=stored-refresh" in route.calls.last.request.content.decode()
    assert not config_path(run_cli).exists()


@pytest.mark.covers("cli:auth refresh")
def test_refresh_dry_run_redacts_secrets(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    path = write_tokens(run_cli)
    before = path.read_text()
    route = mock_api.post(REFRESH_URL)

    outcome = run_cli("auth", "refresh", "--dry-run", **OAUTH_ENV)

    assert outcome.exit_code == 0
    assert not route.called
    assert path.read_text() == before
    form = outcome.json()["requests"][0]["body"]["form"]
    assert form["refresh_token"] == "***REDACTED***"
    assert_no_secrets(outcome.stdout)


@pytest.mark.covers("cli:auth refresh")
def test_refresh_with_integration_key_exits_3(run_cli: CliHarness) -> None:
    outcome = run_cli("auth", "refresh")

    assert outcome.exit_code == 3
    assert "Integration keys do not expire" in outcome.error()["message"]


@pytest.mark.covers("cli:auth whoami")
def test_whoami_reports_sources_not_values(run_cli: CliHarness) -> None:
    outcome = run_cli("auth", "whoami")

    document = outcome.json()
    assert document["auth_method"] == "integration_key"
    assert document["sources"]["integration_key"] == "env"
    assert document["config_exists"] is False
    assert_no_secrets(outcome.stdout)


@pytest.mark.covers("cli:auth whoami")
def test_whoami_with_config_tokens(run_cli: CliHarness) -> None:
    write_tokens(run_cli)

    outcome = run_cli("auth", "whoami", ADOBESIGN_INTEGRATION_KEY=None)

    document = outcome.json()
    assert document["auth_method"] == "oauth"
    assert document["sources"]["client_id"] == "config"
    assert document["tokens"]["has_refresh_token"] is True
    assert "stored-access" not in outcome.stdout
    assert_no_secrets(outcome.stdout)


@pytest.mark.covers("cli:auth whoami")
def test_whoami_with_nothing_configured(run_cli: CliHarness) -> None:
    outcome = run_cli("auth", "whoami", ADOBESIGN_INTEGRATION_KEY=None)

    assert outcome.json()["auth_method"] is None
    assert outcome.json()["tokens"] is None


@pytest.mark.covers("cli:auth base-uris")
def test_base_uris(run_cli: CliHarness, mock_api: respx.MockRouter) -> None:
    route = mock_api.get("https://api.adobesign.com/api/rest/v6/baseUris").mock(
        return_value=json_response("base_uris.json")
    )

    outcome = run_cli("auth", "base-uris")

    assert outcome.exit_code == 0
    assert outcome.json()["apiAccessPoint"] == CLI_ACCESS_POINT
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {CLI_KEY}"


def test_oauth_from_config_auto_refreshes_and_persists(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    path = write_tokens(run_cli, expires_at="2000-01-01T00:00:00Z")
    mock_api.post(REFRESH_URL).mock(return_value=json_response("oauth_refresh.json"))
    agreement = mock_api.get(
        "https://api.eu1.adobesign.com/api/rest/v6/agreements/A"
    ).mock(return_value=json_response("agreement.json"))

    outcome = run_cli("agreements", "get", "A", **OAUTH_ENV)

    assert outcome.exit_code == 0, outcome.stderr
    assert agreement.calls.last.request.headers["Authorization"] == (
        "Bearer 3AAABLblqZhBexampleRefreshedAccessToken"
    )
    assert read_config_file(path)["tokens"]["access_token"] == (
        "3AAABLblqZhBexampleRefreshedAccessToken"
    )


def test_oauth_env_refresh_token_requires_base_uri(run_cli: CliHarness) -> None:
    outcome = run_cli(
        "agreements", "get", "A", ADOBESIGN_REFRESH_TOKEN="r", ADOBESIGN_BASE_URI=None,
        **OAUTH_ENV,
    )  # fmt: skip

    assert outcome.exit_code == 3
    assert "ADOBESIGN_BASE_URI" in outcome.error()["message"]


def test_missing_credentials_exit_3(run_cli: CliHarness) -> None:
    outcome = run_cli("agreements", "get", "A", ADOBESIGN_INTEGRATION_KEY=None)

    assert outcome.exit_code == 3
    assert "No credentials" in outcome.error()["message"]


def test_config_file_settings_are_used(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    write_config_file(
        config_path(run_cli),
        {"integration_key": "file-key", "base_uri": "https://api.na2.adobesign.com/"},
    )
    route = mock_api.get("https://api.na2.adobesign.com/api/rest/v6/agreements/A").mock(
        return_value=json_response("agreement.json")
    )

    outcome = run_cli(
        "agreements",
        "get",
        "A",
        ADOBESIGN_INTEGRATION_KEY=None,
        ADOBESIGN_BASE_URI=None,
    )

    assert outcome.exit_code == 0
    assert route.calls.last.request.headers["Authorization"] == "Bearer file-key"


def test_world_readable_config_warns(run_cli: CliHarness) -> None:
    path = config_path(run_cli)
    write_config_file(path, {"base_uri": CLI_ACCESS_POINT})
    os.chmod(path, 0o644)

    outcome = run_cli("auth", "whoami")

    assert "chmod 600" in outcome.stderr


def test_invalid_config_toml_exits_3(run_cli: CliHarness) -> None:
    config_path(run_cli).write_text("this is = = not toml")

    outcome = run_cli("auth", "whoami")

    assert outcome.exit_code == 3
    assert "not valid TOML" in outcome.error()["message"]


def test_explicit_config_option(run_cli: CliHarness, tmp_path: Path) -> None:
    other = tmp_path / "other.toml"
    write_config_file(other, {"base_uri": "https://api.na3.adobesign.com/"})

    outcome = run_cli("--config", str(other), "auth", "whoami", ADOBESIGN_BASE_URI=None)

    assert outcome.json()["base_uri"] == "https://api.na3.adobesign.com/"
    assert outcome.json()["config_path"] == str(other)


def test_unauthorised_api_call_exits_3_with_error_document(
    run_cli: CliHarness, mock_api: respx.MockRouter
) -> None:
    mock_api.get("https://api.eu1.adobesign.com/api/rest/v6/agreements/A").mock(
        return_value=httpx.Response(
            401, json={"code": "INVALID_ACCESS_TOKEN", "message": "Token invalid"}
        )
    )

    outcome = run_cli("agreements", "get", "A")

    assert outcome.exit_code == 3
    error = outcome.error()
    assert error["type"] == "AuthenticationError"
    assert error["code"] == "INVALID_ACCESS_TOKEN"
    assert_no_secrets(outcome.stderr)

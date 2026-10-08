"""Shared fixtures: a client pointed at a fixed access point, plus respx mocking.

No test in this directory (outside ``tests/live``) touches the network:
``respx`` intercepts every httpx request, and an unmatched request fails.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import respx

from adobesign import AdobeSignClient
from adobesign import IntegrationKey
from adobesign import OAuthApp

from cli_helpers import ALL_CLI_ENV
from cli_helpers import CLI_ACCESS_POINT
from cli_helpers import CLI_KEY
from cli_helpers import CliHarness
from helpers import ACCESS_POINT
from helpers import CLIENT_ID
from helpers import CLIENT_SECRET
from helpers import INTEGRATION_KEY
from helpers import REDIRECT_URI
from helpers import SleepRecorder
from helpers import deterministic_retry_policy


@pytest.fixture
def sleeps() -> SleepRecorder:
    return SleepRecorder()


@pytest.fixture
def mock_api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def client(
    sleeps: SleepRecorder, mock_api: respx.MockRouter
) -> Iterator[AdobeSignClient]:
    with AdobeSignClient(
        IntegrationKey(INTEGRATION_KEY),
        api_access_point=ACCESS_POINT,
        retry=deterministic_retry_policy(),
        sleep=sleeps,
    ) as api_client:
        yield api_client


@pytest.fixture
def oauth_app(sleeps: SleepRecorder, mock_api: respx.MockRouter) -> Iterator[OAuthApp]:
    app = OAuthApp(
        CLIENT_ID,
        CLIENT_SECRET,
        REDIRECT_URI,
        retry=deterministic_retry_policy(),
        sleep=sleeps,
    )
    yield app
    app.close()


@pytest.fixture
def run_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_api: respx.MockRouter
) -> CliHarness:
    """CLI runner with an integration key, a fixed base URI and no retries."""
    for name in ALL_CLI_ENV:
        monkeypatch.delenv(name, raising=False)
    return CliHarness(
        {
            "ADOBESIGN_CONFIG": str(tmp_path / "config.toml"),
            "ADOBESIGN_INTEGRATION_KEY": CLI_KEY,
            "ADOBESIGN_BASE_URI": CLI_ACCESS_POINT,
            "ADOBESIGN_MAX_RETRIES": "0",
        }
    )

"""Constants and helpers shared by the mocked test modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from adobesign import RetryPolicy

FIXTURES = Path(__file__).parent / "fixtures"
ACCESS_POINT = "https://api.eu1.adobesign.com/"
API = f"{ACCESS_POINT}api/rest/v6"
INTEGRATION_KEY = "3AAABLblqZhTestIntegrationKey"
CLIENT_ID = "CBJCHBCAABAAtestClientId"
CLIENT_SECRET = "test-client-secret"
REDIRECT_URI = "https://app.example.com/adobesign/callback"
MAX_RETRIES = 2


def load_fixture(name: str) -> Any:
    """The parsed JSON content of ``tests/fixtures/<name>``."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def json_response(name: str, status_code: int = 200) -> httpx.Response:
    """A JSON response whose body is the named fixture."""
    return httpx.Response(status_code, json=load_fixture(name))


class SleepRecorder:
    """Stands in for ``time.sleep`` and records each requested delay."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def deterministic_retry_policy() -> RetryPolicy:
    """Retry policy used by every test client: no jitter, two retries."""
    return RetryPolicy(max_retries=MAX_RETRIES, backoff_factor=0.5, jitter=False)


def assert_bearer(request: httpx.Request, token: str = INTEGRATION_KEY) -> None:
    """The request carried ``Authorization: Bearer <token>``."""
    assert request.headers["Authorization"] == f"Bearer {token}"

"""Helpers for driving the ``adobesign`` CLI in tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from click.testing import CliRunner
from click.testing import Result

from adobesign.cli._config import CONFIG_ENV
from adobesign.cli._config import SETTING_ENV
from adobesign.cli.app import MAX_RETRIES_ENV
from adobesign.cli.app import cli

CLI_KEY = "3AAABLblqZhCliSecretIntegrationKey"
CLI_ACCESS_POINT = "https://api.eu1.adobesign.com/"
CLI_API = f"{CLI_ACCESS_POINT}api/rest/v6"
ALL_CLI_ENV = [*SETTING_ENV.values(), CONFIG_ENV, MAX_RETRIES_ENV]


@dataclass
class CliOutcome:
    """A finished CLI run with its parsed stdout / stderr documents."""

    result: Result

    @property
    def exit_code(self) -> int:
        return self.result.exit_code

    @property
    def stdout(self) -> str:
        return self.result.stdout

    @property
    def stderr(self) -> str:
        return self.result.stderr

    def json(self) -> Any:
        return json.loads(self.result.stdout)

    def error(self) -> dict[str, Any]:
        document = json.loads(self.result.stderr[self.result.stderr.index("{\n") :])
        error: dict[str, Any] = document["error"]
        return error


class CliHarness:
    """Runs CLI commands with a controlled environment."""

    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self.runner = CliRunner()

    def __call__(
        self, *args: str, input: str | None = None, **env: str | None
    ) -> CliOutcome:
        merged = {**self.env, **env}
        result = self.runner.invoke(cli, list(args), env=merged, input=input)
        if result.exception and not isinstance(result.exception, SystemExit):
            raise result.exception
        return CliOutcome(result)

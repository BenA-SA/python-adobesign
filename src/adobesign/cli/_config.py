"""Where the CLI gets credentials: environment variables, then a config file.

Credentials are never accepted as command-line flags (they would land in
shell history and process listings). Sources, highest priority first:

* environment: ``ADOBESIGN_INTEGRATION_KEY``; or ``ADOBESIGN_CLIENT_ID`` +
  ``ADOBESIGN_CLIENT_SECRET`` (+ ``ADOBESIGN_REDIRECT_URI``) with
  ``ADOBESIGN_REFRESH_TOKEN``; ``ADOBESIGN_BASE_URI`` for the account's API
  access point;
* the TOML config file (``$ADOBESIGN_CONFIG`` or
  ``~/.config/adobesign/config.toml``), whose ``[tokens]`` table holds the
  OAuth tokens written by ``adobesign auth exchange`` and kept current by
  every refresh.

The file is created with mode 0600; a warning is printed if it is readable by
other users.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

import click

from adobesign.auth import InMemoryTokenStore
from adobesign.auth import IntegrationKey
from adobesign.auth import OAuthApp
from adobesign.auth import OAuthCredentials
from adobesign.auth import TokenSet
from adobesign.cli._output import credentials_error
from adobesign.client import Credentials

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

CONFIG_ENV = "ADOBESIGN_CONFIG"
SETTING_ENV = {
    "integration_key": "ADOBESIGN_INTEGRATION_KEY",
    "client_id": "ADOBESIGN_CLIENT_ID",
    "client_secret": "ADOBESIGN_CLIENT_SECRET",
    "redirect_uri": "ADOBESIGN_REDIRECT_URI",
    "refresh_token": "ADOBESIGN_REFRESH_TOKEN",
    "base_uri": "ADOBESIGN_BASE_URI",
}
FILE_SETTINGS = (
    "integration_key",
    "client_id",
    "client_secret",
    "redirect_uri",
    "base_uri",
)
EXPIRED = datetime(1970, 1, 1, tzinfo=timezone.utc)


def default_config_path(environ: Mapping[str, str]) -> Path:
    if environ.get(CONFIG_ENV):
        return Path(environ[CONFIG_ENV]).expanduser()
    config_home = environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "adobesign" / "config.toml"


def read_config_file(path: Path) -> dict[str, Any]:
    """Parse the config file, or ``{}`` when it does not exist."""
    if not path.exists():
        return {}
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        click.echo(
            json.dumps(
                {"warning": f"{path} is accessible by other users; run chmod 600"}
            ),
            err=True,
        )
    try:
        parsed: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
        return parsed
    except tomllib.TOMLDecodeError as exc:
        raise credentials_error(f"Config file {path} is not valid TOML: {exc}") from exc


def _toml_value(value: str) -> str:
    """A TOML basic string (JSON string escaping is a valid subset)."""
    return json.dumps(value, ensure_ascii=False)


def write_config_file(path: Path, data: Mapping[str, Any]) -> None:
    """Write ``data`` (string values, one optional table level) with mode 0600."""
    lines = [
        f"{key} = {_toml_value(str(value))}"
        for key, value in data.items()
        if not isinstance(value, Mapping) and value is not None
    ]
    for table, values in data.items():
        if not isinstance(values, Mapping):
            continue
        lines.append(f"\n[{table}]")
        lines.extend(
            f"{key} = {_toml_value(str(value))}"
            for key, value in values.items()
            if value is not None
        )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.replace(temporary, path)
    os.chmod(path, 0o600)


class ConfigFileTokenStore:
    """Persists OAuth tokens in the config file's ``[tokens]`` table."""

    def __init__(self, path: Path, tokens: TokenSet) -> None:
        self.path = path
        self._tokens = tokens

    def load(self) -> TokenSet:
        return self._tokens

    def save(self, tokens: TokenSet) -> None:
        self._tokens = tokens
        data = read_config_file(self.path)
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        write_config_file(self.path, data)


@dataclass
class Settings:
    """Resolved settings plus where each one came from (never the values)."""

    path: Path
    values: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    file_tokens: TokenSet | None = None

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def require(self, *names: str) -> list[str]:
        missing = [name for name in names if not self.values.get(name)]
        if missing:
            env_names = ", ".join(SETTING_ENV[name] for name in missing)
            raise credentials_error(
                f"Missing {env_names} (set the environment variable or the "
                f"matching key in {self.path})"
            )
        return [self.values[name] for name in names]


def load_settings(path: Path | None, environ: Mapping[str, str]) -> Settings:
    """Merge the config file and environment (environment wins)."""
    settings = Settings(path=path or default_config_path(environ))
    file_data = read_config_file(settings.path)
    for name in FILE_SETTINGS:
        value = file_data.get(name)
        if isinstance(value, str) and value:
            settings.values[name] = value
            settings.sources[name] = "config"
    for name, env_name in SETTING_ENV.items():
        value = environ.get(env_name)
        if value:
            settings.values[name] = value
            settings.sources[name] = "env"
    tokens = file_data.get("tokens")
    if isinstance(tokens, dict) and tokens.get("access_token"):
        settings.file_tokens = TokenSet.model_validate(tokens)
    return settings


def build_oauth_app(settings: Settings, **kwargs: Any) -> OAuthApp:
    client_id, client_secret = settings.require("client_id", "client_secret")
    redirect_uri = settings.get("redirect_uri") or "https://localhost/callback"
    return OAuthApp(client_id, client_secret, redirect_uri, **kwargs)


def build_credentials(settings: Settings, **app_kwargs: Any) -> Credentials:
    """Integration key if configured, else OAuth (env refresh token or file)."""
    integration_key = settings.get("integration_key")
    if integration_key:
        return IntegrationKey(integration_key)
    env_refresh_token = settings.sources.get("refresh_token") == "env"
    if not env_refresh_token and settings.file_tokens is None:
        raise credentials_error(
            "No credentials: set ADOBESIGN_INTEGRATION_KEY, or OAuth settings "
            "(ADOBESIGN_CLIENT_ID, ADOBESIGN_CLIENT_SECRET and "
            "ADOBESIGN_REFRESH_TOKEN), or run 'adobesign auth exchange'."
        )
    app = build_oauth_app(settings, **app_kwargs)
    if env_refresh_token:
        (base_uri,) = settings.require("base_uri")
        tokens = TokenSet(
            access_token="",
            refresh_token=settings.values["refresh_token"],
            expires_at=EXPIRED,
            api_access_point=base_uri,
        )
        return OAuthCredentials(app, InMemoryTokenStore(tokens))
    assert settings.file_tokens is not None
    file_tokens = settings.file_tokens
    if file_tokens.api_access_point is None and settings.get("base_uri"):
        file_tokens = file_tokens.model_copy(
            update={"api_access_point": settings.get("base_uri")}
        )
    return OAuthCredentials(app, ConfigFileTokenStore(settings.path, file_tokens))

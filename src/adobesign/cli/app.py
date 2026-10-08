"""Click command tree for the ``adobesign`` CLI.

Every leaf command follows the same contract (see :mod:`._output`): JSON on
stdout, ``{"error": ...}`` on stderr, documented exit codes. Mutating
commands take ``--dry-run``; commands that make Acrobat Sign email or call
someone also need ``--yes`` (or an interactive confirmation).
"""

from __future__ import annotations

import os
import re
import secrets
import sys
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from itertools import islice
from pathlib import Path
from typing import Any
from typing import TypeVar

import click

from adobesign import __version__
from adobesign import errors
from adobesign._transport import RetryPolicy
from adobesign.auth import OAuthCredentials
from adobesign.cli._config import Settings
from adobesign.cli._config import build_credentials
from adobesign.cli._config import build_oauth_app
from adobesign.cli._config import load_settings
from adobesign.cli._config import read_config_file
from adobesign.cli._config import write_config_file
from adobesign.cli._http import DryRunCredentials
from adobesign.cli._http import DryRunTransport
from adobesign.cli._http import build_http_client
from adobesign.cli._http import token_summary
from adobesign.cli._output import EXIT_CODE_HELP
from adobesign.cli._output import FORMATS
from adobesign.cli._output import CliError
from adobesign.cli._output import ExitCode
from adobesign.cli._output import credentials_error
from adobesign.cli._output import emit
from adobesign.cli._output import fail
from adobesign.cli._output import usage_error
from adobesign.client import AdobeSignClient
from adobesign.client import Credentials
from adobesign.enums import AgreementCreateState
from adobesign.enums import WebhookEvent
from adobesign.enums import WebhookResourceType
from adobesign.enums import WebhookScope
from adobesign.models import AgreementCreate
from adobesign.models import CcInfo
from adobesign.models import ExternalId
from adobesign.models import FileInfo
from adobesign.models import MemberInfo
from adobesign.models import ParticipantSetInfo
from adobesign.models import WebhookCreate
from adobesign.models import WebhookUrlInfo
from adobesign.notifications import parse_notification
from adobesign.notifications import verify_webhook_request

FuncT = TypeVar("FuncT", bound=Callable[..., Any])

DEFAULT_SCOPES = (
    "user_login:self",
    "agreement_read:account",
    "agreement_write:account",
    "agreement_send:account",
    "webhook_read:account",
    "webhook_write:account",
)
SIGNER_PATTERN = re.compile(
    r"^\s*(?:(?P<name>[^<>]*?)\s*<(?P<email>[^<>\s]+@[^<>\s]+)>|(?P<bare>[^<>\s]+@[^<>\s]+))\s*$"
)
PENDING_STATUS_PREFIX = "WAITING_FOR_MY_"
DRY_RUN_PENDING_PARTICIPANT = "<pending participant ids from GET .../members>"


def is_interactive() -> bool:
    """True when a human can answer a confirmation prompt."""
    return sys.stdin.isatty()


@dataclass(frozen=True)
class Invocation:
    """Per-command options shared by every leaf command."""

    output_format: str
    verbose: bool
    dry_run: bool = False


def _epilog(emails: str) -> str:
    return f"Emails or calls anyone: {emails}\n\n{EXIT_CODE_HELP}"


def leaf(
    group: click.Group, name: str, *, emails: str = "no"
) -> Callable[[FuncT], FuncT]:
    """Register a leaf command with the shared --format/--verbose options."""

    def decorate(func: FuncT) -> FuncT:
        func = click.option(
            "--verbose",
            is_flag=True,
            help="Log each HTTP request/response to stderr (credentials redacted).",
        )(func)
        func = click.option(
            "--format",
            "output_format",
            type=click.Choice(FORMATS),
            default="json",
            show_default=True,
            help="json for machines (stable keys), table for humans.",
        )(func)
        group.command(name, epilog=_epilog(emails))(func)
        return func

    return decorate


def dry_run_option(func: FuncT) -> FuncT:
    return click.option(
        "--dry-run",
        is_flag=True,
        help="Print the exact HTTP request(s) that would be sent; send nothing.",
    )(func)


def yes_option(func: FuncT) -> FuncT:
    return click.option(
        "--yes",
        is_flag=True,
        help="Confirm this outward-facing action (required when not interactive).",
    )(func)


def confirm_outward(description: str, *, yes: bool, dry_run: bool) -> None:
    """Refuse an outward-facing action unless confirmed."""
    if dry_run or yes:
        return
    if not is_interactive():
        raise CliError(
            f"Refusing to run without --yes: {description}. Re-run with "
            "--dry-run to inspect the request, then with --yes to confirm.",
            exit_code=ExitCode.USAGE,
            error_type="ConfirmationRequired",
        )
    if not click.confirm(f"{description}. Continue?", err=True):
        raise CliError(
            "Cancelled at the confirmation prompt.",
            exit_code=ExitCode.USAGE,
            error_type="Cancelled",
        )


MAX_RETRIES_ENV = "ADOBESIGN_MAX_RETRIES"


def retry_policy() -> RetryPolicy:
    """Retry policy for CLI calls; ``ADOBESIGN_MAX_RETRIES=0`` disables retries."""
    raw = os.environ.get(MAX_RETRIES_ENV)
    if raw is None:
        return RetryPolicy()
    if not raw.isdigit():
        raise usage_error(f"{MAX_RETRIES_ENV} must be a non-negative integer.")
    return RetryPolicy(max_retries=int(raw))


def _settings() -> Settings:
    context = click.get_current_context()
    config_path = context.find_root().params.get("config_path")
    return load_settings(config_path, os.environ)


def _dry_run_access_point(settings: Settings) -> str | None:
    if settings.get("base_uri"):
        return settings.get("base_uri")
    tokens = settings.file_tokens
    return tokens.api_access_point if tokens else None


@contextmanager
def _client(
    settings: Settings, invocation: Invocation
) -> Iterator[tuple[AdobeSignClient, DryRunTransport | None]]:
    transport = DryRunTransport() if invocation.dry_run else None
    http = build_http_client(dry_run=transport, verbose=invocation.verbose)
    credentials: Credentials
    if transport is not None:
        credentials = DryRunCredentials(_dry_run_access_point(settings))
    else:
        credentials = build_credentials(
            settings, http_client=http, retry=retry_policy()
        )
    try:
        yield (
            AdobeSignClient(
                credentials,
                api_access_point=settings.get("base_uri"),
                http_client=http,
                retry=retry_policy(),
            ),
            transport,
        )
    finally:
        http.close()


def execute(invocation: Invocation, action: Callable[[AdobeSignClient], Any]) -> None:
    """Run ``action`` with a configured client and emit its result or error."""
    try:
        settings = _settings()
        with _client(settings, invocation) as (client, transport):
            result = action(client)
            if transport is not None:
                result = {"dry_run": True, "requests": transport.requests}
        emit(result, invocation.output_format)
    except (errors.AdobeSignError, CliError) as exc:
        fail(exc)


def run_local(invocation: Invocation, action: Callable[[], Any]) -> None:
    """Run an action that needs no API client; same output contract."""
    try:
        emit(action(), invocation.output_format)
    except (errors.AdobeSignError, CliError) as exc:
        fail(exc)


class JsonErrorGroup(click.Group):
    """A group whose click usage errors follow the JSON error contract."""

    def main(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["standalone_mode"] = False
        try:
            result = super().main(*args, **kwargs)
        except click.ClickException as exc:
            fail(usage_error(exc.format_message()))
        except click.Abort:
            fail(CliError("Aborted.", exit_code=ExitCode.USAGE, error_type="Aborted"))
        raise SystemExit(result if isinstance(result, int) else 0)


@click.group(
    cls=JsonErrorGroup,
    epilog=EXIT_CODE_HELP + "\n\nCredentials come only from environment variables "
    "(ADOBESIGN_INTEGRATION_KEY, or ADOBESIGN_CLIENT_ID / "
    "ADOBESIGN_CLIENT_SECRET / ADOBESIGN_REFRESH_TOKEN, plus "
    "ADOBESIGN_BASE_URI) or the config file "
    "(~/.config/adobesign/config.toml, or $ADOBESIGN_CONFIG). "
    "Throttled (429) and failed idempotent calls are retried up to 3 times; "
    "set ADOBESIGN_MAX_RETRIES=0 to handle retries yourself.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Config file path (default: $ADOBESIGN_CONFIG or "
    "~/.config/adobesign/config.toml).",
)
@click.version_option(__version__, prog_name="adobesign")
def cli(config_path: Path | None) -> None:
    """Unofficial CLI for the Adobe Acrobat Sign REST API v6 (not an Adobe SDK).

    Prints JSON to stdout; errors are JSON on stderr. Commands that email or
    call anyone require --yes when not run interactively, and every mutating
    command accepts --dry-run.
    """


@cli.group()
def auth() -> None:
    """OAuth login, token refresh and credential checks."""


@cli.group()
def documents() -> None:
    """Upload documents for use in agreements."""


@cli.group()
def agreements() -> None:
    """Send, track, remind, cancel and download agreements."""


@cli.group()
def webhooks() -> None:
    """Manage webhooks and answer Acrobat Sign's verification handshake."""


@cli.group()
def notifications() -> None:
    """Parse webhook notification bodies."""


@leaf(auth, "login-url")
@click.option(
    "--scope",
    "scopes",
    multiple=True,
    help="OAuth scope (repeatable). Default: login, agreement read/write/send, "
    "webhook read/write at account level.",
)
@click.option("--state", help="CSRF state to embed (default: a random value).")
def auth_login_url(
    scopes: tuple[str, ...], state: str | None, output_format: str, verbose: bool
) -> None:
    """Print the URL a user opens to grant this app access.

    Needs ADOBESIGN_CLIENT_ID and ADOBESIGN_REDIRECT_URI (env or config).
    Keep the printed state; pass it to 'auth exchange'.

    \b
    Example:
      adobesign auth login-url
      adobesign auth login-url --scope user_login:self --scope agreement_read:self
    """

    def action() -> dict[str, str]:
        settings = _settings()
        settings.require("client_id", "redirect_uri")
        app = build_oauth_app(_with_placeholder_secret(settings))
        chosen_state = state or secrets.token_urlsafe(24)
        try:
            url = app.authorization_url(scopes or DEFAULT_SCOPES, state=chosen_state)
        finally:
            app.close()
        return {"url": url, "state": chosen_state, "redirect_uri": app.redirect_uri}

    run_local(Invocation(output_format, verbose), action)


def _with_placeholder_secret(settings: Settings) -> Settings:
    """Building an authorisation URL needs no client secret."""
    values = {"client_secret": "unused", **settings.values}
    return Settings(path=settings.path, values=values, sources=settings.sources)


def _read_redirect(redirect_url: str) -> str:
    if redirect_url != "-":
        return redirect_url
    return sys.stdin.read().strip()


@leaf(auth, "exchange")
@click.argument("redirect_url")
@click.option("--state", required=True, help="The state printed by 'auth login-url'.")
@dry_run_option
def auth_exchange(
    redirect_url: str,
    state: str,
    dry_run: bool,
    output_format: str,
    verbose: bool,
) -> None:
    """Swap the OAuth redirect for tokens and save them to the config file.

    REDIRECT_URL is the full URL the browser was redirected to (use '-' to
    read it from stdin, which keeps the one-time code out of shell history).
    Tokens are written to the config file with mode 0600 and never printed.

    \b
    Example:
      pbpaste | adobesign auth exchange - --state "$STATE"
    """
    invocation = Invocation(output_format, verbose, dry_run)

    def action() -> dict[str, Any]:
        settings = _settings()
        transport = DryRunTransport() if dry_run else None
        http = build_http_client(dry_run=transport, verbose=verbose)
        app = build_oauth_app(settings, http_client=http, retry=retry_policy())
        try:
            redirect = app.parse_redirect(
                _read_redirect(redirect_url), expected_state=state
            )
            access_point = redirect.api_access_point or settings.get("base_uri")
            if not access_point:
                raise usage_error(
                    "The redirect has no api_access_point; set ADOBESIGN_BASE_URI."
                )
            tokens = app.exchange_code(redirect.code, api_access_point=access_point)
        finally:
            http.close()
        if transport is not None:
            return {"dry_run": True, "requests": transport.requests}
        data = read_config_file(settings.path)
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        write_config_file(settings.path, data)
        return {"saved_to": str(settings.path), **token_summary(tokens)}

    run_local(invocation, action)


@leaf(auth, "refresh")
@dry_run_option
def auth_refresh(dry_run: bool, output_format: str, verbose: bool) -> None:
    """Refresh the OAuth access token now and persist it.

    Tokens from the config file are written back to it; a refresh token
    supplied via ADOBESIGN_REFRESH_TOKEN is refreshed but not persisted.

    \b
    Example:
      adobesign auth refresh
    """

    def action() -> dict[str, Any]:
        settings = _settings()
        transport = DryRunTransport() if dry_run else None
        http = build_http_client(dry_run=transport, verbose=verbose)
        try:
            credentials = build_credentials(
                settings, http_client=http, retry=retry_policy()
            )
            if not isinstance(credentials, OAuthCredentials):
                raise credentials_error(
                    "Integration keys do not expire; there is nothing to refresh."
                )
            if transport is not None:
                credentials.app.refresh(credentials.store.load())
                return {"dry_run": True, "requests": transport.requests}
            tokens = credentials.refresh()
        finally:
            http.close()
        persisted = settings.sources.get("refresh_token") != "env"
        return {"persisted": persisted, **token_summary(tokens)}

    run_local(Invocation(output_format, verbose, dry_run), action)


@leaf(auth, "whoami")
def auth_whoami(output_format: str, verbose: bool) -> None:
    """Show which credentials and settings are in use (never their values).

    Makes no network call. Use 'auth base-uris' to test the credentials.

    \b
    Example:
      adobesign auth whoami
    """

    def action() -> dict[str, Any]:
        settings = _settings()
        method = None
        if settings.get("integration_key"):
            method = "integration_key"
        elif settings.get("refresh_token") or settings.file_tokens:
            method = "oauth"
        return {
            "auth_method": method,
            "config_path": str(settings.path),
            "config_exists": settings.path.exists(),
            "sources": dict(sorted(settings.sources.items())),
            "base_uri": settings.get("base_uri"),
            "tokens": token_summary(settings.file_tokens)
            if settings.file_tokens
            else None,
        }

    run_local(Invocation(output_format, verbose), action)


@leaf(auth, "base-uris")
def auth_base_uris(output_format: str, verbose: bool) -> None:
    """Discover the account's regional API and web access points.

    A cheap way to check that the credentials work (GET /baseUris).

    \b
    Example:
      adobesign auth base-uris
    """
    execute(Invocation(output_format, verbose), lambda c: c.discover_base_uris())


@leaf(documents, "upload")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--mime-type", default="application/pdf", show_default=True)
@dry_run_option
def documents_upload(
    path: Path, mime_type: str, dry_run: bool, output_format: str, verbose: bool
) -> None:
    """Upload a file as a transient document (valid for 7 days).

    Prints the transientDocumentId to pass to 'agreements send --document-id'.

    \b
    Example:
      adobesign documents upload contract.pdf
    """
    execute(
        Invocation(output_format, verbose, dry_run),
        lambda c: c.transient_documents.upload(path, mime_type=mime_type),
    )


def parse_signer(value: str) -> MemberInfo:
    """``"Name <email>"`` or ``"email"`` → :class:`MemberInfo`."""
    match = SIGNER_PATTERN.match(value)
    if match is None:
        raise usage_error(f"Invalid --signer {value!r}; use 'Name <email>' or 'email'.")
    if match.group("bare"):
        return MemberInfo(email=match.group("bare"))
    return MemberInfo(email=match.group("email"), name=match.group("name") or None)


def _participant_sets(signers: tuple[str, ...], order: str) -> list[ParticipantSetInfo]:
    if not signers:
        raise usage_error("At least one --signer is required.")
    members = [parse_signer(value) for value in signers]
    return [
        ParticipantSetInfo(
            member_infos=[member],
            order=position if order == "sequential" else 1,
            role="SIGNER",
        )
        for position, member in enumerate(members, start=1)
    ]


@dataclass(frozen=True)
class AgreementRequest:
    """Inputs shared by 'agreements send' and 'agreements draft'."""

    files: tuple[Path, ...]
    document_ids: tuple[str, ...]
    signers: tuple[str, ...]
    order: str
    name: str
    message: str | None
    ccs: tuple[str, ...]
    external_id: str | None

    def build(self, client: AdobeSignClient, state: AgreementCreateState) -> Any:
        participant_sets = _participant_sets(self.signers, self.order)
        if not (self.files or self.document_ids):
            raise usage_error("Give at least one --file or --document-id.")
        uploaded = [
            client.transient_documents.upload(path).transient_document_id
            for path in self.files
        ]
        agreement = AgreementCreate(
            name=self.name,
            file_infos=[
                FileInfo(transient_document_id=document_id)
                for document_id in (*uploaded, *self.document_ids)
            ],
            participant_sets_info=participant_sets,
            state=state,
            message=self.message,
            ccs=[CcInfo(email=email) for email in self.ccs] or None,
            external_id=ExternalId(id=self.external_id) if self.external_id else None,
        )
        return client.agreements.create(agreement)


def agreement_options(func: FuncT) -> FuncT:
    options = [
        click.option(
            "--file",
            "files",
            multiple=True,
            type=click.Path(exists=True, dir_okay=False, path_type=Path),
            help="Document to upload and attach (repeatable).",
        ),
        click.option(
            "--document-id",
            "document_ids",
            multiple=True,
            help="Already-uploaded transientDocumentId (repeatable).",
        ),
        click.option(
            "--signer",
            "signers",
            multiple=True,
            help="'Name <email>' or 'email' (repeatable, in signing order).",
        ),
        click.option(
            "--order",
            type=click.Choice(["sequential", "parallel"]),
            default="sequential",
            show_default=True,
            help="Sign one after another, or all at once.",
        ),
        click.option("--name", required=True, help="Agreement name (email subject)."),
        click.option("--message", help="Message shown to the signers."),
        click.option("--cc", "ccs", multiple=True, help="Email to copy (repeatable)."),
        click.option("--external-id", help="Your own reference for the agreement."),
    ]
    for option in reversed(options):
        func = option(func)
    return func


@leaf(agreements, "send", emails="YES - every signer is emailed immediately")
@agreement_options
@dry_run_option
@yes_option
def agreements_send(
    files: tuple[Path, ...],
    document_ids: tuple[str, ...],
    signers: tuple[str, ...],
    order: str,
    name: str,
    message: str | None,
    ccs: tuple[str, ...],
    external_id: str | None,
    dry_run: bool,
    yes: bool,
    output_format: str,
    verbose: bool,
) -> None:
    """Send documents for signature now (state IN_PROCESS).

    Uploads each --file, then creates the agreement. Acrobat Sign emails the
    first signer(s) straight away. Not retried on server errors, to avoid
    sending twice. Prints the new agreement id.

    \b
    Example:
      adobesign agreements send --file contract.pdf --name "Contract" \\
        --signer "Alice Example <alice@example.com>" \\
        --signer "bob@example.com" --dry-run
    """
    request = AgreementRequest(
        files, document_ids, signers, order, name, message, ccs, external_id
    )
    invocation = Invocation(output_format, verbose, dry_run)
    try:
        recipients = ", ".join(parse_signer(s).email for s in signers) or "nobody"
        confirm_outward(
            f"Send agreement {name!r} to {recipients} (they will be emailed)",
            yes=yes,
            dry_run=dry_run,
        )
    except CliError as exc:
        fail(exc)
    execute(invocation, lambda c: request.build(c, AgreementCreateState.IN_PROCESS))


@leaf(agreements, "draft")
@agreement_options
@dry_run_option
def agreements_draft(
    files: tuple[Path, ...],
    document_ids: tuple[str, ...],
    signers: tuple[str, ...],
    order: str,
    name: str,
    message: str | None,
    ccs: tuple[str, ...],
    external_id: str | None,
    dry_run: bool,
    output_format: str,
    verbose: bool,
) -> None:
    """Create a DRAFT agreement (nobody is emailed until it is sent).

    \b
    Example:
      adobesign agreements draft --file contract.pdf --name "Contract" \\
        --signer "alice@example.com"
    """
    request = AgreementRequest(
        files, document_ids, signers, order, name, message, ccs, external_id
    )
    execute(
        Invocation(output_format, verbose, dry_run),
        lambda c: request.build(c, AgreementCreateState.DRAFT),
    )


@leaf(agreements, "get")
@click.argument("agreement_id")
def agreements_get(agreement_id: str, output_format: str, verbose: bool) -> None:
    """Show an agreement's details and status.

    \b
    Example:
      adobesign agreements get CBJCHBCAABAA...
    """
    execute(
        Invocation(output_format, verbose), lambda c: c.agreements.get(agreement_id)
    )


@leaf(agreements, "list")
@click.option("--limit", type=click.IntRange(min=1), default=50, show_default=True)
@click.option("--page-size", type=click.IntRange(min=1), default=None)
def agreements_list(
    limit: int, page_size: int | None, output_format: str, verbose: bool
) -> None:
    """List the user's agreements, newest first, up to --limit.

    \b
    Example:
      adobesign agreements list --limit 20 --format table
    """
    execute(
        Invocation(output_format, verbose),
        lambda c: list(islice(c.agreements.list(page_size=page_size), limit)),
    )


@leaf(agreements, "members")
@click.argument("agreement_id")
def agreements_members(agreement_id: str, output_format: str, verbose: bool) -> None:
    """Show participants, their status, and who the agreement is waiting on.

    \b
    Example:
      adobesign agreements members CBJCHBCAABAA...
    """
    execute(
        Invocation(output_format, verbose),
        lambda c: c.agreements.get_members(agreement_id),
    )


@leaf(agreements, "events")
@click.argument("agreement_id")
def agreements_events(agreement_id: str, output_format: str, verbose: bool) -> None:
    """Show the agreement's audit events (created, viewed, signed...).

    \b
    Example:
      adobesign agreements events CBJCHBCAABAA... --format table
    """
    execute(
        Invocation(output_format, verbose),
        lambda c: c.agreements.get_events(agreement_id),
    )


@leaf(agreements, "cancel", emails="yes, unless --no-notify")
@click.argument("agreement_id")
@click.option("--comment", help="Reason, shown to participants.")
@click.option(
    "--notify/--no-notify",
    default=True,
    show_default=True,
    help="Email participants about the cancellation.",
)
@dry_run_option
@yes_option
def agreements_cancel(
    agreement_id: str,
    comment: str | None,
    notify: bool,
    dry_run: bool,
    yes: bool,
    output_format: str,
    verbose: bool,
) -> None:
    """Cancel (recall) an agreement that is still out for signature.

    Cannot be undone.

    \b
    Example:
      adobesign agreements cancel CBJCHBCAABAA... --comment "Superseded" --dry-run
    """
    try:
        confirm_outward(
            f"Cancel agreement {agreement_id}"
            + (" and email its participants" if notify else ""),
            yes=yes,
            dry_run=dry_run,
        )
    except CliError as exc:
        fail(exc)

    def action(client: AdobeSignClient) -> dict[str, Any]:
        client.agreements.cancel(agreement_id, comment=comment, notify_others=notify)
        return {"id": agreement_id, "state": "CANCELLED"}

    execute(Invocation(output_format, verbose, dry_run), action)


def _is_waiting_on_own_action(status: object) -> bool:
    """``WAITING_FOR_MY_SIGNATURE`` / ``_APPROVAL`` / ... (enum or raw string)."""
    value = status.value if isinstance(status, Enum) else status
    return isinstance(value, str) and value.startswith(PENDING_STATUS_PREFIX)


def _pending_participant_ids(client: AdobeSignClient, agreement_id: str) -> list[str]:
    """Member ids in participant sets that are waiting on their own action."""
    members = client.agreements.get_members(agreement_id)
    return [
        member.id
        for participant_set in members.participant_sets
        if _is_waiting_on_own_action(participant_set.status)
        for member in participant_set.member_infos
        if member.id
    ]


@leaf(agreements, "remind", emails="YES - each reminded participant is emailed")
@click.argument("agreement_id")
@click.option(
    "--participant",
    "participants",
    multiple=True,
    help="Participant (member) id to remind (repeatable). Default: everyone the "
    "agreement is waiting on.",
)
@click.option("--note", help="Note included in the reminder email.")
@dry_run_option
@yes_option
def agreements_remind(
    agreement_id: str,
    participants: tuple[str, ...],
    note: str | None,
    dry_run: bool,
    yes: bool,
    output_format: str,
    verbose: bool,
) -> None:
    """Email a reminder to participants who have not acted yet.

    \b
    Example:
      adobesign agreements remind CBJCHBCAABAA... --note "Friendly nudge" --dry-run
    """
    try:
        confirm_outward(
            f"Email a reminder for agreement {agreement_id}", yes=yes, dry_run=dry_run
        )
    except CliError as exc:
        fail(exc)

    def action(client: AdobeSignClient) -> Any:
        ids = list(participants) or _pending_participant_ids(client, agreement_id)
        if not ids and dry_run:
            ids = [DRY_RUN_PENDING_PARTICIPANT]
        if not ids:
            raise usage_error("Nobody is waiting to act on this agreement.")
        return client.agreements.send_reminder(agreement_id, ids, note=note)

    execute(Invocation(output_format, verbose, dry_run), action)


@leaf(agreements, "signing-urls")
@click.argument("agreement_id")
@click.option("--frame-parent", help="Parent domain when embedding in an iframe.")
def agreements_signing_urls(
    agreement_id: str, frame_parent: str | None, output_format: str, verbose: bool
) -> None:
    """Show embedded-signing URLs for participants who are due to sign.

    Exits 5 (AGREEMENT_NOT_SIGNABLE) while a new agreement is still being
    processed; wait a few seconds and retry.

    \b
    Example:
      adobesign agreements signing-urls CBJCHBCAABAA...
    """
    execute(
        Invocation(output_format, verbose),
        lambda c: c.agreements.get_signing_urls(
            agreement_id, frame_parent=frame_parent
        ),
    )


@leaf(agreements, "download")
@click.argument("agreement_id")
@click.option(
    "--signed", "kind", flag_value="signed", help="The combined (signed) PDF."
)
@click.option(
    "--audit-trail", "kind", flag_value="audit-trail", help="The audit report."
)
@click.option(
    "-o",
    "--output",
    "output_path",
    required=True,
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    help="Where to write the PDF.",
)
def agreements_download(
    agreement_id: str,
    kind: str | None,
    output_path: Path,
    output_format: str,
    verbose: bool,
) -> None:
    """Download the signed PDF (--signed) or the audit trail (--audit-trail).

    \b
    Example:
      adobesign agreements download CBJCHBCAABAA... --signed -o signed.pdf
      adobesign agreements download CBJCHBCAABAA... --audit-trail -o audit.pdf
    """
    if kind is None:
        fail(usage_error("Choose --signed or --audit-trail."))

    def action(client: AdobeSignClient) -> dict[str, Any]:
        if kind == "signed":
            content = client.agreements.download_combined_document(agreement_id)
        else:
            content = client.agreements.download_audit_trail(agreement_id)
        output_path.write_bytes(content)
        return {"path": str(output_path), "bytes": len(content), "kind": kind}

    execute(Invocation(output_format, verbose), action)


@leaf(webhooks, "create", emails="Acrobat Sign calls the --url to verify it")
@click.option("--name", required=True, help="Webhook name.")
@click.option("--url", required=True, help="HTTPS URL that receives notifications.")
@click.option(
    "--event",
    "events",
    multiple=True,
    type=click.Choice([event.value for event in WebhookEvent]),
    help="Event to subscribe to (repeatable). Default: AGREEMENT_ALL.",
)
@click.option(
    "--scope",
    type=click.Choice([scope.value for scope in WebhookScope]),
    default=WebhookScope.ACCOUNT.value,
    show_default=True,
)
@click.option("--agreement-id", help="Limit to one agreement (sets scope RESOURCE).")
@dry_run_option
@yes_option
def webhooks_create(
    name: str,
    url: str,
    events: tuple[str, ...],
    scope: str,
    agreement_id: str | None,
    dry_run: bool,
    yes: bool,
    output_format: str,
    verbose: bool,
) -> None:
    """Register a webhook for agreement events.

    The URL must answer Acrobat Sign's verification call by echoing the
    X-AdobeSign-ClientId header (see 'webhooks verify').

    \b
    Example:
      adobesign webhooks create --name "Completions" \\
        --url https://app.example.com/adobesign \\
        --event AGREEMENT_WORKFLOW_COMPLETED --dry-run
    """
    try:
        confirm_outward(
            f"Register webhook {name!r} that delivers events to {url}",
            yes=yes,
            dry_run=dry_run,
        )
    except CliError as exc:
        fail(exc)
    webhook = WebhookCreate(
        name=name,
        scope=WebhookScope.RESOURCE.value if agreement_id else scope,
        webhook_subscription_events=list(events) or [WebhookEvent.AGREEMENT_ALL],
        webhook_url_info=WebhookUrlInfo(url=url),
        resource_type=WebhookResourceType.AGREEMENT if agreement_id else None,
        resource_id=agreement_id,
    )
    execute(
        Invocation(output_format, verbose, dry_run),
        lambda c: c.webhooks.create(webhook),
    )


@leaf(webhooks, "list")
@click.option("--limit", type=click.IntRange(min=1), default=100, show_default=True)
@click.option("--include-inactive", is_flag=True, help="Include INACTIVE webhooks.")
def webhooks_list(
    limit: int, include_inactive: bool, output_format: str, verbose: bool
) -> None:
    """List the user's webhooks.

    \b
    Example:
      adobesign webhooks list --format table
    """
    execute(
        Invocation(output_format, verbose),
        lambda c: list(
            islice(c.webhooks.list(show_inactive=include_inactive or None), limit)
        ),
    )


@leaf(webhooks, "delete", emails="no (stops future notifications)")
@click.argument("webhook_id")
@dry_run_option
@yes_option
def webhooks_delete(
    webhook_id: str, dry_run: bool, yes: bool, output_format: str, verbose: bool
) -> None:
    """Delete a webhook; its URL stops receiving notifications.

    \b
    Example:
      adobesign webhooks delete CBJCHBCAABAA... --dry-run
    """
    try:
        confirm_outward(f"Delete webhook {webhook_id}", yes=yes, dry_run=dry_run)
    except CliError as exc:
        fail(exc)

    def action(client: AdobeSignClient) -> dict[str, Any]:
        client.webhooks.delete(webhook_id)
        return {"id": webhook_id, "deleted": True}

    execute(Invocation(output_format, verbose, dry_run), action)


@leaf(webhooks, "verify")
@click.option(
    "--received-client-id",
    required=True,
    help="Value of the X-AdobeSign-ClientId header Acrobat Sign sent.",
)
@click.option(
    "--expected-client-id",
    "expected",
    multiple=True,
    help="Accepted client id (repeatable). Default: ADOBESIGN_CLIENT_ID.",
)
def webhooks_verify(
    received_client_id: str,
    expected: tuple[str, ...],
    output_format: str,
    verbose: bool,
) -> None:
    """Check a webhook call's client id and print the reply Acrobat Sign expects.

    Exits 3 if the client id is not one you expect (the endpoint must then
    NOT answer with a 2xx). Makes no network call.

    \b
    Example:
      adobesign webhooks verify --received-client-id CBJCHBCAABAA...
    """

    def action() -> dict[str, Any]:
        accepted = list(expected) or [_settings().require("client_id")[0]]
        handshake = verify_webhook_request(
            {"X-AdobeSign-ClientId": received_client_id}, accepted
        )
        return {
            "status_code": handshake.status_code,
            "headers": handshake.headers,
            "body": handshake.body,
        }

    run_local(Invocation(output_format, verbose), action)


@leaf(notifications, "parse")
@click.argument("source", type=click.File("rb"))
def notifications_parse(source: Any, output_format: str, verbose: bool) -> None:
    """Parse a webhook notification body (a file, or '-' for stdin).

    Prints the typed notification; unknown fields are kept. Exits 2 when the
    body is not a notification. Makes no network call.

    \b
    Example:
      adobesign notifications parse notification.json
    """
    run_local(
        Invocation(output_format, verbose), lambda: parse_notification(source.read())
    )

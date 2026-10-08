---
name: adobesign
description: Send documents for e-signature and track them in Adobe Acrobat Sign using the unofficial `adobesign` CLI (python-adobesign). Use when asked to send a contract or PDF for signature, check who has or hasn't signed an agreement, chase signers with a reminder, cancel an agreement, download a signed PDF or audit trail, or set up Acrobat Sign webhooks. Not an official Adobe tool.
---

# Acrobat Sign via the `adobesign` CLI

`adobesign` is an unofficial, community-maintained CLI over Adobe's public
Acrobat Sign REST API v6. Run `adobesign <group> <command> --help` for flags,
examples, exit codes, and whether a command emails anyone. Read the help
rather than guessing flags. This file covers judgement, not syntax.

## Setup

- Install: `pip install 'python-adobesign[cli] @ git+https://github.com/BenA-SA/python-adobesign'`.
- Credentials come only from the environment or `~/.config/adobesign/config.toml`
  (mode 0600), never from flags. Either `ADOBESIGN_INTEGRATION_KEY`, or OAuth:
  `ADOBESIGN_CLIENT_ID`, `ADOBESIGN_CLIENT_SECRET`, `ADOBESIGN_REDIRECT_URI`,
  then `adobesign auth login-url`, which the user opens, then
  `adobesign auth exchange - --state …`, which saves tokens to the config file.
- Accounts live on regional shards. Set `ADOBESIGN_BASE_URI` if you know it;
  otherwise it is discovered. `adobesign auth base-uris` both discovers it and
  proves the credentials work. `adobesign auth whoami` shows what is
  configured without making a network call.

## Safety rules (always follow)

1. Read-only commands are fine to run freely: `get`, `list`, `members`,
   `events`, `signing-urls`, `download`, `webhooks list`, `auth whoami` and
   `auth base-uris`.
2. Before `agreements send`, `cancel`, `remind`, `webhooks create` or
   `webhooks delete`:
   run it with `--dry-run`, show the user who will be emailed and what will be
   sent, and get explicit confirmation in the conversation.
3. Only pass `--yes` after that confirmation, for exactly the command that was
   shown. Never add `--yes` pre-emptively or to "unblock" an exit code 2.
4. Never print, echo, log or paste credentials, tokens or the config file
   contents. Don't ask the user to paste secrets into the chat. Tell them to
   set environment variables themselves.
5. Prefer `agreements draft` when the user is unsure; nobody is emailed.

## Recipes

**Send a contract to signers in order and track it to completion**
1. `agreements send --file … --name … --signer "Name <email>" …` with `--dry-run`.
   Signers sign in the order given (use `--order parallel` for all at once).
2. Confirm with the user, then re-run with `--yes`. Keep the returned `id`.
3. Track with `agreements get ID` (`status`). `OUT_FOR_SIGNATURE` means it is
   still in progress, and `SIGNED` means it is complete. Poll gently (minutes,
   not seconds), or use a webhook.

**Who hasn't signed yet?**
`agreements members ID`: look at `nextParticipantSets` (who it is waiting
on) and each participant set's `status`. `agreements events ID` gives the
history (sent, viewed, signed). To chase them, use `agreements remind ID`
(dry-run first, then confirm).

**Download the signed PDF and audit trail**
`agreements download ID --signed -o signed.pdf` and
`agreements download ID --audit-trail -o audit.pdf`. Downloading before
completion gives the unsigned document, so check `status` is `SIGNED` first.

**Set up a webhook**
The URL must already be deployed and echo the `X-AdobeSign-ClientId` header
(`webhooks verify` shows the exact reply). Then `webhooks create` with
`--dry-run`, confirm, and `--yes`. Use `notifications parse FILE` to inspect a
received payload.

## Handling exit codes

Errors are JSON on stderr: `{"error": {"type", "message", "status_code",
"code", "request_id", "retry_after", ...}}`.

- `0`: success, with JSON on stdout.
- `1`: API, server or network error. Report it. Do not blindly retry a `send`.
- `2`: usage or validation, including a missing `--yes`. Fix the input or ask
  the user. Never respond by adding `--yes`.
- `3`: authentication. Check `auth whoami`. For OAuth, run `auth refresh`; if
  that fails, the user must log in again (`auth login-url`).
- `4`: rate-limited. Wait `retry_after` seconds, then retry once.
- `5`: not found. Check the ID. A brand-new agreement can briefly 404 on
  `signing-urls` while it is processing, so wait a few seconds.

## Gotchas

- `agreements send` (`POST /agreements`) is never retried automatically. A
  replay could email the signers twice. After an exit code 1 on send, run
  `agreements list` to see whether it went through before retrying.
- IDs are long opaque strings (`CBJCHBCAABAA…`). Copy them exactly. Never
  construct or shorten them.
- Agreements live on the account's regional shard. A wrong
  `ADOBESIGN_BASE_URI` gives 401/404-style errors, so re-check with
  `auth base-uris`.
- Output keys use Adobe's camelCase field names, so they match the API docs.
- `ADOBESIGN_MAX_RETRIES=0` turns off the CLI's own 429/5xx retries if you
  want to handle them yourself.

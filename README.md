# python-adobesign

> [!IMPORTANT]
> **This is not an official Adobe SDK.** `python-adobesign` is an unofficial,
> community-maintained Python client. It is not affiliated with, endorsed by
> or supported by Adobe. It is built on Adobe's publicly documented
> [Acrobat Sign REST API (v6)](https://secure.adobesign.com/public/docs/restapi/v6)
> (see also the [Acrobat Sign developer documentation](https://opensource.adobe.com/acrobat-sign/)).
> Adobe and Acrobat Sign are trademarks of Adobe Inc.

[![CI](https://github.com/BenA-SA/python-adobesign/actions/workflows/ci.yml/badge.svg)](https://github.com/BenA-SA/python-adobesign/actions/workflows/ci.yml)
![Python 3.10–3.13](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

A typed Python client for the Acrobat Sign REST API v6 signing flow: upload a
document, send it for signature, track it, download the signed PDF, and
receive webhook events.

Adobe does not publish a Python SDK, and its older generated SDKs for other
languages predate REST v6; Adobe recommends calling the REST API directly.
This library does that, with typed models, typed errors and retries.

## Install

Not on PyPI yet. Install from GitHub:

```bash
pip install git+https://github.com/BenA-SA/python-adobesign
# or
uv add git+https://github.com/BenA-SA/python-adobesign
```

Requires Python 3.10+. Dependencies: `httpx` and `pydantic>=2`.

## Quickstart

### Authenticate

With an integration key (simplest; the account's regional access point is
discovered with `GET /baseUris` on first use):

```python
from adobesign import AdobeSignClient, IntegrationKey

client = AdobeSignClient(IntegrationKey("3AAABLblqZh..."))
```

With OAuth 2.0 (authorisation-code flow):

```python
from adobesign import AdobeSignClient, OAuthApp, OAuthCredentials

app = OAuthApp(
    client_id, client_secret, redirect_uri="https://app.example.com/callback"
)

# 1. Send the user to Acrobat Sign to grant access.
url = app.authorization_url(
    [
        "user_login:self",
        "agreement_read:account",
        "agreement_write:account",
        "agreement_send:account",
        "webhook_read:account",
        "webhook_write:account",
    ],
    state=session_state,
)

# 2. On the callback, check the state and swap the code for tokens.
redirect = app.parse_redirect(callback_url, expected_state=session_state)
tokens = app.exchange_code(redirect.code, api_access_point=redirect.api_access_point)
save_somewhere(tokens.model_dump_json())

# 3. Use the tokens. They refresh automatically; persist every refresh.
credentials = OAuthCredentials.from_tokens(
    app, tokens, on_refresh=lambda new: save_somewhere(new.model_dump_json())
)
client = AdobeSignClient(credentials)
```

For a shared store (database, cache) implement the `TokenStore` protocol
(`load()` / `save(tokens)`) and pass it to `OAuthCredentials(app, store)`.

### Send a PDF for signature

```python
from adobesign import AgreementCreate, FileInfo, ParticipantSetInfo, ParticipantRole

document = client.transient_documents.upload("contract.pdf")

created = client.agreements.create(
    AgreementCreate(
        name="Consultancy agreement",
        file_infos=[FileInfo(transient_document_id=document.transient_document_id)],
        participant_sets_info=[
            ParticipantSetInfo.single("alice@example.com", name="Alice", order=1),
            ParticipantSetInfo.single(
                "bob@example.com", order=2, role=ParticipantRole.APPROVER
            ),
        ],
        message="Please review and sign.",
        # state=AgreementCreateState.DRAFT or AUTHORING to send later
    )
)
agreement_id = created.id
```

### Track it

```python
from adobesign import AgreementStatus

agreement = client.agreements.get(agreement_id)
if agreement.status == AgreementStatus.SIGNED:
    ...

members = client.agreements.get_members(agreement_id)  # who has acted, who is next
events = client.agreements.get_events(agreement_id)  # audit events
waiting = [m.id for s in members.participant_sets for m in s.member_infos if m.id]
client.agreements.send_reminder(agreement_id, waiting)

for row in client.agreements.list(page_size=100):  # cursor pagination, lazy
    print(row.id, row.status)
```

Polling works, but webhooks (below) are the better way to learn about
completion.

### Download the signed PDF and audit trail

```python
from pathlib import Path

Path("signed.pdf").write_bytes(
    client.agreements.download_combined_document(agreement_id)
)
Path("audit.pdf").write_bytes(client.agreements.download_audit_trail(agreement_id))
```

Embedded signing: `client.agreements.get_signing_urls(agreement_id)`.
Cancel: `client.agreements.cancel(agreement_id, comment="Superseded")`.

### Webhooks

Register a webhook:

```python
from adobesign import WebhookCreate, WebhookEvent, WebhookScope, WebhookUrlInfo

client.webhooks.create(
    WebhookCreate(
        name="Agreement events",
        scope=WebhookScope.ACCOUNT,
        webhook_subscription_events=[WebhookEvent.AGREEMENT_WORKFLOW_COMPLETED],
        webhook_url_info=WebhookUrlInfo(
            url="https://app.example.com/adobesign/webhook"
        ),
    )
)
```

Acrobat Sign calls the URL with an `X-AdobeSign-ClientId` header (a `GET` to
verify intent, then a `POST` per event) and requires the client id echoed
back. The helpers are framework-agnostic; for example in Django:

```python
from django.http import HttpResponse, HttpResponseForbidden
from adobesign import WebhookClientIdError, parse_notification, verify_webhook_request


def adobesign_webhook(request):
    try:
        handshake = verify_webhook_request(
            request.headers, settings.ADOBESIGN_CLIENT_ID
        )
    except WebhookClientIdError:
        return HttpResponseForbidden()
    if request.method == "POST":
        notification = parse_notification(request.body)
        handle(notification.event, notification.agreement)
    return HttpResponse(
        handshake.body_json, status=handshake.status_code, headers=handshake.headers
    )
```

## Errors and retries

Every exception derives from `AdobeSignError` and carries its values as
attributes (and in `exc.context`, ready for structured logging):

| Exception | When | Useful attributes |
| --- | --- | --- |
| `BadRequestError` | HTTP 400 | `code`, `api_message`, `request_id` |
| `AuthenticationError` / `OAuthError` | HTTP 401 / OAuth error body | `code`, `error`, `error_description` |
| `PermissionDeniedError` | HTTP 403 | `code` |
| `NotFoundError` | HTTP 404 | `code` |
| `ConflictError` | HTTP 409 | `code` |
| `RateLimitedError` | HTTP 429 | `retry_after` |
| `ServerError` | HTTP 5xx | `status_code` |
| `ResponseParseError` | invalid JSON / unexpected shape | `model`, `detail` |
| `TransportError` | no response (timeout, DNS, reset) | `reason` |

`RetryPolicy` (pass `retry=` to the client) retries 429 for any request,
honouring `Retry-After`, and retries 5xx and network failures for idempotent
requests (`GET`/`PUT`/`DELETE`) with exponential backoff and jitter. `POST`
requests are not replayed on 5xx by default, because a replayed
`POST /agreements` could send an agreement twice; opt in with
`RetryPolicy(retry_non_idempotent=True)`.

Models tolerate new fields (kept in `model_extra`) and unknown enum values
(kept as plain strings), so additions on Adobe's side do not break you.

## Scope (v0.1)

v0.1 deliberately covers the **signing flow only**:

- OAuth 2.0 (authorisation URL, code exchange, refresh) and integration keys
- `GET /baseUris`
- `POST /transientDocuments`
- `POST /agreements`, `GET /agreements`, `GET /agreements/{id}`,
  `.../members`, `.../events`, `PUT .../state` (cancel), `POST .../reminders`,
  `.../combinedDocument`, `.../auditTrail`, `.../signingUrls`
- `POST /webhooks`, `GET /webhooks`, `DELETE /webhooks/{id}`, notification
  parsing and the client-id handshake

### Unverified against a live account

The library was written from Adobe's public documentation and is tested
against mocked HTTP only. These details were inferred and still need checking
against a real developer account (reports welcome):

- The default discovery URL `https://api.adobesign.com/api/rest/v6/baseUris`
  and authorise URL `https://secure.adobesign.com/public/oauth/v2` (some
  accounts may need a shard-specific host; both are configurable).
- Whether the token endpoint returns `api_access_point`, and that refresh does
  not rotate the refresh token (a new one is used if returned).
- Which response header, if any, carries a request id (the client checks
  `x-request-id`, `x-adobesign-request-id`, `x-amzn-requestid`).
- That 429 responses carry `Retry-After` in seconds and/or a `retryAfter`
  body field.
- `MemberInfo.name` on agreement creation, and the exact fields of
  `/members`, `/events`, `/signingUrls` and `GET /webhooks` rows.
- The shape of `agreement.participantSetsInfo` inside webhook notifications
  (modelled as the `/members` shape).
- Whether `recipientParticipantIds` on reminders expects member ids (as the
  examples use) or participant-set ids.
- Boolean query parameters are sent as `true` / `false`.

## Contributing — broader coverage welcome

v0.1 deliberately covers the signing flow only. Pull requests adding more of
the API are very welcome: library templates, web forms (widgets), MegaSign,
users and groups, search, form-field data, and so on. An async client is
another good candidate. See [CONTRIBUTING.md](CONTRIBUTING.md): every new
endpoint needs mocked tests and a fixture, and CI enforces that.

## Licence

[MIT](LICENSE) © Ben Atkinson. Not affiliated with Adobe Inc.

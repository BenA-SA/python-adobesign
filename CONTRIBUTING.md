# Contributing

Thanks for helping. v0.1 covers the Acrobat Sign signing flow only, and
pull requests that extend coverage (library templates, web forms, MegaSign,
users/groups, search, an async client...) are welcome.

This project is unofficial and not affiliated with Adobe. Please do not add
Adobe logos or anything that suggests otherwise.

## Development setup

```bash
git clone https://github.com/BenA-SA/python-adobesign
cd python-adobesign
uv sync                       # creates .venv with dev dependencies
uv run pre-commit install
```

Before every push:

```bash
uv run pre-commit run --all-files     # ruff, ruff-format, mypy, basic hooks
uv run pytest --cov --cov-branch --cov-fail-under=95
```

## The rule for new endpoints

**A PR that adds an endpoint must add its mocked tests and a JSON fixture.**
CI enforces it:

1. Decorate the method with `@api_endpoint("METHOD", "/path")`
   (`adobesign/_endpoint.py`).
2. Add a fixture under `tests/fixtures/` modelled on the v6 API reference, and
   register it in `tests/fixture_catalog.py` with its model and the doc
   section it mirrors. `test_fixture_contract.py` parses it strictly: every
   fixture field must be modelled.
3. Write request/response tests marked
   `@pytest.mark.covers("ResourceClass.method")` that assert the exact
   request (method, path, query, `Authorization`, body) and the parsed model.
4. Add an `EndpointCase` to `tests/endpoint_cases.py`. That runs the method
   through the shared error matrix (400/401/403/404/409, 429 with
   `Retry-After`, 5xx retry and exhaustion, network failure, malformed and
   wrong-shape JSON, unexpected fields).
5. Add the endpoint to the live suite in `tests/live/`.

`tests/test_coverage_guard.py` discovers every public method in the package
and fails if any lacks a `covers` marker, if an `@api_endpoint` method is
missing from `ENDPOINT_CASES`, or if one is missing from the live suite.
Branch coverage below 95% also fails CI.

## Code style

- ruff (88 columns, double quotes, single-line imports) and mypy `--strict`.
- Guard clauses over nesting; at most two levels of nesting in a function.
- Docstrings for rationale; `#` comments only for short single-line notes.
- Each failure mode gets its own exception class under `AdobeSignError`,
  with the values that caused it as attributes. Never put a token or secret
  on an exception or in a log.
- Models use snake_case attributes with Adobe's camelCase names as aliases,
  stay tolerant of unknown fields, and type enums as `Open<Enum>`.
- Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).

## Live tests against a developer account

`tests/live/` runs the whole flow (upload, send, status, members, events,
signing URLs, reminder, downloads, webhooks, cancel) against a real account.
It is skipped unless credentials are set. Use a free
[Acrobat Sign developer account](https://www.adobe.com/sign/developer-form.html),
never a production account: it sends a real agreement and then cancels it.

```bash
export ADOBESIGN_INTEGRATION_KEY=...          # or the four OAuth variables below
# export ADOBESIGN_CLIENT_ID=... ADOBESIGN_CLIENT_SECRET=...
# export ADOBESIGN_REFRESH_TOKEN=... ADOBESIGN_API_ACCESS_POINT=https://api.na1.adobesign.com/
export ADOBESIGN_TEST_SIGNER_EMAIL=you+signer@example.com   # not the account's own address
export ADOBESIGN_TEST_WEBHOOK_URL=https://...               # optional; must echo X-AdobeSign-ClientId
uv run pytest tests/live -m live -v -rs
```

In GitHub, add those names as repository secrets and run the **Live tests
(manual)** workflow from the Actions tab (`workflow_dispatch`).

## Releases

Not on PyPI yet. Releases will be published with PyPI trusted publishing
from a tagged GitHub release once the client has been verified against a
live account.

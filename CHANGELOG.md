# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

### Added

- Synchronous `AdobeSignClient` for the Acrobat Sign REST API v6 signing flow.
- Authentication: static integration keys; OAuth 2.0 authorisation URL,
  redirect validation (state check), code exchange and token refresh, with
  automatic refresh and a pluggable `TokenStore`.
- Per-account access-point discovery via `GET /baseUris`.
- Transient document upload from a path, bytes or a file object.
- Agreements: create (IN_PROCESS / DRAFT / AUTHORING), get, list (cursor
  pagination), members, events, cancel, reminders, combined document and
  audit trail downloads, embedded signing URLs.
- Webhooks: create, list, delete; notification parsing; framework-agnostic
  `X-AdobeSign-ClientId` verification handshake.
- Typed Pydantic v2 models and forward-compatible enums.
- Typed exception hierarchy and retries with backoff honouring `Retry-After`.

"""``POST /transientDocuments`` multipart upload.

Modelled on the v6 API reference, "transientDocuments" → "Uploads a document
and obtains the document's ID": a ``multipart/form-data`` body with
``File-Name``, ``Mime-Type`` and ``File`` parts, answered by
``{"transientDocumentId": "..."}``.
"""

from __future__ import annotations

import io
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path

import httpx
import pytest
import respx

from adobesign import AdobeSignClient

from endpoint_cases import PDF_BYTES
from helpers import API
from helpers import FIXTURES
from helpers import assert_bearer
from helpers import json_response

UPLOAD_URL = f"{API}/transientDocuments"
TRANSIENT_ID = "3AAABLblqZhCtpwYb9nPjD0Xu6K1mHkOeQ8r2sTzWvYx-example"


def multipart_parts(request: httpx.Request) -> dict[str, tuple[str | None, bytes]]:
    """Map each form part name to its (filename, payload)."""
    raw = (
        f"Content-Type: {request.headers['Content-Type']}\r\n\r\n".encode()
        + request.content
    )
    message = BytesParser(policy=HTTP).parsebytes(raw)
    parts: dict[str, tuple[str | None, bytes]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        assert isinstance(name, str)
        assert isinstance(payload, bytes)
        parts[name] = (part.get_filename(), payload)
    return parts


@pytest.fixture
def upload_route(mock_api: respx.MockRouter) -> respx.Route:
    return mock_api.post(UPLOAD_URL).mock(
        return_value=json_response("transient_document.json", status_code=201)
    )


@pytest.mark.covers("TransientDocumentsResource.upload")
def test_upload_from_path_uses_file_name(
    client: AdobeSignClient, upload_route: respx.Route
) -> None:
    document = client.transient_documents.upload(FIXTURES / "sample.pdf")

    assert document.transient_document_id == TRANSIENT_ID
    request = upload_route.calls.last.request
    assert_bearer(request)
    assert request.headers["Content-Type"].startswith("multipart/form-data")
    parts = multipart_parts(request)
    assert parts["File-Name"] == (None, b"sample.pdf")
    assert parts["Mime-Type"] == (None, b"application/pdf")
    assert parts["File"] == ("sample.pdf", PDF_BYTES)


@pytest.mark.covers("TransientDocumentsResource.upload")
def test_upload_from_str_path(
    client: AdobeSignClient, upload_route: respx.Route
) -> None:
    client.transient_documents.upload(str(FIXTURES / "sample.pdf"))

    assert multipart_parts(upload_route.calls.last.request)["File"][0] == "sample.pdf"


@pytest.mark.covers("TransientDocumentsResource.upload")
def test_upload_from_bytes_defaults_name(
    client: AdobeSignClient, upload_route: respx.Route
) -> None:
    client.transient_documents.upload(PDF_BYTES)

    parts = multipart_parts(upload_route.calls.last.request)
    assert parts["File-Name"] == (None, b"document.pdf")
    assert parts["File"] == ("document.pdf", PDF_BYTES)


@pytest.mark.covers("TransientDocumentsResource.upload")
def test_upload_from_file_object_uses_its_name(
    client: AdobeSignClient, upload_route: respx.Route, tmp_path: Path
) -> None:
    path = tmp_path / "contract.pdf"
    path.write_bytes(PDF_BYTES)

    with path.open("rb") as handle:
        client.transient_documents.upload(handle)

    assert multipart_parts(upload_route.calls.last.request)["File"] == (
        "contract.pdf",
        PDF_BYTES,
    )


@pytest.mark.covers("TransientDocumentsResource.upload")
def test_upload_from_nameless_stream_with_overrides(
    client: AdobeSignClient, upload_route: respx.Route
) -> None:
    client.transient_documents.upload(
        io.BytesIO(b"hello"), file_name="notes.txt", mime_type="text/plain"
    )

    parts = multipart_parts(upload_route.calls.last.request)
    assert parts["File-Name"] == (None, b"notes.txt")
    assert parts["Mime-Type"] == (None, b"text/plain")
    assert parts["File"] == ("notes.txt", b"hello")


@pytest.mark.covers("TransientDocumentsResource.upload")
def test_upload_from_nameless_stream_defaults_name(
    client: AdobeSignClient, upload_route: respx.Route
) -> None:
    client.transient_documents.upload(io.BytesIO(PDF_BYTES))

    assert multipart_parts(upload_route.calls.last.request)["File"][0] == (
        "document.pdf"
    )


@pytest.mark.covers("TransientDocumentsResource.upload")
def test_upload_retries_429_with_the_same_body(
    client: AdobeSignClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(UPLOAD_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            json_response("transient_document.json"),
        ]
    )

    client.transient_documents.upload(io.BytesIO(PDF_BYTES))

    first, second = (call.request for call in route.calls)
    assert multipart_parts(first)["File"] == multipart_parts(second)["File"]

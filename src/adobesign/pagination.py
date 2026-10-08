"""Lazy iteration over Acrobat Sign's cursor-paginated list endpoints.

List endpoints such as ``GET /agreements`` and ``GET /webhooks`` return one
page of results plus ``page.nextCursor``; the next page is fetched by passing
that value back as the ``cursor`` query parameter. The last page has no
``nextCursor``.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from typing import Generic
from typing import TypeVar

from adobesign.models import Page

ItemT = TypeVar("ItemT")

FetchPage = Callable[[str | None], Page[ItemT]]


class Paginator(Generic[ItemT]):
    """Iterate items across every page, fetching each page only when needed.

    Iterating the paginator yields items; :meth:`pages` yields whole pages.
    Nothing is requested until iteration starts, and each iteration starts
    again from the first page.
    """

    def __init__(self, fetch_page: FetchPage[ItemT]) -> None:
        self._fetch_page = fetch_page

    def pages(self) -> Iterator[Page[ItemT]]:
        """Yield each page in order until the API stops returning a cursor."""
        cursor: str | None = None
        while True:
            page = self._fetch_page(cursor)
            yield page
            if not page.next_cursor:
                return
            cursor = page.next_cursor

    def __iter__(self) -> Iterator[ItemT]:
        for page in self.pages():
            yield from page.items

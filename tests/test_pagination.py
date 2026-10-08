"""The generic cursor paginator (``page.nextCursor`` → ``cursor``)."""

from __future__ import annotations

import pytest

from adobesign.models import Page
from adobesign.pagination import Paginator


class FakePages:
    """Serves canned pages keyed by cursor and records requested cursors."""

    def __init__(self, pages: dict[str | None, Page[int]]) -> None:
        self.pages = pages
        self.requested: list[str | None] = []

    def __call__(self, cursor: str | None) -> Page[int]:
        self.requested.append(cursor)
        return self.pages[cursor]


THREE_PAGES = {
    None: Page[int](items=[1, 2], next_cursor="c2"),
    "c2": Page[int](items=[], next_cursor="c3"),
    "c3": Page[int](items=[3], next_cursor=None),
}


@pytest.mark.covers("Paginator.pages")
def test_pages_follow_cursors_until_none() -> None:
    fetch = FakePages(THREE_PAGES)

    pages = list(Paginator(fetch).pages())

    assert [page.items for page in pages] == [[1, 2], [], [3]]
    assert fetch.requested == [None, "c2", "c3"]


@pytest.mark.covers("Paginator.pages")
def test_empty_cursor_string_ends_iteration() -> None:
    fetch = FakePages({None: Page[int](items=[1], next_cursor="")})

    assert list(Paginator(fetch)) == [1]
    assert fetch.requested == [None]


@pytest.mark.covers("Paginator.pages")
def test_iteration_is_lazy_and_restartable() -> None:
    fetch = FakePages(THREE_PAGES)
    paginator = Paginator(fetch)

    assert fetch.requested == []
    assert next(iter(paginator)) == 1
    assert fetch.requested == [None]
    assert list(paginator) == [1, 2, 3]
    assert list(paginator) == [1, 2, 3]

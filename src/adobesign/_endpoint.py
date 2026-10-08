"""Marks client methods that call an Acrobat Sign HTTP endpoint.

The marker documents which REST route a method wraps and lets the test suite
enumerate every HTTP-calling method, so a method added without mocked tests
fails the endpoint-coverage guard.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from typing import TypeVar

ENDPOINT_ATTRIBUTE = "__adobesign_endpoint__"

FuncT = TypeVar("FuncT", bound=Callable[..., Any])


def api_endpoint(method: str, path: str) -> Callable[[FuncT], FuncT]:
    """Record the ``(method, path)`` REST route the decorated method calls."""

    def decorate(func: FuncT) -> FuncT:
        setattr(func, ENDPOINT_ATTRIBUTE, (method, path))
        return func

    return decorate

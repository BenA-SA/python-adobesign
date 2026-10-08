"""Endpoint-coverage guard: no public client method may ship without tests.

Two rules, both enforced here:

1. Every public method/property of the client-facing classes, and every
   public helper function, has at least one test marked
   ``@pytest.mark.covers("<Class>.<name>")`` (or ``covers("<function>")``).
   Classes and functions are discovered automatically from the package
   modules, so a new resource class is guarded without editing this file.
   Markers are found by statically scanning ``tests/test_*.py``, so the check
   does not depend on which tests happen to be selected.
2. Every method decorated with ``@api_endpoint`` (i.e. one that makes an
   HTTP call) is registered in ``endpoint_cases.ENDPOINT_CASES`` and so runs
   through the full error matrix.

Adding an endpoint without its mocked tests therefore fails CI.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

from adobesign import auth
from adobesign import client
from adobesign import models
from adobesign import notifications
from adobesign import pagination
from adobesign._endpoint import ENDPOINT_ATTRIBUTE
from adobesign._transport import RetryPolicy

from endpoint_cases import ENDPOINT_CASES

TESTS_DIR = Path(__file__).parent
LIVE_TESTS_DIR = TESTS_DIR / "live"
LIVE_EXEMPT = {
    "OAuthApp.exchange_code": "needs a browser consent to mint an auth code",
}

SCANNED_MODULES = [auth, client, models, notifications, pagination]


def _defined_here(module: ModuleType) -> list[object]:
    return [
        value
        for name, value in vars(module).items()
        if not name.startswith("_")
        and getattr(value, "__module__", None) == module.__name__
    ]


def _is_concrete_class(value: object) -> bool:
    return inspect.isclass(value) and not getattr(value, "_is_protocol", False)


PUBLIC_CLASSES: list[type] = [
    value
    for module in SCANNED_MODULES
    for value in _defined_here(module)
    if _is_concrete_class(value)
] + [RetryPolicy]

PUBLIC_FUNCTIONS = [
    value
    for module in SCANNED_MODULES
    for value in _defined_here(module)
    if inspect.isfunction(value)
]


def _is_public_member(name: str, member: object) -> bool:
    if name.startswith("_"):
        return False
    if isinstance(member, (property, classmethod, staticmethod)):
        return True
    return inspect.isfunction(member)


def public_targets() -> set[str]:
    """``Class.member`` for every public member defined on each class."""
    targets = {
        f"{cls.__name__}.{name}"
        for cls in PUBLIC_CLASSES
        for name, member in vars(cls).items()
        if _is_public_member(name, member)
    }
    return targets | {function.__name__ for function in PUBLIC_FUNCTIONS}  # type: ignore[attr-defined]


def endpoint_targets() -> set[str]:
    """``Class.method`` for every method marked ``@api_endpoint``."""
    return {
        f"{cls.__name__}.{name}"
        for cls in PUBLIC_CLASSES
        for name, member in vars(cls).items()
        if hasattr(member, ENDPOINT_ATTRIBUTE)
    }


def _covers_argument(node: ast.Call) -> str | None:
    """The string passed to ``pytest.mark.covers(...)``, if ``node`` is one."""
    func = node.func
    is_covers = isinstance(func, ast.Attribute) and func.attr == "covers"
    if not is_covers or not node.args:
        return None
    argument = node.args[0]
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None


def covered_targets(directory: Path = TESTS_DIR) -> set[str]:
    """Every target named by a ``covers`` marker in ``directory``'s tests."""
    covered: set[str] = set()
    for path in directory.glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            target = _covers_argument(node) if isinstance(node, ast.Call) else None
            if target:
                covered.add(target)
    return covered


def test_public_surface_is_discovered() -> None:
    targets = public_targets()

    assert "AgreementsResource.get" in targets
    assert "WebhooksResource.delete" in targets
    assert "OAuthApp.authorization_url" in targets
    assert "parse_notification" in targets


def test_every_public_method_has_a_covering_test() -> None:
    missing = sorted(public_targets() - covered_targets())

    assert missing == [], (
        "Public methods without a @pytest.mark.covers(...) test: " + ", ".join(missing)
    )


def test_covers_markers_name_real_targets() -> None:
    unknown = sorted(covered_targets() - public_targets())

    assert unknown == [], f"covers() markers naming unknown targets: {unknown}"


def test_every_http_endpoint_runs_through_the_error_matrix() -> None:
    registered = {case.target for case in ENDPOINT_CASES}

    assert endpoint_targets() == registered


def test_every_http_endpoint_is_marked() -> None:
    for target in endpoint_targets():
        class_name, method_name = target.split(".")
        cls = next(c for c in PUBLIC_CLASSES if c.__name__ == class_name)
        method, path = getattr(vars(cls)[method_name], ENDPOINT_ATTRIBUTE)
        assert method in {"GET", "POST", "PUT", "DELETE"}
        assert path.startswith("/")


def test_live_suite_exercises_every_http_endpoint() -> None:
    registered = {case.target for case in ENDPOINT_CASES}
    missing = sorted(registered - covered_targets(LIVE_TESTS_DIR) - set(LIVE_EXEMPT))

    assert missing == [], f"Endpoints missing from tests/live: {missing}"

"""Contract test: every fixture field is modelled, with no silent extras.

Runtime models keep unknown fields (``extra="allow"``) so new Adobe fields
never break an integration. Here the same parse is checked strictly: any
field that lands in ``model_extra`` anywhere in the tree fails the test, so a
fixture and its model cannot drift apart unnoticed.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from fixture_catalog import FIXTURE_CATALOG
from fixture_catalog import FixtureSpec
from helpers import FIXTURES
from helpers import load_fixture


def unmodelled_fields(value: Any, path: str = "$") -> list[str]:
    """Paths of every field that parsed into ``model_extra``, recursively."""
    if isinstance(value, list):
        return [
            found
            for index, item in enumerate(value)
            for found in unmodelled_fields(item, f"{path}[{index}]")
        ]
    if not isinstance(value, BaseModel):
        return []
    found = [f"{path}.{name}" for name in (value.model_extra or {})]
    for name in type(value).model_fields:
        found.extend(unmodelled_fields(getattr(value, name), f"{path}.{name}"))
    return found


@pytest.mark.parametrize(
    ("fixture_name", "spec"), FIXTURE_CATALOG.items(), ids=list(FIXTURE_CATALOG)
)
def test_fixture_parses_strictly_into_its_model(
    fixture_name: str, spec: FixtureSpec
) -> None:
    payload = load_fixture(fixture_name)

    parsed = spec.model.model_validate(payload)

    assert unmodelled_fields(parsed) == []
    assert parsed.model_dump(by_alias=True, exclude_unset=True, mode="json") == payload


def test_every_json_fixture_is_catalogued() -> None:
    on_disk = {path.name for path in FIXTURES.glob("*.json")}

    assert on_disk == set(FIXTURE_CATALOG)


def test_strict_check_detects_drift() -> None:
    drifted = FIXTURE_CATALOG["agreement.json"].model.model_validate(
        {**load_fixture("agreement.json"), "renamedField": 1}
    )

    assert unmodelled_fields(drifted) == ["$.renamedField"]

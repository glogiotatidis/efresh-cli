"""Live checks against the real site. Run with `pytest -m live`."""
from __future__ import annotations

import pytest

from efresh_cli import api

pytestmark = pytest.mark.live


def test_public_search_returns_sanitas() -> None:
    client = api.Client(timeout=20)
    data = client.search("sanitas")
    assert data["total"] >= 1
    assert any("Sanitas" in item["name"] or "sanitas" in item["name"].lower()
               for item in data["results"])
    for item in data["results"]:
        assert item["id"].isdigit()
        assert item["price"] >= 0


def test_public_search_pagination() -> None:
    client = api.Client(timeout=20)
    first = client.search("γαλα", page=1)
    second = client.search("γαλα", page=2)
    assert first["page"] == 1
    assert second["page"] == 2
    assert first["total"] > 0

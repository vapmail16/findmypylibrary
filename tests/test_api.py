"""The public Python API: `findmypylibrary.search` and the exceptions it raises."""

from __future__ import annotations

import pytest
from helpers import make_pkg

import findmypylibrary
from findmypylibrary import cache


def test_search_returns_public_fields_only() -> None:
    cache.save_packages([make_pkg("openpyxl", "read/write Excel xlsx files", 339_316_525)])

    ((package, score),) = findmypylibrary.search("excel", top_n=5)

    assert set(package) == {
        "name",
        "summary",
        "keywords",
        "topics",
        "homepage",
        "version",
        "last_release",
        "downloads_30d",
    }
    assert package["downloads_30d"] == 339_316_525
    assert 0.0 <= score <= 1.0


def test_search_without_a_snapshot_raises_the_exported_snapshot_error() -> None:
    with pytest.raises(findmypylibrary.SnapshotError, match="refresh"):
        findmypylibrary.search("excel")


@pytest.mark.parametrize("bad", [0, -1])
def test_top_n_must_be_positive(bad: int) -> None:
    cache.save_packages([make_pkg("openpyxl", "read/write Excel xlsx files")])
    with pytest.raises(ValueError, match="top_n"):
        findmypylibrary.search("excel", top_n=bad)


def test_exceptions_are_exported() -> None:
    assert issubclass(findmypylibrary.SnapshotError, Exception)
    assert issubclass(findmypylibrary.FetchError, Exception)

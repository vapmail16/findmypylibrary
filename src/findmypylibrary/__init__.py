"""findmypylibrary — describe a use case in plain English, get a ranked shortlist
of real PyPI packages.

    import findmypylibrary
    try:
        for package, score in findmypylibrary.search("parse pdf files", top_n=5):
            print(package["name"], package["downloads_30d"], score)
    except findmypylibrary.SnapshotError as exc:   # no snapshot yet: run `findmypylibrary refresh`
        print(exc)
"""

from __future__ import annotations

__version__ = "0.2.0"

# Imported after __version__ on purpose: fetch.py reads it for its User-Agent.
from .errors import FetchError, SnapshotError  # noqa: E402
from .rank import search  # noqa: E402

__all__ = ["FetchError", "SnapshotError", "__version__", "search"]
